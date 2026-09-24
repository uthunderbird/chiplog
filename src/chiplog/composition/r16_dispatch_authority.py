"""Private issuance, writer recapture and retained original source interpretation."""

from __future__ import annotations

import base64
import json
import secrets
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.effects.dispatch_authority_contracts import DispatchObservationDTO
from chiplog.capabilities.effects.dispatch_v2 import (
    DispatchPreparationV2,
    DispatchRecordV2,
    digest,
    prepare_dispatch,
)
from chiplog.capabilities.effects.dispatch_v2_contracts import (
    CommitFirstSendV2,
    PublishDispatchIntentV2,
)
from chiplog.composition.r7_planning import ObservedTrustCall
from chiplog.composition.r16_denial_inputs import _retained_trust_snapshot, require_invocation
from chiplog.composition.r16_dispatch_inputs import (
    DispatchCapture,
    capture_dispatch,
    current_inputs,
    require_dispatch_scope,
)
from chiplog.composition.r16_effects_authority import effect_materialization_state
from chiplog.composition.r16_effects_inputs import canonical
from chiplog.platform._owner_publication_contracts import (
    ExactReplayQuery,
    InvocationProofRef,
    PublicationIdentity,
    PublicationRejected,
    RegisteredPublication,
    WorkerAuthentication,
)
from chiplog.platform.broker import PublicPortCall, PublicPortSuccess
from chiplog.platform.owner_publications import PreparedOwnerPublication, SelectedOwnerDecision
from chiplog.platform.r7_trust import TrustOwnerCall

if TYPE_CHECKING:
    from chiplog.composition.r16_dispatch_runtime import R16DispatchRuntime


class DispatchIssuance(DispatchObservationDTO):
    schema_id: Literal["chiplog.effects.dispatch-issuance.v2"]
    captured: DispatchCapture
    observed: ObservedTrustCall
    sent: PublicPortCall
    request: DispatchPreparationV2
    record: DispatchRecordV2


def validate_issuance(
    batch: RegisteredPublication, runtime: R16DispatchRuntime
) -> DispatchIssuance:
    auth = batch.authentication
    if not isinstance(auth, WorkerAuthentication) or (
        auth.applicability_schema != "chiplog.effects.dispatch-issuance.v2"
        or digest(auth.applicability_bytes) != auth.applicability_fingerprint
    ):
        raise ValueError("dispatch selection lacks exact retained issuance")
    value = DispatchIssuance.model_validate_json(auth.applicability_bytes)
    if value.canonical_bytes() != auth.applicability_bytes:
        raise ValueError("noncanonical retained dispatch issuance")
    return validate_dispatch_issuance_value(value, batch, runtime)


