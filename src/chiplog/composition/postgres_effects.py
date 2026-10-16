"""Native original effect custody, authenticated observations and current recovery.

The registered hermetic provider is an actual durable transport. No SQLite proof
snapshot or caller-supplied terminal flag participates in this path.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Literal, TypedDict, cast

from chiplog.adapters.driven.effects_hermetic import (
    HermeticEffectsProvider,
    HermeticResponseLost,
    IssuedEffectSendTicket,
    verify_hermetic_receipt,
)
from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.contracts import LoopHold, LoopRejected
from chiplog.capabilities.agent_loop.execution_client_contracts import NativeCallStateV1
from chiplog.capabilities.agent_loop.execution_client_transitions import advance, headed
from chiplog.capabilities.agent_loop.execution_contracts import ConsequentialToolCall
from chiplog.capabilities.agent_loop.recovery_contracts import (
    Absent,
    OriginalObligationBinding,
    Present,
)
from chiplog.capabilities.effects.client_effect_contracts import (
    NativeEffectClaimV1,
    NativeEffectClosureV1,
    NativeEffectDTO,
    NativeEffectEvidenceV1,
    NativeEffectIntentV1,
    NativeEffectInvocationV1,
    NativeEffectPreviewV1,
    NativeEffectReductionV1,
    NativeEffectSendV1,
)
from chiplog.capabilities.effects.contracts import ExactHead, ProviderRecipient, TransmissionAttempt
from chiplog.capabilities.effects.domain import ChildMemberEvidence, reduce_child_evidence
from chiplog.composition.r16_dispatch_registry import SEMANTICS
from chiplog.platform.deployment_gate import DeploymentGateRequest
from chiplog.platform.postgres import PostgresIntegrityError, Record, Snapshot
from chiplog.platform.postgres_gate import PostgresDeploymentGate, canonical

if TYPE_CHECKING:
    from chiplog.composition.postgres_execution import PostgresExecutionRuntime


type NativeOutcome = Literal[
    "OUTCOME_UNKNOWN", "PARTIAL", "CONFIRMED", "FAILED_NO_EFFECT", "PARTIAL_CONFIRMED"
]


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def exact(subject: str, raw: bytes) -> ExactHead:
    return ExactHead(subject_id=subject, head=subject + ":" + digest(raw), fingerprint=digest(raw))


def effect_head[T: NativeEffectDTO](value: T) -> T:
    value = value.model_copy(update={"head": "pending"})
    return value.model_copy(update={"head": "effect:" + value.digest()})


def record(value: NativeEffectDTO | NativeCallStateV1) -> Record:
    return Record(
        value.head,
        "effects" if isinstance(value, NativeEffectDTO) else "agent_loop",
        value.schema_id,
        value.canonical_bytes(),
    )


@dataclass(frozen=True)
class PostgresEffectsBinding:
    provider: HermeticEffectsProvider
    receipt_key: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not self.receipt_key
            or self.provider._journal_path is None
            or self.provider._observation_journal_path is None
        ):
            raise ValueError(
                "native effects require persistent transfer/observation custody and key"
            )
        if self.provider._receipt_key != self.receipt_key:
            raise ValueError("provider receipt key differs from registered key")

    def target(self, runtime: PostgresExecutionRuntime) -> ProviderRecipient:
        return self.target_for_principal(runtime.principal_id)

    def target_for_principal(self, principal_id: str) -> ProviderRecipient:
        if principal_id != "hermetic-principal":
            raise PermissionError(
                "registered provider supports only its authenticated self recipient"
            )
        return ProviderRecipient(
            provider="hermetic-effects",
            account="hermetic-account",
            recipient=principal_id,
            canonical_address=b"hermetic://effects/hermetic-principal",
            endpoint=exact("hermetic-effects-endpoint", b"hermetic-effects.v1"),
            credential_binding=exact(
                "hermetic-effects-credential", digest(self.receipt_key).encode()
            ),
        )


def rows[T: NativeEffectDTO](
    runtime: PostgresExecutionRuntime, snapshot: Snapshot, kind: type[T], call_id: str
) -> tuple[T, ...]:
    result = []
    schema = kind.model_fields["schema_id"].default
    for batch in snapshot.batches:
        for item in batch.records:
            if item.schema_id != schema:
                continue
            try:
                value = kind.model_validate_json(item.canonical_bytes)
                if (
                    item.owner != "effects"
                    or item.record_id != value.head
                    or effect_head(value).head != value.head
                    or value.canonical_bytes() != item.canonical_bytes
                    or value.tenant_id != runtime.tenant_id
                    or value.principal_id != runtime.principal_id
                ):
                    raise ValueError("native effect record identity differs")
            except ValueError as error:
                raise PostgresIntegrityError(
                    f"operation=effects.read tenant={runtime.tenant_id} record_id={item.record_id}"
                ) from error
            if value.original_call_id == call_id:
                result.append(value)
    return tuple(result)


def one[T: NativeEffectDTO](
    runtime: PostgresExecutionRuntime, snapshot: Snapshot, kind: type[T], call_id: str
) -> T:
    selected = rows(runtime, snapshot, kind, call_id)
    if len(selected) != 1:
        raise LoopHold("original effect requires exactly one " + kind.__name__)
    return selected[0]


class CommonFields(TypedDict):
    head: str
    tenant_id: str
    principal_id: str
    original_call_id: str


def common(runtime: PostgresExecutionRuntime, call_id: str) -> CommonFields:
    return dict(
        head="pending",
        tenant_id=runtime.tenant_id,
        principal_id=runtime.principal_id,
        original_call_id=call_id,
    )


def ticket(send: NativeEffectSendV1) -> IssuedEffectSendTicket:
    return decode_ticket(send.ticket_bytes)


def decode_ticket(raw: bytes) -> IssuedEffectSendTicket:
    body = json.loads(raw)
    body["payload"] = bytes.fromhex(body["payload"])
    body["canonical_address"] = bytes.fromhex(body["canonical_address"])
    body["bundle_members"] = tuple(body["bundle_members"])
    return IssuedEffectSendTicket(**body)


def binding_matches(
    runtime: PostgresExecutionRuntime,
    binding: PostgresEffectsBinding,
    intent: NativeEffectIntentV1,
    *,
    live: bool,
) -> None:
    if intent.preview.target != binding.target(
        runtime
    ) or intent.preview.receipt_key_fingerprint != digest(binding.receipt_key):
        raise PermissionError("original effect registration changed")
    if live and time.time_ns() >= intent.preview.expires_at_ns:
        raise LoopHold("adopted effect horizon expired")


def require_active_original_run(
    runtime: PostgresExecutionRuntime, snapshot: Snapshot, run_id: str
) -> None:
    selected = [run for run in runtime._runs(snapshot) if run.run_id == run_id]
    if not selected or selected[-1].state != "ACTIVE":
        raise LoopHold("new effect admission requires an ACTIVE original Run")


async def preview_effect(
    runtime: PostgresExecutionRuntime,
    binding: PostgresEffectsBinding,
    peer: str,
    call_id: str,
    expires_at_ns: int,
) -> NativeEffectPreviewV1:
    snapshot = runtime._snapshot_for_peer(peer)
    state = runtime._call_states(snapshot).get(call_id)
    originals = [
        call for call in runtime._initialized(snapshot) if call.original_call_id == call_id
    ]
    if state is None or len(originals) != 1 or originals[0].call.classification != "CONSEQUENTIAL":
        raise LoopRejected("preview requires an original consequential call")
    if state.state != "INITIALIZED" or expires_at_ns <= time.time_ns():
        raise LoopRejected("preview requires an unchosen call and future explicit horizon")
    require_active_original_run(runtime, snapshot, state.run_id)
    call = ConsequentialToolCall.model_validate_json(
        base64.b64decode(originals[0].call.canonical_call_base64, validate=True)
    )
    if len(set(call.arguments.bundle_members)) != len(call.arguments.bundle_members):
        raise LoopRejected("effect bundle repeats a member")
    preview = effect_head(
        NativeEffectPreviewV1(
            **common(runtime, call_id),
            initialized_head=state.head,
            payload=call.arguments.payload,
            bundle_members=call.arguments.bundle_members,
            target=binding.target(runtime),
            expires_at_ns=expires_at_ns,
            receipt_key_fingerprint=digest(binding.receipt_key),
        )
    )
    await runtime.publish_execution_batch(
        peer, "effects.preview", preview.head, snapshot.head, (record(preview),)
    )
    return preview


async def adopt_effect(
    runtime: PostgresExecutionRuntime,
    binding: PostgresEffectsBinding,
    peer: str,
    preview_head: str,
    authority_act_id: str,
) -> NativeEffectIntentV1:
    if not authority_act_id:
        raise LoopRejected("explicit exact-preview adoption required")
    snapshot = runtime._snapshot_for_peer(peer)
    previews = [
        NativeEffectPreviewV1.model_validate_json(item.canonical_bytes)
        for batch in snapshot.batches
        for item in batch.records
        if item.record_id == preview_head and item.schema_id == "chiplog.effects.native-preview.v1"
    ]
    if len(previews) != 1:
        raise LoopRejected("selected preview absent")
    preview = previews[0]
    call_id = preview.original_call_id
    if preview not in rows(runtime, snapshot, NativeEffectPreviewV1, call_id):
        raise LoopRejected("selected preview differs from durable original")
    existing = rows(runtime, snapshot, NativeEffectIntentV1, call_id)
    if existing:
        if existing[0].preview == preview and existing[0].authority_act_id == authority_act_id:
            return existing[0]
        raise LoopRejected("original effect adoption already selected")
    state = runtime._call_states(snapshot)[call_id]
    require_active_original_run(runtime, snapshot, state.run_id)
    if state.state != "INITIALIZED" or state.head != preview.initialized_head:
        raise LoopRejected("preview original call branch changed")
    accepted = headed(
        state.model_copy(
            update={
                "predecessor": state.head,
                "state": "ACCEPTED",
                "authority_act_id": authority_act_id,
                "external_intent_id": call_id + "/effect",
            }
        ),
        "call-state:",
    )
    intent = effect_head(
        NativeEffectIntentV1(
            **common(runtime, call_id),
            intent_id=call_id + "/effect",
            run_id=state.run_id,
            accepted_head=accepted.head,
            authority_act_id=authority_act_id,
            preview=preview,
        )
    )
    binding_matches(runtime, binding, intent, live=True)
    await runtime.publish_execution_batch(
        peer, "effects.adopt", call_id, snapshot.head, (record(accepted), record(intent))
    )
    return intent


async def commit_effect_send(
    runtime: PostgresExecutionRuntime,
    binding: PostgresEffectsBinding,
    peer: str,
    call_id: str,
    request: DeploymentGateRequest,
) -> NativeEffectSendV1:
    with runtime._transaction() as (connection, trust):
        runtime._actor(connection, trust, peer)
        snapshot = runtime.database._snapshot(connection)
        intent = one(runtime, snapshot, NativeEffectIntentV1, call_id)
        existing = rows(runtime, snapshot, NativeEffectSendV1, call_id)
        if existing:
            if existing[0].gate_request_bytes != canonical(request.model_dump(mode="json")):
                raise LoopRejected("first-send deployment request changed")
            return existing[0]
        binding_matches(runtime, binding, intent, live=True)
        require_active_original_run(runtime, snapshot, intent.run_id)
        state = runtime._call_states(snapshot)[call_id]
        if state.head != intent.accepted_head or state.state != "ACCEPTED":
            raise LoopRejected("first send requires exact adopted call")
        gate = PostgresDeploymentGate(
            connection, runtime.tenant_id, runtime._authenticate_entitlement
        )
        gate.require(request, "effects.first_send", intent.canonical_bytes(), live=True)
        child = exact(intent.intent_id + "/child/0", intent.canonical_bytes())
        transmission = TransmissionAttempt(
            transmission=child,
            intent=exact(intent.intent_id, intent.canonical_bytes()),
            ordinal=0,
            semantics=SEMANTICS,
            dispatch_time_ns=time.time_ns(),
            payload_fingerprint=digest(intent.preview.payload),
            recipient=intent.preview.target,
            idempotency_fence_key=intent.intent_id + "/first-send",
            send_commit=exact(intent.intent_id + "/send", request.model_dump_json().encode()),
            coverage_proof=None,
        )
        target = intent.preview.target
        issued = IssuedEffectSendTicket(
            tenant_id=runtime.tenant_id,
            issued_operation_id=request.operation_id,
            journal_decision_id="postgres:" + runtime.database.name + ":" + request.operation_id,
            journal_decision_fingerprint=digest(intent.canonical_bytes()),
            transmission_id=child.subject_id,
            transmission_fingerprint=child.fingerprint,
            request_ordinal=0,
            intent_id=intent.intent_id,
            intent_fingerprint=intent.digest(),
            provider=target.provider,
            account=target.account,
            recipient=target.recipient,
            canonical_address=target.canonical_address,
            endpoint_head=target.endpoint.head,
            credential_binding_head=target.credential_binding.head,
            adapter_contract_version=SEMANTICS.adapter_contract_version,
            payload=intent.preview.payload,
            payload_fingerprint=digest(intent.preview.payload),
            idempotency_key=transmission.idempotency_fence_key,
            bundle_members=intent.preview.bundle_members,
        )
        body = asdict(issued)
        body["payload"], body["canonical_address"] = (
            issued.payload.hex(),
            issued.canonical_address.hex(),
        )
        send = effect_head(
            NativeEffectSendV1(
                **common(runtime, call_id),
                intent_head=intent.head,
                transmission=transmission,
                ticket_bytes=canonical(body),
                gate_request_bytes=canonical(request.model_dump(mode="json")),
            )
        )
        runtime.database.append(
            connection, "effects.first-send", call_id, send.digest(), (record(send),), snapshot.head
        )
        gate.crossed(request, intent.canonical_bytes())
        return send


async def emit_effect(
    runtime: PostgresExecutionRuntime, binding: PostgresEffectsBinding, peer: str, call_id: str
) -> NativeEffectClaimV1:
    # The selected claim is irreversible. Recovery may reconcile; it never SENDs again.
    with runtime._transaction() as (connection, trust):
        runtime._actor(connection, trust, peer)
        snapshot = runtime.database._snapshot(connection)
        existing = rows(runtime, snapshot, NativeEffectClaimV1, call_id)
        if existing:
            return existing[0]
        intent = one(runtime, snapshot, NativeEffectIntentV1, call_id)
        send = one(runtime, snapshot, NativeEffectSendV1, call_id)
        binding_matches(runtime, binding, intent, live=True)
        PostgresDeploymentGate(
            connection, runtime.tenant_id, runtime._authenticate_entitlement
        ).require(
            DeploymentGateRequest.model_validate_json(send.gate_request_bytes),
            "effects.first_send",
            intent.canonical_bytes(),
            live=True,
        )
        state = runtime._call_states(snapshot)[call_id]
        if state.head != intent.accepted_head:
            raise LoopRejected("effect call changed before emission claim")
        runs = [run for run in runtime._runs(snapshot) if run.run_id == intent.run_id]
        run = runs[-1]
        if run.state != "ACTIVE":
            raise LoopHold("original Run is no longer active")
        claim = effect_head(
            NativeEffectClaimV1(
                **common(runtime, call_id), send_head=send.head, worker=runtime._worker
            )
        )
        obligation = OriginalObligationBinding(
            original_run_id=run.run_id,
            original_call_id=call_id,
            obligation_id=intent.intent_id + "/obligation",
            obligation_stream_id=intent.intent_id,
            obligation_head=claim.head,
            closure_predicate_id="native.original-effect.closed",
            closure_predicate_version="1",
            resolver_id="native.original-effect.resolver",
            resolver_version="1",
            reducer_id=SEMANTICS.reducer_version,
            reducer_version="2",
            evidence_stream_id=intent.intent_id + "/evidence",
            evidence_head=Absent(),
        )
        opened_run = advance(
            run,
            "OriginalEffectClaimed",
            original_obligations=(*run.original_obligations, obligation),
            no_retry_references=(
                *run.no_retry_references,
                CallSubjectHead(
                    subject_id=claim.head,
                    revision=Present(head="record:" + claim.digest(), fingerprint=claim.digest()),
                ),
            ),
        )
        unknown = headed(
            state.model_copy(update={"predecessor": state.head, "outcome": "OUTCOME_UNKNOWN"}),
            "call-state:",
        )
        runtime.database.append(
            connection,
            "effects.emit-claim",
            call_id,
            claim.digest(),
            (
                record(claim),
                record(unknown),
                Record(
                    opened_run.head,
                    "agent_loop",
                    opened_run.schema_id,
                    opened_run.canonical_bytes(),
                ),
            ),
            snapshot.head,
        )
    runtime.current_worker()
    try:
        receipt = await binding.provider.emit_issued(ticket(send))
    except HermeticResponseLost:
        return claim
    await append_effect_receipt(runtime, binding, peer, call_id, receipt)
    return claim


def evidence_outcome(
    runtime: PostgresExecutionRuntime,
    binding: PostgresEffectsBinding,
    snapshot: Snapshot,
    call_id: str,
    additional: tuple[NativeEffectEvidenceV1, ...] = (),
) -> tuple[str, tuple[str, ...]]:
    intent = one(runtime, snapshot, NativeEffectIntentV1, call_id)
    send = one(runtime, snapshot, NativeEffectSendV1, call_id)
    one(runtime, snapshot, NativeEffectClaimV1, call_id)
    binding_matches(runtime, binding, intent, live=False)
    evidence = (*rows(runtime, snapshot, NativeEffectEvidenceV1, call_id), *additional)
    members = tuple(exact(member, member.encode()) for member in intent.preview.bundle_members)
    member_map = {member.subject_id: member for member in members}
    observations = []
    previous = None
    for retained in evidence:
        if retained.send_head != send.head or retained.predecessor_evidence != previous:
            error = ValueError("native evidence ancestry differs")
            raise PostgresIntegrityError(
                f"operation=effects.reduce tenant={runtime.tenant_id} record_id={retained.head}"
            ) from error
        previous = retained.head
        parsed = verify_hermetic_receipt(
            ticket(send), retained.raw_receipt, registered_receipt_key=binding.receipt_key
        )
        for values, outcome in (
            (parsed.occurred_members, "OCCURRED"),
            (parsed.permanently_incapable_members, "PERMANENTLY_INCAPABLE"),
        ):
            for member in values:
                observations.append(
                    ChildMemberEvidence(
                        evidence=exact(
                            retained.head + "/" + member + "/" + outcome,
                            retained.raw_receipt + member.encode() + outcome.encode(),
                        ),
                        child=send.transmission.transmission,
                        member=member_map[member],
                        outcome=cast("Literal['OCCURRED', 'PERMANENTLY_INCAPABLE']", outcome),
                    )
                )
    outcome = reduce_child_evidence(
        "OUTCOME_UNKNOWN", (send.transmission,), members, tuple(observations)
    )
    return outcome, tuple(value.head for value in evidence)


async def append_effect_receipt(
    runtime: PostgresExecutionRuntime,
    binding: PostgresEffectsBinding,
    peer: str,
    call_id: str,
    raw: bytes,
) -> NativeEffectEvidenceV1:
    snapshot = runtime._snapshot_for_peer(peer)
    intent = one(runtime, snapshot, NativeEffectIntentV1, call_id)
    send = one(runtime, snapshot, NativeEffectSendV1, call_id)
    one(runtime, snapshot, NativeEffectClaimV1, call_id)
    binding_matches(runtime, binding, intent, live=False)
    verify_hermetic_receipt(ticket(send), raw, registered_receipt_key=binding.receipt_key)
    evidence = rows(runtime, snapshot, NativeEffectEvidenceV1, call_id)
    for value in evidence:
        if value.raw_receipt == raw:
            return value
    retained = effect_head(
        NativeEffectEvidenceV1(
            **common(runtime, call_id),
            send_head=send.head,
            predecessor_evidence=evidence[-1].head if evidence else None,
            raw_receipt=raw,
        )
    )
    outcome, inventory = evidence_outcome(runtime, binding, snapshot, call_id, (retained,))
    closures = rows(runtime, snapshot, NativeEffectClosureV1, call_id)
    reduction = effect_head(
        NativeEffectReductionV1(
            **common(runtime, call_id),
            send_head=send.head,
            closure_head=closures[0].head if closures else None,
            complete_evidence_heads=inventory,
            outcome=cast(
                NativeOutcome,
                outcome,
            ),
        )
    )
    await runtime.publish_execution_batch(
        peer, "effects.receipt", retained.head, snapshot.head, (record(retained), record(reduction))
    )
    return retained


async def reconcile_effect(
    runtime: PostgresExecutionRuntime, binding: PostgresEffectsBinding, peer: str, call_id: str
) -> NativeEffectEvidenceV1 | None:
    snapshot = runtime._snapshot_for_peer(peer)
    send = one(runtime, snapshot, NativeEffectSendV1, call_id)
    one(runtime, snapshot, NativeEffectClaimV1, call_id)
    raw = binding.provider.reconcile(send.transmission.transmission.subject_id)
    return (
        None if raw is None else await append_effect_receipt(runtime, binding, peer, call_id, raw)
    )


async def resolve_original_effect(
    runtime: PostgresExecutionRuntime, binding: PostgresEffectsBinding, peer: str, call_id: str
) -> NativeEffectClosureV1:
    with runtime._transaction() as (connection, trust):
        runtime._actor(connection, trust, peer)
        snapshot = runtime.database._snapshot(connection)
        intent = one(runtime, snapshot, NativeEffectIntentV1, call_id)
        send = one(runtime, snapshot, NativeEffectSendV1, call_id)
        outcome, inventory = evidence_outcome(runtime, binding, snapshot, call_id)
        if outcome not in {"CONFIRMED", "FAILED_NO_EFFECT", "PARTIAL_CONFIRMED"}:
            raise LoopHold("original effect evidence does not close every original member")
        previous = rows(runtime, snapshot, NativeEffectClosureV1, call_id)
        reductions = rows(runtime, snapshot, NativeEffectReductionV1, call_id)
        if (
            previous
            and reductions
            and reductions[-1].complete_evidence_heads == inventory
            and reductions[-1].closure_head == previous[0].head
        ):
            return previous[0]
        context = runtime._context(
            trust, runtime.principal_id, runtime.credential_id, runtime.session_id
        )
        invocation = effect_head(
            NativeEffectInvocationV1(
                **common(runtime, call_id),
                send_head=send.head,
                credential_id=runtime.credential_id,
                session_id=runtime.session_id,
                trust_materialization_head=context.trust_reference.materialization_head,
                complete_evidence_heads=inventory,
            )
        )
        closure = (
            previous[0]
            if previous
            else effect_head(
                NativeEffectClosureV1(
                    **common(runtime, call_id),
                    obligation_id=intent.intent_id + "/obligation",
                    send_head=send.head,
                    invocation_head=invocation.head,
                    complete_evidence_heads=inventory,
                    outcome=cast(
                        "Literal['CONFIRMED', 'FAILED_NO_EFFECT', 'PARTIAL_CONFIRMED']", outcome
                    ),
                )
            )
        )
        reduction = effect_head(
            NativeEffectReductionV1(
                **common(runtime, call_id),
                send_head=send.head,
                closure_head=closure.head,
                complete_evidence_heads=inventory,
                outcome=cast(
                    "Literal['CONFIRMED', 'FAILED_NO_EFFECT', 'PARTIAL_CONFIRMED']", outcome
                ),
            )
        )
        state = runtime._call_states(snapshot)[call_id]
        selected = headed(
            state.model_copy(
                update={
                    "predecessor": state.head,
                    "state": "TERMINAL",
                    "outcome": "FAILED_DEFINITE" if outcome == "FAILED_NO_EFFECT" else "SUCCEEDED",
                    "result_base64": base64.b64encode(
                        canonical({"effect_outcome": outcome})
                    ).decode(),
                }
            ),
            "call-state:",
        )
        records = (
            record(invocation),
            *((record(closure),) if not previous else ()),
            record(reduction),
        )
        if selected != state:
            records += (record(selected),)
        runtime.database.append(
            connection,
            "effects.resolve",
            reduction.head,
            reduction.digest(),
            records,
            snapshot.head,
        )
        return closure


def current_effect_result(
    runtime: PostgresExecutionRuntime,
    binding: PostgresEffectsBinding,
    snapshot: Snapshot,
    call_id: str,
) -> bytes:
    """Pure snapshot join: original closure AND complete current semantic result."""
    closure = one(runtime, snapshot, NativeEffectClosureV1, call_id)
    outcome, inventory = evidence_outcome(runtime, binding, snapshot, call_id)
    reductions = rows(runtime, snapshot, NativeEffectReductionV1, call_id)
    if (
        not reductions
        or reductions[-1].closure_head != closure.head
        or reductions[-1].complete_evidence_heads != inventory
        or reductions[-1].outcome != outcome
        or outcome not in {"CONFIRMED", "FAILED_NO_EFFECT", "PARTIAL_CONFIRMED"}
    ):
        raise LoopHold("original closure lacks a closed current semantic reduction")
    return canonical({"effect_outcome": outcome})
