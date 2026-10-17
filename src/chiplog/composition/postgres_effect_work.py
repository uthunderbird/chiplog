"""Native action previews, compensation and bounded durable effect work."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from chiplog.adapters.driven.planning_postgres import PostgresPlanningRepository
from chiplog.capabilities.agent_loop.contracts import LoopHold, LoopRejected
from chiplog.capabilities.effects.action_lifecycle_contracts import (
    ActionProposalValue,
    ActionSendV1,
    CompensationActionProposal,
    NonToolEffectPreview,
)
from chiplog.capabilities.effects.client_effect_contracts import (
    NativeEffectIntentV1,
    NativeEffectSendV1,
)
from chiplog.capabilities.effects.effect_work_contracts import (
    CompensationEffectIntent,
    CompensationEffectRequest,
    EffectWork,
    EffectWorkDiscovery,
    EffectWorkScan,
    EffectWorkStep,
    PendingEffectSend,
    PlanEffectIntent,
    PlanEffectRequest,
    UnresolvedEffectClaim,
)
from chiplog.capabilities.planning import CreateIntentionLine
from chiplog.composition.postgres_effects import (
    PostgresEffectsBinding,
    claim_rows,
    closure_rows,
    digest,
    effect_head,
    emit_effect,
    evidence_inventory_head,
    exact,
    only,
    reconcile_effect,
    record,
    rows,
    stream,
    subject,
)
from chiplog.composition.r8 import command_bytes
from chiplog.platform.deployment_gate import DeploymentGateRequest
from chiplog.platform.postgres import Snapshot
from chiplog.platform.postgres_gate import PostgresDeploymentGate

if TYPE_CHECKING:
    from chiplog.composition.postgres_execution import PostgresExecutionRuntime


def require_proposal(
    runtime: PostgresExecutionRuntime,
    binding: PostgresEffectsBinding,
    snapshot: Snapshot,
    proposal: ActionProposalValue,
) -> None:
    if (proposal.tenant_id, proposal.principal_id) != (runtime.tenant_id, runtime.principal_id):
        raise LoopRejected("action proposal scope differs")
    if proposal.target != binding.target(runtime) or proposal.expires_at_ns <= time.time_ns():
        raise LoopHold("action target or horizon differs")
    if len(set(proposal.bundle_members)) != len(proposal.bundle_members):
        raise LoopRejected("action bundle repeats a member")
    if isinstance(proposal, CompensationActionProposal):
        original = subject(runtime, snapshot, proposal.original_stream_id)
        if exact(original.intent_id, original.canonical_bytes()) != proposal.original_intent:
            raise LoopRejected("compensation original intent differs")
        if proposal.intent_id == original.intent_id:
            raise LoopRejected("compensation requires a distinct intent identity")
        if (
            evidence_inventory_head(runtime, snapshot, proposal.original_stream_id)
            != proposal.original_evidence_head
        ):
            raise LoopHold("compensation original evidence changed")


async def preview_action(
    runtime: PostgresExecutionRuntime,
    binding: PostgresEffectsBinding,
    peer: str,
    proposal: ActionProposalValue,
) -> NonToolEffectPreview:
    with runtime._transaction() as (connection, trust):
        runtime._actor(connection, trust, peer)
        snapshot = runtime.database._snapshot(connection)
        require_proposal(runtime, binding, snapshot, proposal)
        preview = effect_head(
            NonToolEffectPreview(
                head="pending",
                proposal=proposal,
                receipt_key_fingerprint=digest(binding.receipt_key),
            )
        )
        runtime.database.append(
            connection,
            "effects.action-preview",
            preview.head,
            digest(preview.canonical_bytes()),
            (record(preview),),
            snapshot.head,
        )
        return preview


def selected_action_preview(
    runtime: PostgresExecutionRuntime,
    binding: PostgresEffectsBinding,
    snapshot: Snapshot,
    request: CompensationEffectRequest | PlanEffectRequest,
) -> NonToolEffectPreview:
    preview = only(
        tuple(
            item
            for item in rows(runtime, snapshot, NonToolEffectPreview, request.intent_id)
            if exact(item.head, item.canonical_bytes()) == request.adopted_preview
        )
    )
    if (
        request.model_dump(exclude={"adopted_preview", "authority_act_id"})
        != preview.proposal.model_dump()
    ):
        raise LoopRejected("action adoption differs from exact retained preview")
    require_proposal(runtime, binding, snapshot, preview.proposal)
    if preview.receipt_key_fingerprint != digest(binding.receipt_key):
        raise LoopHold("action receipt key changed")
    if not request.authority_act_id:
        raise LoopRejected("fresh exact-preview adoption required")
    return preview


async def adopt_compensation(
    runtime: PostgresExecutionRuntime,
    binding: PostgresEffectsBinding,
    peer: str,
    request: CompensationEffectRequest,
    gate_request: DeploymentGateRequest,
) -> CompensationEffectIntent:
    with runtime._transaction() as (connection, trust):
        runtime._actor(connection, trust, peer)
        snapshot = runtime.database._snapshot(connection)
        previous = rows(runtime, snapshot, CompensationEffectIntent, request.intent_id)
        if previous:
            value = only(previous)
            if value.request != request:
                raise LoopRejected("compensation identity reused with changed request")
            return value
        require_new_identity(runtime, snapshot, request.intent_id)
        selected_action_preview(runtime, binding, snapshot, request)
        original = subject(runtime, snapshot, request.original_stream_id)
        original_act = (
            original.original.authority_act_id
            if isinstance(original.original, NativeEffectIntentV1)
            else original.original.request.authority_act_id
        )
        if request.authority_act_id == original_act:
            raise LoopRejected("compensation requires a fresh adoption act")
        value = effect_head(CompensationEffectIntent(head="pending", request=request))
        gate = PostgresDeploymentGate(
            connection, runtime.tenant_id, runtime._authenticate_entitlement
        )
        gate.require(gate_request, "effects.first_send", request.canonical_bytes(), live=True)
        runtime.database.append(
            connection,
            "effects.compensation-adopt",
            request.intent_id,
            digest(request.canonical_bytes()),
            (record(value),),
            snapshot.head,
        )
        return value


async def discover_effect_work(
    runtime: PostgresExecutionRuntime,
    binding: PostgresEffectsBinding,
    peer: str,
    request: EffectWorkScan,
) -> EffectWorkDiscovery:
    snapshot = runtime._snapshot_for_peer(peer)
    if (request.tenant_id, request.principal_id) != (runtime.tenant_id, runtime.principal_id):
        raise LoopRejected("effect work scan scope differs")
    sends: tuple[NativeEffectSendV1 | ActionSendV1, ...] = (
        *rows(runtime, snapshot, NativeEffectSendV1, None),
        *rows(runtime, snapshot, ActionSendV1, None),
    )
    indexed = {item.head: item for item in sends}
    ordered = [
        indexed[item.record_id]
        for batch in snapshot.batches
        for item in batch.records
        if item.record_id in indexed
    ]
    if request.after_send is not None:
        positions = [index for index, item in enumerate(ordered) if item.head == request.after_send]
        if len(positions) != 1:
            raise LoopRejected("effect work cursor is not a retained scoped SEND")
        ordered = ordered[positions[0] + 1 :]
    items: list[EffectWork] = []
    last_send = None
    for send in ordered:
        stream_id = stream(send)
        value = subject(runtime, snapshot, stream_id)
        claims = claim_rows(runtime, snapshot, stream_id)
        # Closure alone is insufficient; late evidence must not hide behind it.
        if closure_rows(runtime, snapshot, stream_id):
            from chiplog.composition.postgres_effects import current_effect_result

            try:
                current_effect_result(runtime, binding, snapshot, stream_id)
            except LoopHold:
                pass
            else:
                continue
        fields = dict(
            tenant_id=runtime.tenant_id,
            principal_id=runtime.principal_id,
            stream_id=stream_id,
            intent=exact(value.intent_id, value.canonical_bytes()),
            send=exact(send.head, send.canonical_bytes()),
        )
        work = (
            UnresolvedEffectClaim.model_validate(
                {**fields, "claim": exact(only(claims).head, only(claims).canonical_bytes())}
            )
            if claims
            else PendingEffectSend.model_validate(fields)
        )
        items.append(work)
        last_send = send.head
        if len(items) == request.limit:
            return EffectWorkDiscovery(
                snapshot_head=str(snapshot.head),
                items=tuple(items),
                has_more=send != ordered[-1],
                next_send=last_send,
            )
    return EffectWorkDiscovery(
        snapshot_head=str(snapshot.head), items=tuple(items), has_more=False, next_send=None
    )


async def step_effect_work(
    runtime: PostgresExecutionRuntime,
    binding: PostgresEffectsBinding,
    peer: str,
    work: EffectWork,
) -> EffectWorkStep:
    snapshot = runtime._snapshot_for_peer(peer)
    if (work.tenant_id, work.principal_id) != (runtime.tenant_id, runtime.principal_id):
        raise LoopRejected("effect work scope differs")
    value = subject(runtime, snapshot, work.stream_id)
    send = only(
        tuple(item for item in rows(runtime, snapshot, NativeEffectSendV1, work.stream_id))
        + tuple(item for item in rows(runtime, snapshot, ActionSendV1, work.stream_id))
    )
    if work.intent != exact(value.intent_id, value.canonical_bytes()) or work.send != exact(
        send.head, send.canonical_bytes()
    ):
        raise LoopRejected("discovered effect work changed")
    claims = claim_rows(runtime, snapshot, work.stream_id)
    if isinstance(work, PendingEffectSend) and not claims:
        claim = await emit_effect(runtime, binding, peer, work.stream_id)
        return EffectWorkStep(
            work=work,
            disposition="EMITTED",
            current_head=claim.head,
            detail="send custody selected; provider call attempted",
        )
    claim = only(claims)
    if isinstance(work, UnresolvedEffectClaim) and work.claim != exact(
        claim.head, claim.canonical_bytes()
    ):
        raise LoopRejected("discovered claim changed")
    evidence = await reconcile_effect(runtime, binding, peer, work.stream_id)
    return EffectWorkStep(
        work=work,
        disposition="RECONCILED" if evidence else "HELD",
        current_head=evidence.head if evidence else claim.head,
        detail="evidence retained; resolver remains explicit"
        if evidence
        else "provider absence does not prove no effect",
    )


async def publish_plan_effect(
    runtime: PostgresExecutionRuntime,
    binding: PostgresEffectsBinding,
    peer: str,
    command: CreateIntentionLine,
    request: PlanEffectRequest,
    planning_gate: DeploymentGateRequest,
) -> PlanEffectIntent:
    """Two original owner batches commit in one physical PostgreSQL transaction."""
    with runtime._transaction() as (connection, trust):
        runtime._actor(connection, trust, peer)
        snapshot = runtime.database._snapshot(connection)
        previous = rows(runtime, snapshot, PlanEffectIntent, request.intent_id)
        if previous:
            value = only(previous)
            if value.request != request or command_bytes(command) != request.planning_command_bytes:
                raise LoopRejected("Plan/effect replay changed exact request")
            return value
        require_new_identity(runtime, snapshot, request.intent_id)
        selected_action_preview(runtime, binding, snapshot, request)
        if (
            request.planning_command_schema != "chiplog.planning.public.create.v2"
            or command_bytes(command) != request.planning_command_bytes
            or command.authority_act_id != request.authority_act_id
        ):
            raise LoopRejected("Planning command differs from adopted owner bytes")
        repository = PostgresPlanningRepository(runtime.database, runtime.tenant, connection)
        publications = repository.committed_publications(runtime.tenant)
        revisions = [
            item
            for item in publications
            if item.result.intention_line_id == command.intention_line_id
        ]
        expected = None
        if revisions:
            latest = revisions[-1]
            revision = only(
                tuple(
                    item for item in latest.records if item.record_id == latest.result.revision_id
                )
            )
            expected = exact(revision.record_id.value, revision.canonical_bytes)
        if request.expected_planning_revision != expected:
            raise LoopHold("adopted Planning revision changed")
        outcome = runtime.publish_plan_in_transaction(
            connection,
            trust,
            principal_id=runtime.principal_id,
            credential_id=runtime.credential_id,
            session_id=runtime.session_id,
            command=command,
            gate_request=planning_gate,
        )
        if outcome.disposition != "COMMITTED" or outcome.result is None:
            raise LoopRejected(
                "Plan/effect requires a fresh Planning commit: " + str(outcome.reason)
            )
        selected = runtime.database._snapshot(connection)
        selected_revision = only(
            tuple(
                item
                for batch in selected.batches
                for item in batch.records
                if item.record_id == outcome.result.revision_id.value
            )
        )
        value = effect_head(
            PlanEffectIntent(
                head="pending",
                request=request,
                planning_revision=exact(
                    selected_revision.record_id, selected_revision.canonical_bytes
                ),
            )
        )
        runtime.database.append(
            connection,
            "effects.plan-intent",
            request.intent_id,
            digest(request.canonical_bytes()),
            (record(value),),
            selected.head,
        )
        return value


def require_new_identity(
    runtime: PostgresExecutionRuntime, snapshot: Snapshot, intent_id: str
) -> None:
    values: tuple[NativeEffectIntentV1 | PlanEffectIntent | CompensationEffectIntent, ...] = (
        *rows(runtime, snapshot, NativeEffectIntentV1, None),
        *rows(runtime, snapshot, PlanEffectIntent, None),
        *rows(runtime, snapshot, CompensationEffectIntent, None),
    )
    if any(
        intent_id in (call.original_call_id, call.original_call_id + "/effect")
        for call in runtime._initialized(snapshot)
    ):
        raise LoopRejected("effect identity is reserved by an initialized tool call")
    if any(
        stream(value) == intent_id
        or (value.intent_id if isinstance(value, NativeEffectIntentV1) else value.request.intent_id)
        == intent_id
        for value in values
    ):
        raise LoopRejected("effect intent or stream identity already selected")
