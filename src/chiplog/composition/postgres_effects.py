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
from chiplog.capabilities.effects.action_lifecycle_contracts import (
    ActionClaimV1,
    ActionClosureV1,
    ActionEvidenceV1,
    ActionInvocationV1,
    ActionRecord,
    ActionReductionV1,
    ActionSendV1,
    CompensationActionProposal,
    NonToolEffectPreview,
    PlanActionProposal,
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
from chiplog.capabilities.effects.dispatch_authority_contracts import DispatchObservationDTO
from chiplog.capabilities.effects.domain import ChildMemberEvidence, reduce_child_evidence
from chiplog.capabilities.effects.effect_work_contracts import (
    CompensationEffectIntent,
    PlanEffectIntent,
)
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


def effect_head[T: DispatchObservationDTO](value: T) -> T:
    value = value.model_copy(update={"head": "pending"})
    return value.model_copy(update={"head": "effect:" + digest(value.canonical_bytes())})


def record(value: DispatchObservationDTO | NativeCallStateV1) -> Record:
    return Record(
        str(value.model_dump()["head"]),
        "agent_loop" if isinstance(value, NativeCallStateV1) else "effects",
        str(value.model_dump()["schema_id"]),
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


def rows[T: DispatchObservationDTO](
    runtime: PostgresExecutionRuntime, snapshot: Snapshot, kind: type[T], call_id: str | None
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
                    or item.record_id != value.model_dump()["head"]
                    or effect_head(value).model_dump()["head"] != value.model_dump()["head"]
                    or value.canonical_bytes() != item.canonical_bytes
                    or scope(value) != (runtime.tenant_id, runtime.principal_id)
                ):
                    raise ValueError("native effect record identity differs")
            except ValueError as error:
                raise PostgresIntegrityError(
                    f"operation=effects.read tenant={runtime.tenant_id} record_id={item.record_id}"
                ) from error
            if call_id is None or stream(value) == call_id:
                result.append(value)
    return tuple(result)


def one[T: DispatchObservationDTO](
    runtime: PostgresExecutionRuntime, snapshot: Snapshot, kind: type[T], call_id: str | None
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


def ticket(send: NativeEffectSendV1 | ActionSendV1) -> IssuedEffectSendTicket:
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
    intent: NativeEffectIntentV1 | EffectSubject,
    *,
    live: bool,
) -> None:
    if intent.preview.target != binding.target(runtime) or (
        intent.preview.receipt_key_fingerprint
        if isinstance(intent, NativeEffectIntentV1)
        else intent.receipt_key_fingerprint
    ) != digest(binding.receipt_key):
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
    action_intents: tuple[PlanEffectIntent | CompensationEffectIntent, ...] = (
        *rows(runtime, snapshot, PlanEffectIntent, None),
        *rows(runtime, snapshot, CompensationEffectIntent, None),
    )
    if any(item.request.intent_id in (call_id, call_id + "/effect") for item in action_intents):
        raise LoopRejected("tool identity collides with selected non-tool effect")
    state = runtime._call_states(snapshot)[call_id]
    require_active_original_run(runtime, snapshot, state.run_id)
    if state.state != "INITIALIZED" or state.head != preview.initialized_head:
        raise LoopRejected("preview original call branch changed")
    runtime.scheduler_fence(snapshot, state.run_id)
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
) -> NativeEffectSendV1 | ActionSendV1:
    with runtime._transaction() as (connection, trust):
        runtime._actor(connection, trust, peer)
        snapshot = runtime.database._snapshot(connection)
        intent = subject(runtime, snapshot, call_id)
        existing = send_rows(runtime, snapshot, call_id)
        if existing:
            if existing[0].gate_request_bytes != canonical(request.model_dump(mode="json")):
                raise LoopRejected("first-send deployment request changed")
            return existing[0]
        binding_matches(runtime, binding, intent, live=True)
        require_subject_live(runtime, snapshot, intent)
        if intent.tool is not None:
            state = runtime._call_states(snapshot)[call_id]
            if state.head != intent.tool.accepted_head or state.state != "ACCEPTED":
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
            (NativeEffectSendV1 if intent.tool is not None else ActionSendV1).model_validate(
                dict(
                    **intent.identity(),
                    intent_head=intent.head,
                    transmission=transmission,
                    ticket_bytes=canonical(body),
                    gate_request_bytes=canonical(request.model_dump(mode="json")),
                )
            )
        )
        send_records: tuple[Record, ...] = (record(send),)
        if intent.tool is not None:
            fence = runtime.scheduler_fence(snapshot, intent.tool.run_id)
            if fence is not None:
                send_records += (
                    Record(
                        send.head + "/scheduler-lease",
                        "agent_loop",
                        "chiplog.scheduler.action-fence.v1",
                        fence,
                    ),
                )
        runtime.database.append(
            connection, "effects.first-send", call_id, send.digest(), send_records, snapshot.head
        )
        gate.crossed(request, intent.canonical_bytes())
        return send


