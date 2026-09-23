"""Private original-ticket evidence custody and separately selected effects resolution."""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, Literal

from chiplog.capabilities.effects.contracts import CommandIdentity, ExactHead
from chiplog.capabilities.effects.dispatch_authority_contracts import DispatchObservationDTO
from chiplog.capabilities.effects.dispatch_outcome_contracts import (
    DispatchEvidenceV2,
    DispatchOutcomeCommandV2,
    DispatchOutcomePreparationV2,
    DispatchOutcomeRecordV2,
)
from chiplog.capabilities.effects.dispatch_outcomes import prepare_outcome
from chiplog.capabilities.effects.dispatch_v2 import canonical, digest, reference
from chiplog.composition.r16_denial_inputs import require_invocation
from chiplog.composition.r16_dispatch_history import verify_dispatch_history
from chiplog.composition.r16_dispatch_outbox import _request, _send, validate_consumptions
from chiplog.composition.r16_dispatch_publication import authenticate, owner_call
from chiplog.composition.r16_effects import MaterializedEffectsCut, read_materialized_effects
from chiplog.composition.r16_effects_authority import effect_materialization_state
from chiplog.composition.r16_effects_inputs import canonical as retained_canonical
from chiplog.platform._owner_publication_contracts import (
    AuthoritativeReadManifest,
    BrokerPublicationResult,
    ExactReplayQuery,
    InvocationProofRef,
    JournalSelectedPublication,
    OwnerCommandBytes,
    OwnerRecordBytes,
    PublicationIdentity,
    PublicationRejected,
    RegisteredPublication,
    SingleOwnerBatch,
    WorkerAuthentication,
)
from chiplog.platform.owner_publications import (
    BrokerPublicationCoordinator,
    PreparedOwnerPublication,
    SelectedOwnerDecision,
)

if TYPE_CHECKING:
    from chiplog.adapters.driven.effects_hermetic import IssuedEffectSendTicket
    from chiplog.composition.r7_planning import ObservedTrustCall
    from chiplog.composition.r16_dispatch_runtime import R16DispatchRuntime

OUTCOME_SCHEMA = "chiplog.effects.dispatch-outcome-record.v2"
REGISTRY = reference(
    "effects.outcome-interpreter.v2", b"original-child-evidence;separate-resolver;no-send"
)


class OutcomeIssuance(DispatchObservationDTO):
    schema_id: Literal["chiplog.effects.dispatch-outcome-issuance.v2"]
    request: DispatchOutcomePreparationV2
    record: DispatchOutcomeRecordV2
    signature: str


def _sealed(request: DispatchOutcomePreparationV2, record: DispatchOutcomeRecordV2) -> bytes:
    return canonical([request.model_dump(mode="json"), record.model_dump(mode="json")])


def _boundary(request: DispatchOutcomePreparationV2) -> bool:
    return (
        request.command.operation == "APPEND_EVIDENCE"
        and request.command.evidence is not None
        and request.command.evidence.observation == "BOUNDARY_CROSSED"
    )


def _evidence(
    runtime: R16DispatchRuntime,
    ticket: IssuedEffectSendTicket,
    observation: Literal[
        "PROVIDER_RECEIPT", "BOUNDARY_CROSSED", "TIMEOUT", "ABSENCE_OBSERVED", "MALFORMED_RESPONSE"
    ],
    raw: bytes,
) -> DispatchEvidenceV2:
    positive: tuple[str, ...] = ()
    negative: tuple[str, ...] = ()
    source = ticket.provider + "/" + ticket.account + "/" + ticket.credential_binding_head
    if observation == "PROVIDER_RECEIPT":
        authenticated = runtime._require_dispatch_resources().verify_receipt(ticket, raw)
        positive, negative = (
            authenticated.occurred_members,
            authenticated.permanently_incapable_members,
        )
        evidence_id = ticket.transmission_id + "/receipt/" + str(authenticated.sequence)
    else:
        evidence_id = ticket.transmission_id + "/" + observation + "/" + digest(raw)
    return DispatchEvidenceV2(
        evidence_id=evidence_id,
        source_identity=source,
        raw_bytes=raw,
        raw_digest=digest(raw),
        observation=observation,
        transmission=ExactHead(
            subject_id=ticket.transmission_id,
            head=ticket.transmission_id + "/" + ticket.transmission_fingerprint,
            fingerprint=ticket.transmission_fingerprint,
        ),
        occurred_members=positive,
        permanently_incapable_members=negative,
    )


