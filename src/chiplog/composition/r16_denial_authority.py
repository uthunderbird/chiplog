"""Private invocation-local issuance for the registered denying operation."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.composition.r7_planning import ObservedTrustCall
from chiplog.composition.r16_denial_history import (
    retained_ingress,
    validate_denial_batch,
    validate_selected_denials,
)
from chiplog.composition.r16_denial_inputs import (
    DenialCapture,
    build_denial_request,
    capture_denial,
    require_invocation,
)
from chiplog.composition.r16_effects_authority import effect_materialization_state
from chiplog.composition.r16_effects_inputs import canonical, digest, effect_snapshot
from chiplog.platform._owner_publication_contracts import (
    ExactReplayQuery,
    InvocationProofRef,
    OwnerCommandBytes,
    PublicationIdentity,
    PublicationRejected,
    RegisteredPublication,
    SingleOwnerBatch,
)
from chiplog.platform.broker import PublicPortCall, PublicPortSuccess
from chiplog.platform.owner_publications import PreparedOwnerPublication, SelectedOwnerDecision

if TYPE_CHECKING:
    from chiplog.composition.r14_runtime import R14PlanningRuntime


@dataclass(frozen=True)
class _Invocation:
    proof: InvocationProofRef
    identity: PublicationIdentity
    command: OwnerCommandBytes
    observed: ObservedTrustCall


@dataclass(frozen=True)
class _Issued:
    prepared: PreparedOwnerPublication
    captured: DenialCapture
    invocation: _Invocation
    sent: PublicPortCall
    returned: PublicPortSuccess


class DenialAuthority:
    def __init__(self, runtime: R14PlanningRuntime) -> None:
        self._runtime = runtime
        self._invocations: dict[str, _Invocation] = {}
        self._issued: dict[str, _Issued] = {}

    def invocation(
        self, identity: PublicationIdentity, command: OwnerCommandBytes, observed: ObservedTrustCall
    ) -> InvocationProofRef:
        with self._runtime._authority_gate().hold():
            require_invocation(self._runtime, observed)
            if identity.tenant_id != self._runtime._tenant_id:
                raise LoopRejected("foreign denying invocation")
            caller = observed.request.caller
            nonce = secrets.token_hex(32)
            proof = InvocationProofRef(
                issuance_id=nonce,
                issuance_fingerprint=digest(canonical((nonce, identity, command, observed))),
                broker_epoch=str(caller.broker_epoch),
                broker_session=caller.session_id,
                runtime_generation=caller.generation_id,
                operation_subject=identity.command_id,
            )
            self._invocations[nonce] = _Invocation(proof, identity, command, observed)
            return proof

    def authenticate_replay(self, query: ExactReplayQuery) -> PublicationRejected | None:
        with self._runtime._authority_gate().hold():
            entry = self._invocations.get(query.current_invocation.issuance_id)
            if (
                entry is None
                or entry.proof != query.current_invocation
                or entry.identity != query.identity
                or query.original_commands != (entry.command,)
                or query.operation != "effects.before_send"
                or self._runtime._trust_observation_guard(entry.observed) is not None
            ):
                return PublicationRejected(
                    kind="DENIED",
                    tenant_id=query.identity.tenant_id,
                    command_id=query.identity.command_id,
                    reason="no currently issued denying invocation",
                )
        return None

    def register(
        self,
        batch: SingleOwnerBatch,
        captured: DenialCapture,
        sent: PublicPortCall,
        returned: PublicPortSuccess,
    ) -> None:
        with self._runtime._authority_gate().hold():
            invocation = self._invocations.get(batch.authentication.invocation.issuance_id)
            if (
                invocation is None
                or batch.authentication.invocation != invocation.proof
                or invocation.identity != batch.identity
                or invocation.command != batch.command
            ):
                raise LoopRejected("unissued denying publication")
            request = validate_denial_batch(batch, effect_snapshot(captured.cut).records)
            if (
                request.current.observed_time_ns > time.monotonic_ns()
                or build_denial_request(
                    retained_ingress(request),
                    captured,
                    invocation.observed,
                    observed_time_ns=request.current.observed_time_ns,
                )
                != request
            ):
                raise LoopRejected("denying request differs from actual captured authority sources")
            if (
                sent.canonical_payload != batch.command.canonical_bytes
                or sent.operation_id != "effects.prepare_denial"
                or sent.schema_id != "chiplog.effects.before-send-preparation.v2"
                or sent.callee != captured.sessions[0]
                or sent.budget.absolute_deadline_ns != request.command.authority.valid_until_ns
                or returned.request_id != sent.request_id
                or returned.responder != sent.callee
                or returned.schema_id != "chiplog.effects.before-send-prepared-publication.v2"
            ):
                raise LoopRejected("denying owner response differs from retained invocation")
            from chiplog.capabilities.effects.denial import prepare_denial

            if returned.canonical_payload != prepare_denial(request).canonical_bytes():
                raise LoopRejected("denying owner substituted exact output")
            prepared = PreparedOwnerPublication(
                batch, secrets.token_hex(32), "r6", 0, captured.cut.materialization_commitment
            )
            self._issued[prepared.issuance_id] = _Issued(
                prepared, captured, invocation, sent, returned
            )
            if self.check_prepared(prepared) is not None:
                del self._issued[prepared.issuance_id]
                raise LoopRejected("denying sources changed before issuance")

    async def prepare(
        self, request: RegisteredPublication
    ) -> PreparedOwnerPublication | PublicationRejected:
        matches = tuple(
            entry.prepared for entry in self._issued.values() if entry.prepared.request is request
        )
        if len(matches) == 1:
            return matches[0]
        return PublicationRejected(
            kind="DENIED",
            tenant_id=request.identity.tenant_id,
            command_id=request.identity.command_id,
            reason="unissued denying object",
        )

    def check_prepared(
        self, prepared: PreparedOwnerPublication
    ) -> Literal["DENIED", "STALE", "INDETERMINATE"] | None:
        with self._runtime._authority_gate().hold():
            entry = self._issued.get(prepared.issuance_id)
            if entry is None or entry.prepared is not prepared:
                return "DENIED"
            if time.monotonic_ns() >= entry.sent.budget.absolute_deadline_ns:
                return "STALE"
            worker = entry.captured.cut.worker
            if worker is None:
                return "DENIED"
            try:
                if (
                    capture_denial(self._runtime, entry.invocation.observed, worker.run.run_id)
                    != entry.captured
                ):
                    return "STALE"
            except LoopRejected, ValueError, RuntimeError:
                return "STALE"
            return None

    def materialization_state(
        self, decision: SelectedOwnerDecision
    ) -> Literal["ABSENT", "COMPLETE", "CONFLICT"]:
        with self._runtime._authority_gate().hold():
            validate_selected_denials(self._runtime._owner_decisions().snapshot())
            return effect_materialization_state(self._runtime, decision)

    def check_selected_predecessor(self, decision: SelectedOwnerDecision) -> bool:
        return self.materialization_state(decision) == "ABSENT"
