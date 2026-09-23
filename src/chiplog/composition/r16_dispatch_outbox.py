"""Closed broker-owned one-shot consumption; recovery never recreates a send permit."""

from __future__ import annotations

import secrets
from dataclasses import asdict
from typing import TYPE_CHECKING, Final, Literal

from chiplog.adapters.driven.effects_hermetic import IssuedEffectSendTicket
from chiplog.capabilities.effects.contracts import ExactHead
from chiplog.capabilities.effects.dispatch_authority_contracts import DispatchObservationDTO
from chiplog.capabilities.effects.dispatch_v2 import DispatchRecordV2, canonical, digest, reference
from chiplog.composition.r16_denial_inputs import require_invocation
from chiplog.composition.r16_dispatch_authority import validate_issuance
from chiplog.composition.r16_dispatch_history import RECORD_SCHEMA, verify_dispatch_history
from chiplog.composition.r16_dispatch_publication import authenticate
from chiplog.composition.r16_effects import read_materialized_effects
from chiplog.composition.r16_effects_authority import effect_materialization_state
from chiplog.composition.r16_effects_inputs import canonical as retained_canonical
from chiplog.platform._owner_publication_contracts import (
    AuthoritativeReadManifest,
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
    from chiplog.composition.r7_planning import ObservedTrustCall
    from chiplog.composition.r16_dispatch_runtime import R16DispatchRuntime

CONSUMPTION_SCHEMA: Final = "chiplog.broker-dispatch.send-consumption.v1"
CONSUME_SCHEMA: Final = "chiplog.broker-dispatch.consume-effect-send.v1"
REGISTRY = reference("broker-dispatch.registry.v1", b"exact-selected-child-once;replay-never-emits")


class ConsumeRequest(DispatchObservationDTO):
    schema_id: Literal["chiplog.broker-dispatch.consume-effect-send.v1"]
    tenant_id: str
    command_id: str
    selected_send: ExactHead
    send_record: ExactHead
    intent: ExactHead
    transmission: ExactHead
    ticket_bytes: bytes
    ticket_digest: str
    registry: ExactHead


class ConsumptionRecord(DispatchObservationDTO):
    schema_id: Literal["chiplog.broker-dispatch.send-consumption.v1"]
    state: Literal["CONSUMED"]
    request: ConsumeRequest


def _send(
    runtime: R16DispatchRuntime, intent_id: str
) -> tuple[SelectedOwnerDecision, DispatchRecordV2]:
    journal = runtime._owner_decisions().snapshot()
    cut = read_materialized_effects(runtime, runtime._owner_decisions())
    history = verify_dispatch_history(cut, journal)
    selected = tuple(
        row
        for row in history.v2_records
        if row.snapshot.intent.intent_id == intent_id
        and isinstance(row, DispatchRecordV2)
        and row.kind == "SEND_COMMITTED"
    )
    if (
        not selected
        or selected[-1].snapshot.state != "SEND_COMMITTED"
        or len(selected[-1].snapshot.transmissions) != 1
    ):
        raise ValueError("consumption needs exact retained first SEND child")
    record = selected[-1]
    decision = next(
        decision
        for decision in journal.decisions
        if any(
            member.record_id == record.record.head
            for member in decision.prepared.request.complete_records
        )
    )
    validate_issuance(decision.prepared.request, runtime)
    return decision, record


def _request(
    decision: SelectedOwnerDecision, record: DispatchRecordV2
) -> tuple[ConsumeRequest, IssuedEffectSendTicket]:
    intent = record.snapshot.intent
    child = record.snapshot.transmissions[0]
    recipient = intent.mandate.recipient
    command_id = "consume-send/" + digest(
        canonical([intent.mandate.tenant_id, child.transmission.model_dump(mode="json")])
    )
    ticket = IssuedEffectSendTicket(
        tenant_id=intent.mandate.tenant_id,
        issued_operation_id=command_id,
        journal_decision_id=decision.decision_id,
        journal_decision_fingerprint=decision.decision_fingerprint,
        transmission_id=child.transmission.subject_id,
        transmission_fingerprint=child.transmission.fingerprint,
        request_ordinal=child.ordinal,
        intent_id=intent.intent_id,
        intent_fingerprint=intent.fingerprint,
        provider=recipient.provider,
        account=recipient.account,
        recipient=recipient.recipient,
        canonical_address=recipient.canonical_address,
        endpoint_head=recipient.endpoint.head,
        credential_binding_head=recipient.credential_binding.head,
        adapter_contract_version=intent.mandate.semantics.adapter_contract_version,
        payload=intent.mandate.payload,
        payload_fingerprint=intent.mandate.effect_fingerprint,
        idempotency_key=intent.mandate.idempotency_fence_key,
        bundle_members=tuple(member.subject_id for member in intent.mandate.bundle_members),
    )
    raw = retained_canonical(asdict(ticket))
    return ConsumeRequest(
        schema_id=CONSUME_SCHEMA,
        tenant_id=intent.mandate.tenant_id,
        command_id=command_id,
        selected_send=ExactHead(
            subject_id=decision.decision_id,
            head=decision.decision_head,
            fingerprint=decision.decision_fingerprint,
        ),
        send_record=record.record,
        intent=reference(intent.intent_id, intent.canonical_bytes()),
        transmission=child.transmission,
        ticket_bytes=raw,
        ticket_digest=digest(raw),
        registry=REGISTRY,
    ), ticket


def validate_consumptions(runtime: R16DispatchRuntime) -> None:
    history = runtime._owner_decisions().snapshot()
    sends: dict[str, tuple[SelectedOwnerDecision, DispatchRecordV2]] = {}
    consumed: set[str] = set()
    for decision in history.decisions:
        batch = decision.prepared.request
        for row in batch.complete_records:
            if row.owner == "effects" and row.schema_id == RECORD_SCHEMA:
                record = DispatchRecordV2.model_validate_json(row.canonical_bytes)
                if record.kind == "SEND_COMMITTED":
                    sends[record.snapshot.transmissions[0].transmission.subject_id] = (
                        decision,
                        record,
                    )
        signals = batch.operation == "dispatch.consume_effect_send" or any(
            row.owner == "broker_dispatch" or row.schema_id == CONSUMPTION_SCHEMA
            for row in batch.complete_records
        )
        if not signals:
            continue
        if (
            not isinstance(batch, SingleOwnerBatch)
            or batch.operation != "dispatch.consume_effect_send"
            or len(batch.complete_records) != 1
        ):
            raise ValueError("unregistered broker dispatch consumption envelope")
        request = ConsumeRequest.model_validate_json(batch.command.canonical_bytes)
        source = sends.get(request.transmission.subject_id)
        if source is None or request.transmission.subject_id in consumed:
            raise ValueError("consumption has missing selected SEND or duplicate child")
        expected, _ = _request(*source)
        consumption = ConsumptionRecord(
            schema_id=CONSUMPTION_SCHEMA, state="CONSUMED", request=expected
        )
        row = batch.complete_records[0]
        if (
            request != expected
            or request.canonical_bytes() != batch.command.canonical_bytes
            or batch.command.owner != "broker_dispatch"
            or batch.command.schema_id != CONSUME_SCHEMA
            or batch.command.fingerprint != digest(request.canonical_bytes())
            or batch.identity.command_id != request.command_id
            or batch.identity.command_fingerprint != digest(request.canonical_bytes())
            or (
                row.owner,
                row.record_kind,
                row.schema_id,
                row.record_id,
                row.canonical_bytes,
                row.fingerprint,
            )
            != (
                "broker_dispatch",
                "send-consumption",
                CONSUMPTION_SCHEMA,
                request.command_id,
                consumption.canonical_bytes(),
                digest(consumption.canonical_bytes()),
            )
        ):
            raise ValueError("consumption substitutes exact selected child/ticket/record")
        consumed.add(request.transmission.subject_id)


class _ConsumeAuthority:
    def __init__(self, runtime: R16DispatchRuntime, observed: ObservedTrustCall) -> None:
        self.runtime, self.observed = runtime, observed
        self.prepared: PreparedOwnerPublication | None = None
        self.proof: InvocationProofRef | None = None
        self.subject: ConsumeRequest | None = None
        self.cut: object = None

    def invocation(self, identity: PublicationIdentity) -> InvocationProofRef:
        require_invocation(self.runtime, self.observed)
        session = self.observed.request.caller
        self.proof = InvocationProofRef(
            issuance_id=secrets.token_hex(24),
            issuance_fingerprint=digest(retained_canonical((identity, self.observed))),
            broker_epoch=str(session.broker_epoch),
            broker_session=session.session_id,
            runtime_generation=session.generation_id,
            operation_subject=identity.command_id,
        )
        return self.proof

    def authenticate_replay(self, query: ExactReplayQuery) -> PublicationRejected | None:
        require_invocation(self.runtime, self.observed)
        if (
            self.proof is not query.current_invocation
            or self.proof.operation_subject != query.identity.command_id
        ):
            return PublicationRejected(
                kind="DENIED",
                tenant_id=query.identity.tenant_id,
                command_id=query.identity.command_id,
                reason="unissued consumption invocation",
            )
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
            reason="unissued consumption request",
        )

    def check_prepared(
        self, prepared: PreparedOwnerPublication
    ) -> Literal["DENIED", "STALE", "INDETERMINATE"] | None:
        if prepared is not self.prepared or self.subject is None:
            return "DENIED"
        try:
            require_invocation(self.runtime, self.observed)
            validate_consumptions(self.runtime)
            current = read_materialized_effects(self.runtime, self.runtime._owner_decisions())
            expected, _ = _request(*_send(self.runtime, self.subject.intent.subject_id))
            return None if current == self.cut and expected == self.subject else "STALE"
        except ValueError, RuntimeError, OSError, KeyError:
            return "INDETERMINATE"

    def materialization_state(
        self, decision: SelectedOwnerDecision
    ) -> Literal["ABSENT", "COMPLETE", "CONFLICT"]:
        validate_consumptions(self.runtime)
        return effect_materialization_state(self.runtime, decision)

    def check_selected_predecessor(self, decision: SelectedOwnerDecision) -> bool:
        return self.materialization_state(decision) == "ABSENT"