def validate_outcome_issuance(
    batch: RegisteredPublication, runtime: R16DispatchRuntime
) -> OutcomeIssuance:
    authentication = batch.authentication
    if (
        not isinstance(batch, SingleOwnerBatch)
        or not isinstance(authentication, WorkerAuthentication)
        or authentication.applicability_schema != "chiplog.effects.dispatch-outcome-issuance.v2"
        or digest(authentication.applicability_bytes) != authentication.applicability_fingerprint
    ):
        raise ValueError("outcome lacks registered retained issuance")
    value = OutcomeIssuance.model_validate_json(authentication.applicability_bytes)
    request, record = value.request, value.record
    resources = runtime._require_dispatch_resources()
    if (
        value.canonical_bytes() != authentication.applicability_bytes
        or not resources.verify_outcome(_sealed(request, record), value.signature)
        or prepare_outcome(request) != record
        or batch.command.canonical_bytes != request.canonical_bytes()
        or batch.command.schema_id != request.schema_id
        or batch.command.owner != "effects"
        or batch.command.fingerprint != digest(request.canonical_bytes())
        or batch.identity.command_id != request.command.identity.command_id
        or batch.identity.command_fingerprint != digest(request.canonical_bytes())
        or batch.identity.tenant_id != request.original_send.snapshot.intent.mandate.tenant_id
        or batch.expected.registry_head != REGISTRY.head
        or batch.expected.registry_fingerprint != REGISTRY.fingerprint
        or request.command.identity.expected_tenant_head != batch.expected.tenant_frontier
        or len(batch.complete_records) != 1
    ):
        raise ValueError("outcome owner output, signature, identity or registry differs")
    expected_row = _row(record)
    if batch.complete_records != (expected_row,) or batch.complete_batch_fingerprint != digest(
        retained_canonical((expected_row,))
    ):
        raise ValueError("outcome selected owner bytes differ")
    source = next(
        (
            decision
            for decision in runtime._owner_decisions().snapshot().decisions
            if any(
                row.owner == "effects"
                and row.canonical_bytes == request.original_send.canonical_bytes()
                for row in decision.prepared.request.complete_records
            )
        ),
        None,
    )
    if source is None or source.tenant_commit_sequence > batch.expected.tenant_frontier:
        raise ValueError("outcome original SEND selection absent")
    consumed, ticket = _request(source, request.original_send)
    command = request.command
    if command.operation == "APPEND_EVIDENCE":
        evidence = command.evidence
        if evidence is None or evidence != _evidence(
            runtime, ticket, evidence.observation, evidence.raw_bytes
        ):
            raise ValueError("outcome evidence not derived from original source receipt")
        if evidence.observation != "BOUNDARY_CROSSED":
            consumption = runtime._owner_decisions().lookup(runtime._tenant_id, consumed.command_id)
            if (
                consumption is None
                or consumption.tenant_commit_sequence > batch.expected.tenant_frontier
            ):
                raise ValueError("provider evidence has no prior consumed transmission")
        if batch.operation != "effects.record_evidence":
            raise ValueError("outcome evidence operation substituted")
    elif (
        batch.operation != "effects.reconcile"
        or command.resolver is None
        or command.resolver.subject_id != request.original_send.snapshot.intent.mandate.principal_id
    ):
        raise ValueError("outcome resolver binding differs")
    return value