async def emit_effect(
    runtime: PostgresExecutionRuntime, binding: PostgresEffectsBinding, peer: str, call_id: str
) -> NativeEffectClaimV1 | ActionClaimV1:
    # The selected claim is irreversible. Recovery may reconcile; it never SENDs again.
    with runtime._transaction() as (connection, trust):
        runtime._actor(connection, trust, peer)
        snapshot = runtime.database._snapshot(connection)
        existing = claim_rows(runtime, snapshot, call_id)
        if existing:
            return existing[0]
        intent = subject(runtime, snapshot, call_id)
        send = only(send_rows(runtime, snapshot, call_id))
        binding_matches(runtime, binding, intent, live=True)
        PostgresDeploymentGate(
            connection, runtime.tenant_id, runtime._authenticate_entitlement
        ).require(
            DeploymentGateRequest.model_validate_json(send.gate_request_bytes),
            "effects.first_send",
            intent.canonical_bytes(),
            live=True,
        )
        require_subject_live(runtime, snapshot, intent)
        if intent.tool is not None:
            state = runtime._call_states(snapshot)[call_id]
            if state.head != intent.tool.accepted_head:
                raise LoopRejected("effect call changed before emission claim")
            expected_fence = selected_scheduler_fence(runtime, snapshot, send.head)
            runtime.assert_scheduler_fence(snapshot, intent.tool.run_id, expected_fence)
        claim = effect_head(
            (NativeEffectClaimV1 if intent.tool is not None else ActionClaimV1).model_validate(
                dict(**intent.identity(), send_head=send.head, worker=runtime._worker)
            )
        )
        companions: tuple[Record, ...] = ()
        if intent.tool is not None:
            run = [run for run in runtime._runs(snapshot) if run.run_id == intent.tool.run_id][-1]
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
                        revision=Present(
                            head="record:" + claim.digest(), fingerprint=claim.digest()
                        ),
                    ),
                ),
            )
            unknown = headed(
                state.model_copy(update={"predecessor": state.head, "outcome": "OUTCOME_UNKNOWN"}),
                "call-state:",
            )
            companions = (
                record(unknown),
                Record(
                    opened_run.head,
                    "agent_loop",
                    opened_run.schema_id,
                    opened_run.canonical_bytes(),
                ),
            )
        runtime.database.append(
            connection,
            "effects.emit-claim",
            call_id,
            claim.digest(),
            (record(claim), *companions),
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
    additional: tuple[NativeEffectEvidenceV1 | ActionEvidenceV1, ...] = (),
) -> tuple[str, tuple[str, ...]]:
    intent = subject(runtime, snapshot, call_id)
    send = only(send_rows(runtime, snapshot, call_id))
    only(claim_rows(runtime, snapshot, call_id))
    binding_matches(runtime, binding, intent, live=False)
    evidence = (*evidence_rows(runtime, snapshot, call_id), *additional)
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
) -> NativeEffectEvidenceV1 | ActionEvidenceV1:
    snapshot = runtime._snapshot_for_peer(peer)
    intent = subject(runtime, snapshot, call_id)
    send = only(send_rows(runtime, snapshot, call_id))
    only(claim_rows(runtime, snapshot, call_id))
    binding_matches(runtime, binding, intent, live=False)
    verify_hermetic_receipt(ticket(send), raw, registered_receipt_key=binding.receipt_key)
    evidence = evidence_rows(runtime, snapshot, call_id)
    for value in evidence:
        if value.raw_receipt == raw:
            return value
    retained = effect_head(
        (NativeEffectEvidenceV1 if intent.tool is not None else ActionEvidenceV1).model_validate(
            dict(
                **intent.identity(),
                send_head=send.head,
                predecessor_evidence=evidence[-1].head if evidence else None,
                raw_receipt=raw,
            )
        )
    )
    outcome, inventory = evidence_outcome(runtime, binding, snapshot, call_id, (retained,))
    closures = closure_rows(runtime, snapshot, call_id)
    reduction = effect_head(
        (NativeEffectReductionV1 if intent.tool is not None else ActionReductionV1).model_validate(
            dict(
                **intent.identity(),
                send_head=send.head,
                closure_head=closures[0].head if closures else None,
                complete_evidence_heads=inventory,
                outcome=cast(
                    NativeOutcome,
                    outcome,
                ),
            )
        )
    )
    await runtime.publish_execution_batch(
        peer, "effects.receipt", retained.head, snapshot.head, (record(retained), record(reduction))
    )
    return retained