async def consume_and_emit(runtime: R16DispatchRuntime, peer: str, intent_id: str) -> bytes | None:
    observed = await authenticate(runtime, peer)
    from chiplog.composition.r16_dispatch_outcomes import observe_outcome

    # The original OPEN obligation is selected before any consumption or provider I/O.
    opening = await observe_outcome(runtime, intent_id, boundary=True)
    if not isinstance(opening, JournalSelectedPublication):
        return None
    observed = await authenticate(runtime, peer)
    authority = _ConsumeAuthority(runtime, observed)
    coordinator = BrokerPublicationCoordinator(
        runtime._appender, authority, runtime._owner_decisions()
    )
    with runtime._authority_gate().hold():
        runtime._require_no_pending()
        validate_consumptions(runtime)
        request, ticket = _request(*_send(runtime, intent_id))
        existing = runtime._owner_decisions().lookup(runtime._tenant_id, request.command_id)
        if existing is not None:
            # This API never returns an executable permit for a historical selection.
            validate_consumptions(runtime)
            return None
        cut = read_materialized_effects(runtime, runtime._owner_decisions())
        identity = PublicationIdentity(
            tenant_id=runtime._tenant_id,
            command_id=request.command_id,
            command_fingerprint=digest(request.canonical_bytes()),
            canonicalization_version="chiplog.owner-publication.v1",
        )
        record = ConsumptionRecord(schema_id=CONSUMPTION_SCHEMA, state="CONSUMED", request=request)
        row = OwnerRecordBytes(
            owner="broker_dispatch",
            record_kind="send-consumption",
            record_id=request.command_id,
            schema_id=CONSUMPTION_SCHEMA,
            canonical_bytes=record.canonical_bytes(),
            fingerprint=digest(record.canonical_bytes()),
        )
        batch = SingleOwnerBatch(
            operation="dispatch.consume_effect_send",
            identity=identity,
            authentication=WorkerAuthentication(
                invocation=authority.invocation(identity),
                applicability_schema="chiplog.broker-dispatch.consumption-issuer.v1",
                applicability_bytes=retained_canonical(observed),
                applicability_fingerprint=digest(retained_canonical(observed)),
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
                owner="broker_dispatch",
                schema_id=CONSUME_SCHEMA,
                canonical_bytes=request.canonical_bytes(),
                fingerprint=digest(request.canonical_bytes()),
            ),
            complete_records=(row,),
            complete_batch_fingerprint=digest(retained_canonical((row,))),
        )
        authority.subject, authority.cut = request, cut
        authority.prepared = PreparedOwnerPublication(
            batch, secrets.token_hex(24), "r6", 0, cut.materialization_commitment
        )
    result = await coordinator.commit(batch)
    if not isinstance(result, JournalSelectedPublication) or result.kind != "COMMITTED":
        return None
    # Only this fresh, winning invocation creates a private permit. No caller DTO,
    # replay, startup materialization or lost acknowledgment can reach this point.
    permit = object()
    runtime._dispatch_permits[id(permit)] = (permit, ticket)
    return await _emit(runtime, permit)


async def _emit(runtime: R16DispatchRuntime, permit: object) -> bytes:
    with runtime._authority_gate().hold():
        stored = runtime._dispatch_permits.pop(id(permit), None)
        if stored is None or stored[0] is not permit:
            raise ValueError("unissued or already consumed transport permit")
        ticket = stored[1]
        validate_consumptions(runtime)
    # Resolve both independently held identities immediately before invocation;
    # no await or mutable alias reread can redirect this locally held leaf.
    # This does not renew authorization or require ACTIVE after selected SEND.
    provider = runtime._require_dispatch_resources().require_original_provider()
    # Permit removed before awaiting external I/O; no SQL transaction is held.
    raw = await provider.emit_issued(ticket)
    from chiplog.composition.r16_dispatch_outcomes import observe_outcome

    selected = await observe_outcome(runtime, ticket.intent_id, raw)
    if not isinstance(selected, JournalSelectedPublication):
        raise ValueError("provider receipt not durably selected; original obligation remains OPEN")
    return raw