def _row(record: DispatchOutcomeRecordV2) -> OwnerRecordBytes:
    return OwnerRecordBytes(
        owner="effects",
        record_kind="effects." + record.kind,
        record_id=record.record.head,
        schema_id=record.schema_id,
        canonical_bytes=record.canonical_bytes(),
        fingerprint=digest(record.canonical_bytes()),
    )


def same_resolver_authority(before: ObservedTrustCall, after: ObservedTrustCall) -> bool:
    """Only the observation deadline/request nonce may refresh; authority cannot."""
    return (
        before.observation == after.observation
        and before.result == after.result
        and before.request.operation_id == after.request.operation_id
        and before.request.schema_id == after.request.schema_id
        and before.request.canonical_payload == after.request.canonical_payload
        and before.request.caller == after.request.caller
        and before.request.callee == after.request.callee
        and before.request.budget.model_dump(exclude={"absolute_deadline_ns"})
        == after.request.budget.model_dump(exclude={"absolute_deadline_ns"})
    )


class OutcomeAuthority:
    def __init__(
        self,
        runtime: R16DispatchRuntime,
        observed: ObservedTrustCall | None,
        cut: MaterializedEffectsCut,
    ) -> None:
        self.runtime, self.observed, self.cut = runtime, observed, cut
        self.owner_session = runtime._supervisor.runtime().session("effects")
        self.prepared: PreparedOwnerPublication | None = None
        self.proof: InvocationProofRef | None = None

    def invocation(self, identity: PublicationIdentity) -> InvocationProofRef:
        if self.observed is not None:
            require_invocation(self.runtime, self.observed)
        session = self.runtime._supervisor.runtime().session("effects")
        self.proof = InvocationProofRef(
            issuance_id=secrets.token_hex(24),
            issuance_fingerprint=digest(retained_canonical((identity, session))),
            broker_epoch=str(session.broker_epoch),
            broker_session="broker:" + session.generation_id,
            runtime_generation=session.generation_id,
            operation_subject=identity.command_id,
        )
        return self.proof

    def authenticate_replay(self, query: ExactReplayQuery) -> PublicationRejected | None:
        expected = self.prepared.request if self.prepared is not None else None
        if (
            not isinstance(expected, SingleOwnerBatch)
            or query.current_invocation is not self.proof
            or query.identity != expected.identity
            or query.operation != expected.operation
            or query.original_commands != (expected.command,)
            or query.current_invocation.operation_subject != query.identity.command_id
        ):
            return PublicationRejected(
                kind="DENIED",
                tenant_id=query.identity.tenant_id,
                command_id=query.identity.command_id,
                reason="unissued outcome invocation or query",
            )
        if self.observed is not None:
            require_invocation(self.runtime, self.observed)
        return None

    async def prepare(
        self, request: RegisteredPublication
    ) -> PreparedOwnerPublication | PublicationRejected:
        if self.prepared is not None and self.prepared.request is request:
            return self.prepared
        return PublicationRejected(
            kind="DENIED",
            tenant_id=request.identity.tenant_id,
            command_id=request.identity.command_id,
            reason="unissued outcome request",
        )

    def check_prepared(
        self, prepared: PreparedOwnerPublication
    ) -> Literal["DENIED", "STALE", "INDETERMINATE"] | None:
        if prepared is not self.prepared:
            return "DENIED"
        try:
            resources = self.runtime._require_dispatch_resources()
            issuance = validate_outcome_issuance(prepared.request, self.runtime)
            if not _boundary(issuance.request):
                resources.require_original_provider()
            if self.runtime._supervisor.runtime().session("effects") != self.owner_session:
                return "STALE"
            if self.observed is not None:
                require_invocation(self.runtime, self.observed)
            validate_consumptions(self.runtime)
            current = read_materialized_effects(self.runtime, self.runtime._owner_decisions())
            if current != self.cut:
                return "STALE"
            resources = self.runtime._require_dispatch_resources()
            if not _boundary(issuance.request):
                resources.require_original_provider()
            if self.observed is not None:
                require_invocation(self.runtime, self.observed)
            return None
        except ValueError, RuntimeError, OSError, KeyError:
            return "INDETERMINATE"

    def materialization_state(
        self, decision: SelectedOwnerDecision
    ) -> Literal["ABSENT", "COMPLETE", "CONFLICT"]:
        validate_outcome_issuance(decision.prepared.request, self.runtime)
        return effect_materialization_state(self.runtime, decision)

    def check_selected_predecessor(self, decision: SelectedOwnerDecision) -> bool:
        return self.materialization_state(decision) == "ABSENT"