async def reconcile_effect(
    runtime: PostgresExecutionRuntime, binding: PostgresEffectsBinding, peer: str, call_id: str
) -> NativeEffectEvidenceV1 | ActionEvidenceV1 | None:
    snapshot = runtime._snapshot_for_peer(peer)
    send = only(send_rows(runtime, snapshot, call_id))
    only(claim_rows(runtime, snapshot, call_id))
    raw = binding.provider.reconcile(send.transmission.transmission.subject_id)
    return (
        None if raw is None else await append_effect_receipt(runtime, binding, peer, call_id, raw)
    )


async def resolve_original_effect(
    runtime: PostgresExecutionRuntime, binding: PostgresEffectsBinding, peer: str, call_id: str
) -> NativeEffectClosureV1 | ActionClosureV1:
    with runtime._transaction() as (connection, trust):
        runtime._actor(connection, trust, peer)
        snapshot = runtime.database._snapshot(connection)
        intent = subject(runtime, snapshot, call_id)
        send = only(send_rows(runtime, snapshot, call_id))
        outcome, inventory = evidence_outcome(runtime, binding, snapshot, call_id)
        if outcome not in {"CONFIRMED", "FAILED_NO_EFFECT", "PARTIAL_CONFIRMED"}:
            raise LoopHold("original effect evidence does not close every original member")
        previous = closure_rows(runtime, snapshot, call_id)
        reductions = reduction_rows(runtime, snapshot, call_id)
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
            (
                NativeEffectInvocationV1 if intent.tool is not None else ActionInvocationV1
            ).model_validate(
                dict(
                    **intent.identity(),
                    send_head=send.head,
                    credential_id=runtime.credential_id,
                    session_id=runtime.session_id,
                    trust_materialization_head=context.trust_reference.materialization_head,
                    complete_evidence_heads=inventory,
                )
            )
        )
        closure = (
            previous[0]
            if previous
            else effect_head(
                (
                    NativeEffectClosureV1 if intent.tool is not None else ActionClosureV1
                ).model_validate(
                    dict(
                        **intent.identity(),
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
        )
        reduction = effect_head(
            (
                NativeEffectReductionV1 if intent.tool is not None else ActionReductionV1
            ).model_validate(
                dict(
                    **intent.identity(),
                    send_head=send.head,
                    closure_head=closure.head,
                    complete_evidence_heads=inventory,
                    outcome=cast(
                        "Literal['CONFIRMED', 'FAILED_NO_EFFECT', 'PARTIAL_CONFIRMED']", outcome
                    ),
                )
            )
        )
        records = (
            record(invocation),
            *((record(closure),) if not previous else ()),
            record(reduction),
        )
        if intent.tool is not None:
            state = runtime._call_states(snapshot)[call_id]
            selected = headed(
                state.model_copy(
                    update={
                        "predecessor": state.head,
                        "state": "TERMINAL",
                        "outcome": "FAILED_DEFINITE"
                        if outcome == "FAILED_NO_EFFECT"
                        else "SUCCEEDED",
                        "result_base64": base64.b64encode(
                            canonical({"effect_outcome": outcome})
                        ).decode(),
                    }
                ),
                "call-state:",
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
    closure = only(closure_rows(runtime, snapshot, call_id))
    outcome, inventory = evidence_outcome(runtime, binding, snapshot, call_id)
    reductions = reduction_rows(runtime, snapshot, call_id)
    if (
        not reductions
        or reductions[-1].closure_head != closure.head
        or reductions[-1].complete_evidence_heads != inventory
        or reductions[-1].outcome != outcome
        or outcome not in {"CONFIRMED", "FAILED_NO_EFFECT", "PARTIAL_CONFIRMED"}
    ):
        raise LoopHold("original closure lacks a closed current semantic reduction")
    return canonical({"effect_outcome": outcome})


def scope(value: DispatchObservationDTO) -> tuple[str, str]:
    if isinstance(value, (PlanEffectIntent, CompensationEffectIntent)):
        return value.request.tenant_id, value.request.principal_id
    if isinstance(value, NonToolEffectPreview):
        return value.proposal.tenant_id, value.proposal.principal_id
    if isinstance(value, (NativeEffectDTO, ActionRecord)):
        return value.tenant_id, value.principal_id
    raise ValueError("unregistered effect record scope")


def stream(value: DispatchObservationDTO) -> str:
    if isinstance(value, (PlanEffectIntent, CompensationEffectIntent)):
        return value.request.intent_id
    if isinstance(value, NonToolEffectPreview):
        return value.proposal.intent_id
    if isinstance(value, NativeEffectDTO):
        return value.original_call_id
    if isinstance(value, ActionRecord):
        return value.effect_stream_id
    raise ValueError("unregistered effect record stream")


@dataclass(frozen=True)
class EffectSubject:
    """Verified projection of owner bytes; never persisted as another intent."""

    original: NativeEffectIntentV1 | PlanEffectIntent | CompensationEffectIntent
    preview: NativeEffectPreviewV1 | PlanActionProposal | CompensationActionProposal
    receipt_key_fingerprint: str
    stream_id: str

    @property
    def tool(self) -> NativeEffectIntentV1 | None:
        return self.original if isinstance(self.original, NativeEffectIntentV1) else None

    @property
    def intent_id(self) -> str:
        if isinstance(self.original, NativeEffectIntentV1):
            return self.original.intent_id
        return self.original.request.intent_id

    @property
    def head(self) -> str:
        return self.original.head

    def canonical_bytes(self) -> bytes:
        return self.original.canonical_bytes()

    def digest(self) -> str:
        return digest(self.canonical_bytes())

    def identity(self) -> dict[str, str]:
        tenant, principal = scope(self.original)
        key = "original_call_id" if self.tool is not None else "effect_stream_id"
        return dict(
            head="pending", tenant_id=tenant, principal_id=principal, **{key: self.stream_id}
        )


def only[T](values: tuple[T, ...]) -> T:
    if len(values) != 1:
        raise LoopHold("effect requires exactly one selected record")
    return values[0]


def subject(runtime: PostgresExecutionRuntime, snapshot: Snapshot, stream_id: str) -> EffectSubject:
    originals: tuple[NativeEffectIntentV1 | PlanEffectIntent | CompensationEffectIntent, ...] = (
        *rows(runtime, snapshot, NativeEffectIntentV1, stream_id),
        *rows(runtime, snapshot, PlanEffectIntent, stream_id),
        *rows(runtime, snapshot, CompensationEffectIntent, stream_id),
    )
    value = only(originals)
    if isinstance(value, NativeEffectIntentV1):
        return EffectSubject(value, value.preview, value.preview.receipt_key_fingerprint, stream_id)
    preview = only(
        tuple(
            item
            for item in rows(runtime, snapshot, NonToolEffectPreview, stream_id)
            if exact(item.head, item.canonical_bytes()) == value.request.adopted_preview
        )
    )
    proposal = value.request.model_dump(exclude={"adopted_preview", "authority_act_id"})
    if proposal != preview.proposal.model_dump():
        raise LoopHold("intent differs from selected exact action preview")
    return EffectSubject(value, preview.proposal, preview.receipt_key_fingerprint, stream_id)


def require_subject_live(
    runtime: PostgresExecutionRuntime, snapshot: Snapshot, value: EffectSubject
) -> None:
    if value.tool is not None:
        require_active_original_run(runtime, snapshot, value.tool.run_id)
    if isinstance(value.original, CompensationEffectIntent):
        request = value.original.request
        original = subject(runtime, snapshot, request.original_stream_id)
        if exact(original.intent_id, original.canonical_bytes()) != request.original_intent:
            raise LoopHold("compensation original intent changed")
        if (
            evidence_inventory_head(runtime, snapshot, request.original_stream_id)
            != request.original_evidence_head
        ):
            raise LoopHold("compensation original evidence changed")


def evidence_inventory_head(
    runtime: PostgresExecutionRuntime, snapshot: Snapshot, stream_id: str
) -> ExactHead:
    value = subject(runtime, snapshot, stream_id)
    send = only(send_rows(runtime, snapshot, stream_id))
    claim = only(claim_rows(runtime, snapshot, stream_id))
    raw = canonical(
        dict(
            intent=value.head,
            send=send.head,
            claim=claim.head,
            evidence=[item.head for item in evidence_rows(runtime, snapshot, stream_id)],
        )
    )
    return exact(value.intent_id + "/evidence-inventory", raw)


def send_rows(
    runtime: PostgresExecutionRuntime, snapshot: Snapshot, stream_id: str
) -> tuple[NativeEffectSendV1 | ActionSendV1, ...]:
    return (
        *rows(runtime, snapshot, NativeEffectSendV1, stream_id),
        *rows(runtime, snapshot, ActionSendV1, stream_id),
    )


def claim_rows(
    runtime: PostgresExecutionRuntime, snapshot: Snapshot, stream_id: str
) -> tuple[NativeEffectClaimV1 | ActionClaimV1, ...]:
    return (
        *rows(runtime, snapshot, NativeEffectClaimV1, stream_id),
        *rows(runtime, snapshot, ActionClaimV1, stream_id),
    )


def evidence_rows(
    runtime: PostgresExecutionRuntime, snapshot: Snapshot, stream_id: str
) -> tuple[NativeEffectEvidenceV1 | ActionEvidenceV1, ...]:
    return (
        *rows(runtime, snapshot, NativeEffectEvidenceV1, stream_id),
        *rows(runtime, snapshot, ActionEvidenceV1, stream_id),
    )


def closure_rows(
    runtime: PostgresExecutionRuntime, snapshot: Snapshot, stream_id: str
) -> tuple[NativeEffectClosureV1 | ActionClosureV1, ...]:
    return (
        *rows(runtime, snapshot, NativeEffectClosureV1, stream_id),
        *rows(runtime, snapshot, ActionClosureV1, stream_id),
    )


def reduction_rows(
    runtime: PostgresExecutionRuntime, snapshot: Snapshot, stream_id: str
) -> tuple[NativeEffectReductionV1 | ActionReductionV1, ...]:
    return (
        *rows(runtime, snapshot, NativeEffectReductionV1, stream_id),
        *rows(runtime, snapshot, ActionReductionV1, stream_id),
    )


def selected_scheduler_fence(
    runtime: PostgresExecutionRuntime, snapshot: Snapshot, send_head: str
) -> bytes | None:
    identity = send_head + "/scheduler-lease"
    selected = [
        (batch, item)
        for batch in snapshot.batches
        for item in batch.records
        if item.record_id == identity
    ]
    if not selected:
        return None
    if (
        len(selected) != 1
        or selected[0][1].owner != "agent_loop"
        or selected[0][1].schema_id != "chiplog.scheduler.action-fence.v1"
        or not selected[0][1].canonical_bytes
        or not any(item.record_id == send_head for item in selected[0][0].records)
    ):
        error = ValueError("effect scheduler fence is not the exact SEND companion")
        raise PostgresIntegrityError(
            f"operation=effects.scheduler-fence tenant={runtime.tenant_id} record_id={identity}"
        ) from error
    return selected[0][1].canonical_bytes