def validate_dispatch_issuance_value(
    value: DispatchIssuance, batch: RegisteredPublication, runtime: R16DispatchRuntime
) -> DispatchIssuance:
    """Interpret an already decoded original exchange without requiring a live epoch."""
    captured, observed, sent, request = value.captured, value.observed, value.sent, value.request
    worker = captured.cut.worker
    if worker is None or len(captured.sessions) != 4:
        raise ValueError("retained dispatch worker/session inventory missing")
    payload = json.loads(observed.request.canonical_payload)
    trust_call = TrustOwnerCall(
        mode=payload["mode"],
        snapshot_bytes=base64.b64decode(payload["snapshot_bytes"], validate=True),
        request_bytes=base64.b64decode(payload["request_bytes"], validate=True),
    )
    trust = _retained_trust_snapshot(trust_call.snapshot_bytes)
    credential = trust["credential"]
    principal = json.loads(captured.principal)
    credentials = json.loads(trust_call.request_bytes)
    expected_reference = {
        "contour": "CLI",
        "credential_head": credential["head"],
        "freshness_sequence": trust["freshness"],
        "materialization_head": trust["materialization_head"],
        "peer_credential": credential["peer_credential"],
        "principal_id": trust["principal_id"],
        "session_head": credential["session_head"],
        "source_head": "local",
        "tenant_id": trust["tenant_id"],
        "trust_head": trust["trust_head"],
    }
    if (
        trust["phase"] != "ACTIVE"
        or credential.get("revoked")
        or principal != expected_reference
        or credentials["contour"] != "CLI"
        or any(
            credentials[key] != credential[key]
            for key in ("credential_id", "session_id", "peer_credential")
        )
        or principal["tenant_id"] != batch.identity.tenant_id
        or principal["principal_id"] != "hermetic-principal"
        or observed.result.reference_bytes != captured.principal
        or observed.result.disposition != "VALID"
        or observed.request.operation_id != "deployment_trust.authenticate"
        or observed.request.schema_id != "chiplog.deployment-trust.owner-call.v1"
        or observed.request.callee != captured.sessions[3]
        or trust_call.mode != "AUTHENTICATE"
        or trust_call.canonical_bytes() != observed.request.canonical_payload
        or trust_call.snapshot_bytes != observed.observation.snapshot_bytes
        or not isinstance(observed.response, PublicPortSuccess)
        or observed.response.request_id != observed.request.request_id
        or observed.response.responder != observed.request.callee
        or observed.response.canonical_payload != observed.result.canonical_bytes()
        or not runtime._dispatch_resources.verify_historical(captured.resources)
        or sent.callee != captured.sessions[0]
        or sent.operation_id != "effects.prepare_dispatch_v2"
        or sent.schema_id != request.schema_id
        or sent.canonical_payload != request.canonical_bytes()
        or sent.budget.absolute_deadline_ns <= request.current.observed_time_ns
        or request.command.fence != worker.fence
        or worker.owner_session != captured.sessions[2]
        or worker.run.state != "ACTIVE"
    ):
        raise ValueError("retained dispatch trust/resource/worker/IPC provenance differs")
    intent = (
        request.command.intent
        if isinstance(request.command, PublishDispatchIntentV2)
        else (None if request.previous is None else request.previous.snapshot.intent)
    )
    if intent is None:
        raise ValueError("dispatch issuance lacks original intent")
    if (
        current_inputs(
            captured,
            request.command.canonical_bytes(),
            intent,
            original_adoption_bytes=intent.acquisition.adoption.canonical_bytes(),
        )
        != request.current
    ):
        raise ValueError("retained source interpretation differs from original current inputs")
    effects = tuple(row for row in batch.complete_records if row.owner == "effects")
    if (
        prepare_dispatch(request) != value.record
        or len(effects) != 1
        or effects[0].canonical_bytes != value.record.canonical_bytes()
        or effects[0].record_id != value.record.record.head
        or effects[0].fingerprint != digest(value.record.canonical_bytes())
        or captured.cut.materialization_commitment
        != batch.expected.expected_materialization_commitment
        or captured.cut.tenant_frontier != batch.expected.tenant_frontier
    ):
        raise ValueError("retained dispatch owner output or original cut differs")
    return value


@dataclass(frozen=True)
class _Prepared:
    prepared: PreparedOwnerPublication
    issuance: DispatchIssuance


