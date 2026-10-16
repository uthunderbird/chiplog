"""Native metadata transitions preserve original work and do not await model lanes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.contracts import LoopHold, LoopRejected
from chiplog.capabilities.agent_loop.execution_client_transitions import (
    ExecutionRun,
    advance,
    headed,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.agent_loop.run_recovery_client_contracts import NativeSuspensionBaselineV1
from chiplog.capabilities.effects.client_effect_contracts import NativeEffectClaimV1
from chiplog.composition.postgres_effects import current_effect_result
from chiplog.platform.postgres import Record, Snapshot

if TYPE_CHECKING:
    from chiplog.composition.postgres_execution import PostgresExecutionRuntime


def _selected(runtime: PostgresExecutionRuntime, snapshot: Snapshot, run_id: str) -> ExecutionRun:
    rows = [row for row in runtime._runs(snapshot) if row.run_id == run_id]
    if not rows:
        raise LoopRejected("execution lifecycle Run is absent")
    return rows[-1]


def _baseline(snapshot: Snapshot, run: ExecutionRun) -> NativeSuspensionBaselineV1:
    reference = run.suspension_baseline
    if reference is None:
        raise LoopHold("native suspension baseline is absent")
    matches = [
        record for batch in snapshot.batches for record in batch.records
        if record.record_id == reference.subject_id
        and record.schema_id == "chiplog.agent-loop.native-suspension.v1"
    ]
    if len(matches) != 1:
        raise LoopHold("native suspension baseline is not selected")
    value = NativeSuspensionBaselineV1.model_validate_json(matches[0].canonical_bytes)
    if (
        matches[0].owner != "agent_loop" or value.head != reference.subject_id
        or value.run_id != run.run_id
        or reference.revision.head != "record:" + value.digest()
        or reference.revision.fingerprint != value.digest()
    ):
        raise LoopHold("native suspension baseline differs from the selected Run")
    return value


def _crossed_work_closed(
    runtime: PostgresExecutionRuntime, snapshot: Snapshot, run: ExecutionRun
) -> None:
    calls = {binding.original_call_id for binding in run.original_obligations}
    by_id = {
        record.record_id: record for batch in snapshot.batches for record in batch.records
    }
    for reference in run.no_retry_references:
        selected = by_id.get(reference.subject_id)
        if selected is None or selected.schema_id != "chiplog.effects.native-claim.v1":
            raise LoopHold("unknown native no-retry reference")
        claim = NativeEffectClaimV1.model_validate_json(selected.canonical_bytes)
        if (
            selected.owner != "effects" or selected.record_id != claim.head
            or reference.revision.head != "record:" + claim.digest()
            or reference.revision.fingerprint != claim.digest()
            or claim.tenant_id != run.tenant or claim.principal_id != run.principal
            or claim.original_call_id not in calls
        ):
            raise LoopHold("native no-retry reference differs from original obligation")
    if calls and runtime.effects_binding is None:
        raise LoopHold("original obligation resolver is unavailable")
    for call in calls:
        assert runtime.effects_binding is not None
        current_effect_result(runtime, runtime.effects_binding, snapshot, call)


async def _transition(
    runtime: PostgresExecutionRuntime, peer: str, run_id: str, expected_head: str,
    action: Literal["suspend", "resume", "abort"],
) -> ExecutionRun:
    # No model-lane acquisition: a pending HTTP invocation must not block abort.
    snapshot = runtime._snapshot_for_peer(peer)
    operation = "execution." + action
    key = run_id + ":" + expected_head
    replay = runtime._replay_run(snapshot, operation, key)
    if replay is not None:
        return replay

    def validate(current: Snapshot) -> ExecutionRun:
        run = _selected(runtime, current, run_id)
        if run.head != expected_head:
            raise LoopRejected("stale execution lifecycle Run")
        if action == "abort":
            if run.state not in ("CREATED", "ACTIVE", "SUSPENDED"):
                raise LoopRejected("terminal Run cannot be aborted or resurrected")
        elif action == "suspend":
            if run.state != "ACTIVE":
                raise LoopRejected("suspension requires ACTIVE Run")
            if not run.turns or run.turns[-1].response_seal is None:
                raise LoopHold("unsealed model attempt uses model recovery or abort")
        else:
            if run.state != "SUSPENDED":
                raise LoopRejected("resume requires SUSPENDED Run")
            baseline = _baseline(current, run)
            source = [
                row for row in runtime._runs(current)
                if row.run_id == run_id and row.head == baseline.source_run_head
            ]
            if (
                len(source) != 1 or source[0].turns != run.turns
                or source[0].original_obligations != run.original_obligations
                or source[0].no_retry_references != run.no_retry_references
            ):
                raise LoopHold("suspension no longer preserves its original work")
            _crossed_work_closed(runtime, current, run)
        return run

    run = validate(snapshot)
    records = []
    if action == "suspend":
        baseline = headed(NativeSuspensionBaselineV1(
            head="pending", run_id=run_id, source_run_head=run.head,
            source_frontier=snapshot.head,
        ), "suspension:")
        reference = CallSubjectHead(
            subject_id=baseline.head,
            revision=Present(head="record:" + baseline.digest(), fingerprint=baseline.digest()),
        )
        proposed = advance(
            run, "RunSuspended", state="SUSPENDED", suspension_baseline=reference
        )
        records.append(Record(
            baseline.head, "agent_loop", baseline.schema_id, baseline.canonical_bytes()
        ))
    elif action == "resume":
        proposed = advance(
            run, "RunResumed", state="ACTIVE", worker_session=runtime.current_worker()
        )
    else:
        proposed = advance(run, "RunAborted", state="ABORTED")
    records.append(Record(
        proposed.head, "agent_loop", proposed.schema_id, proposed.canonical_bytes()
    ))

    def guard(current: Snapshot) -> None:
        validate(current)

    await runtime.publish_execution_batch(
        peer, operation, key, snapshot.head, tuple(records), guard
    )
    return proposed


async def suspend_execution(
    runtime: PostgresExecutionRuntime, peer: str, run_id: str, expected_head: str
) -> ExecutionRun:
    return await _transition(runtime, peer, run_id, expected_head, "suspend")


async def resume_execution(
    runtime: PostgresExecutionRuntime, peer: str, run_id: str, expected_head: str
) -> ExecutionRun:
    return await _transition(runtime, peer, run_id, expected_head, "resume")


async def abort_execution(
    runtime: PostgresExecutionRuntime, peer: str, run_id: str, expected_head: str
) -> ExecutionRun:
    return await _transition(runtime, peer, run_id, expected_head, "abort")