async def publish_outcome(
    runtime: R16DispatchRuntime,
    intent_id: str,
    *,
    evidence: DispatchEvidenceV2 | None,
    observed: ObservedTrustCall | None = None,
    act_id: str | None = None,
) -> BrokerPublicationResult:
    with runtime._authority_gate().hold():
        runtime._require_no_pending()
        if observed is not None:
            require_invocation(runtime, observed)
        command_id = (
            "effect-evidence/" + evidence.evidence_id
            if evidence is not None
            else "effect-resolve/" + str(act_id)
        )
        existing = runtime._owner_decisions().lookup(runtime._tenant_id, command_id)
        if existing is not None:
            retained = validate_outcome_issuance(existing.prepared.request, runtime)
            if (
                retained.request.command.evidence != evidence
                or retained.record.snapshot.intent.intent_id != intent_id
            ):
                raise ValueError("changed replay of original outcome command")
            if evidence is None and observed is None:
                raise ValueError("resolver invocation missing")
            if effect_materialization_state(runtime, existing) != "COMPLETE":
                raise ValueError("outcome exact replay lacks complete physical materialization")
            if observed is not None:
                # Release-time current caller, after retained and physical validation.
                require_invocation(runtime, observed)
            return JournalSelectedPublication(
                kind="EXACT_REPLAY",
                tenant_id=runtime._tenant_id,
                command_id=command_id,
                decision_id=existing.decision_id,
                decision_fingerprint=existing.decision_fingerprint,
                decision_head=existing.decision_head,
                predecessor_commitment=existing.prepared.predecessor_commitment,
                resulting_commitment=existing.resulting_commitment,
                tenant_commit_sequence=existing.tenant_commit_sequence,
                complete_records=existing.prepared.request.complete_records,
            )
        validate_consumptions(runtime)
        _, send = _send(runtime, intent_id)
        cut = read_materialized_effects(runtime, runtime._owner_decisions())
        history = verify_dispatch_history(cut, runtime._owner_decisions().snapshot())
        latest = next(
            record
            for record in reversed(history.v2_records)
            if record.snapshot.intent.intent_id == intent_id
        )
        resolver = None
        if evidence is None:
            if observed is None or act_id is None:
                raise ValueError("resolver requires authenticated explicit command")
            raw_principal = require_invocation(runtime, observed)
            resolver = reference(send.snapshot.intent.mandate.principal_id, raw_principal)
        command = DispatchOutcomeCommandV2(
            schema_id="chiplog.effects.dispatch-outcome-command.v2",
            identity=CommandIdentity(
                command_id=command_id,
                fingerprint=digest(command_id.encode()),
                expected_tenant_head=cut.tenant_frontier,
            ),
            operation="APPEND_EVIDENCE" if evidence is not None else "RESOLVE_OBLIGATION",
            intent=reference(intent_id, send.snapshot.intent.canonical_bytes()),
            original_send=send.record,
            expected_attempt=latest.snapshot.attempt,
            complete_children=tuple(item.transmission for item in send.snapshot.transmissions),
            prior_evidence=latest.snapshot.evidence_heads
            if isinstance(latest, DispatchOutcomeRecordV2)
            else (),
            evidence=evidence,
            resolver=resolver,
        )
        request = DispatchOutcomePreparationV2(
            schema_id="chiplog.effects.dispatch-outcome-preparation.v2",
            original_send=send,
            previous=latest,
            command=command,
        )
        authority = OutcomeAuthority(runtime, observed, cut)
    _, raw = await owner_call(
        runtime, "effects.prepare_dispatch_outcome_v2", request.schema_id, request.canonical_bytes()
    )
    record = DispatchOutcomeRecordV2.model_validate_json(raw)
    if observed is not None:
        refreshed = await authenticate(runtime, "hermetic-ingress")
        if not same_resolver_authority(observed, refreshed):
            raise ValueError("resolver authority changed during owner preparation")
        authority.observed = refreshed
    with runtime._authority_gate().hold():
        resources = runtime._require_dispatch_resources()
        issuance = OutcomeIssuance(
            schema_id="chiplog.effects.dispatch-outcome-issuance.v2",
            request=request,
            record=record,
            signature=resources.seal_boundary_outcome(request, record)
            if _boundary(request)
            else resources.seal_outcome(_sealed(request, record)),
        )
        identity = PublicationIdentity(
            tenant_id=runtime._tenant_id,
            command_id=command_id,
            command_fingerprint=digest(request.canonical_bytes()),
            canonicalization_version="chiplog.owner-publication.v1",
        )
        row = _row(record)
        batch = SingleOwnerBatch(
            operation="effects.record_evidence" if evidence is not None else "effects.reconcile",
            identity=identity,
            authentication=WorkerAuthentication(
                invocation=authority.invocation(identity),
                applicability_schema=issuance.schema_id,
                applicability_bytes=issuance.canonical_bytes(),
                applicability_fingerprint=digest(issuance.canonical_bytes()),
            ),
            expected=AuthoritativeReadManifest(
                tenant_id=runtime._tenant_id,
                tenant_frontier=cut.tenant_frontier,
                expected_materialization_commitment=cut.materialization_commitment,
                registry_head=REGISTRY.head,
                registry_fingerprint=REGISTRY.fingerprint,
                ordered_heads=(),
                complete_manifest_fingerprint=digest(retained_canonical(cut)),
            ),
            command=OwnerCommandBytes(
                owner="effects",
                schema_id=request.schema_id,
                canonical_bytes=request.canonical_bytes(),
                fingerprint=digest(request.canonical_bytes()),
            ),
            complete_records=(row,),
            complete_batch_fingerprint=digest(retained_canonical((row,))),
        )
        validate_outcome_issuance(batch, runtime)
        authority.prepared = PreparedOwnerPublication(
            batch, secrets.token_hex(24), "r6", 0, cut.materialization_commitment
        )
    return await BrokerPublicationCoordinator(
        runtime._appender, authority, runtime._owner_decisions()
    ).commit(batch)