class DispatchPublicationAuthority:
    def __init__(self, runtime: R16DispatchRuntime, observed: ObservedTrustCall) -> None:
        self.runtime, self.observed = runtime, observed
        self.invocations: dict[str, tuple[InvocationProofRef, PublicationIdentity]] = {}
        self.captures: list[DispatchCapture] = []
        self.preparations: list[_Prepared] = []

    def capture(self, run_id: str, *, intent_id: str | None = None) -> DispatchCapture:
        captured = capture_dispatch(
            self.runtime,
            self.runtime._dispatch_resources,
            self.observed,
            run_id,
            intent_id=intent_id,
        )
        self.captures.append(captured)
        return captured

    def invocation(self, identity: PublicationIdentity) -> InvocationProofRef:
        require_invocation(self.runtime, self.observed)
        session, nonce = self.observed.request.caller, secrets.token_hex(24)
        proof = InvocationProofRef(
            issuance_id=nonce,
            issuance_fingerprint=digest(canonical((identity, self.observed, nonce))),
            broker_epoch=str(session.broker_epoch),
            broker_session=session.session_id,
            runtime_generation=session.generation_id,
            operation_subject=identity.command_id,
        )
        self.invocations[nonce] = (proof, identity)
        return proof

    def authenticate_replay(self, query: ExactReplayQuery) -> PublicationRejected | None:
        entry = self.invocations.get(query.current_invocation.issuance_id)
        try:
            require_invocation(self.runtime, self.observed)
            if (
                entry is None
                or entry[0] is not query.current_invocation
                or entry[1] != query.identity
            ):
                raise ValueError("unissued dispatch invocation")
        except (ValueError, RuntimeError) as error:
            return PublicationRejected(
                kind="DENIED",
                tenant_id=query.identity.tenant_id,
                command_id=query.identity.command_id,
                reason=str(error),
            )
        return None

    def register(self, batch: RegisteredPublication, issuance: DispatchIssuance) -> None:
        if not any(captured is issuance.captured for captured in self.captures):
            raise LoopRejected("dispatch capture was not issued by this authority")
        if validate_issuance(batch, self.runtime) != issuance:
            raise LoopRejected("dispatch selected input differs from issued observation")
        prepared = PreparedOwnerPublication(
            batch, secrets.token_hex(24), "r6", 0, issuance.captured.cut.materialization_commitment
        )
        self.preparations.append(_Prepared(prepared, issuance))

    async def prepare(
        self, request: RegisteredPublication
    ) -> PreparedOwnerPublication | PublicationRejected:
        for entry in self.preparations:
            if entry.prepared.request is request:
                return entry.prepared
        return PublicationRejected(
            kind="DENIED",
            tenant_id=request.identity.tenant_id,
            command_id=request.identity.command_id,
            reason="unissued dispatch publication",
        )

    def check_prepared(
        self, prepared: PreparedOwnerPublication
    ) -> Literal["DENIED", "STALE", "INDETERMINATE"] | None:
        for entry in self.preparations:
            if entry.prepared is not prepared:
                continue
            value = entry.issuance
            try:
                worker = value.captured.cut.worker
                if worker is None:
                    return "DENIED"
                intent = (
                    value.request.command.intent
                    if isinstance(value.request.command, PublishDispatchIntentV2)
                    else (
                        None
                        if value.request.previous is None
                        else value.request.previous.snapshot.intent
                    )
                )
                if intent is None:
                    return "DENIED"
                fresh = capture_dispatch(
                    self.runtime,
                    self.runtime._dispatch_resources,
                    value.observed,
                    worker.run.run_id,
                    intent_id=None if value.request.previous is None else intent.intent_id,
                )
                require_dispatch_scope(
                    fresh,
                    self.runtime._dispatch_resources,
                    intent.mandate,
                    None if value.request.previous is None else intent,
                    first_send=isinstance(value.request.command, CommitFirstSendV2),
                )
                if fresh != value.captured or fresh.observed_time_ns >= min(
                    value.sent.budget.absolute_deadline_ns,
                    value.request.current.lease_expires_at_ns,
                ):
                    return "STALE"
                if time.monotonic_ns() >= min(
                    value.sent.budget.absolute_deadline_ns,
                    value.request.current.lease_expires_at_ns,
                ):
                    return "STALE"
                return None
            except ValueError, RuntimeError, OSError, KeyError, TypeError:
                return "INDETERMINATE"
        return "DENIED"

    def materialization_state(
        self, decision: SelectedOwnerDecision
    ) -> Literal["ABSENT", "COMPLETE", "CONFLICT"]:
        validate_issuance(decision.prepared.request, self.runtime)
        return effect_materialization_state(self.runtime, decision)

    def check_selected_predecessor(self, decision: SelectedOwnerDecision) -> bool:
        return self.materialization_state(decision) == "ABSENT"