async def observe_outcome(
    runtime: R16DispatchRuntime, intent_id: str, raw: bytes | None = None, *, boundary: bool = False
) -> BrokerPublicationResult:
    with runtime._authority_gate().hold():
        decision, send = _send(runtime, intent_id)
        _, ticket = _request(decision, send)
        if boundary:
            evidence = _evidence(
                runtime,
                ticket,
                "BOUNDARY_CROSSED",
                b"selected SEND boundary crossed; emission unknown",
            )
        elif raw is None:
            raw = (
                runtime._require_dispatch_resources()
                .require_original_provider()
                .reconcile(ticket.transmission_id)
            )
            evidence = (
                _evidence(
                    runtime,
                    ticket,
                    "ABSENCE_OBSERVED",
                    b"provider read has no retained observation",
                )
                if raw is None
                else _evidence(runtime, ticket, "PROVIDER_RECEIPT", raw)
            )
        else:
            evidence = _evidence(runtime, ticket, "PROVIDER_RECEIPT", raw)
    return await publish_outcome(runtime, intent_id, evidence=evidence)


async def resolve_outcome(
    runtime: R16DispatchRuntime, peer: str, intent_id: str, act_id: str
) -> BrokerPublicationResult:
    observed = await authenticate(runtime, peer)
    return await publish_outcome(
        runtime, intent_id, evidence=None, observed=observed, act_id=act_id
    )
