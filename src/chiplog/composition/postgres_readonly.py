"""One durable accepted attempt before a trusted, user-scoped history read."""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING

from chiplog.capabilities.agent_loop.call_acceptance_contracts import InitializedReadOnlyLineage
from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.readonly_history_tool_contracts import (
    ConversationHistoryQuery,
    ReadOnlyHistoryToolCall,
)
from chiplog.capabilities.agent_loop.readonly_runtime import (
    accept_readonly_attempt,
    initialize_readonly_lineage,
    record_readonly_outcome,
)
from chiplog.capabilities.agent_loop.readonly_runtime_contracts import (
    ReadOnlyLineageV2,
    ReadOnlyOutcomeV2,
)
from chiplog.platform.postgres import (
    PostgresIntegrityError,
    PostgresTransientReadError,
    Record,
    Snapshot,
)

if TYPE_CHECKING:
    from chiplog.composition.postgres_execution import PostgresExecutionRuntime

SCHEMA = "chiplog.readonly.lineage.v2"


class HistoryReadUnavailable(RuntimeError):
    """The trusted history reader reports a definite retryable read failure."""


def _selected(snapshot: Snapshot, original_call_id: str) -> ReadOnlyLineageV2 | None:
    selected = None
    for batch in snapshot.batches:
        for record in batch.records:
            if record.schema_id != SCHEMA:
                continue
            value = ReadOnlyLineageV2.model_validate_json(record.canonical_bytes)
            if record.owner != "agent_loop" or record.record_id != value.head:
                raise PostgresIntegrityError("read-only selected record identity differs")
            if value.original_call_id == original_call_id:
                selected = value
    return selected


def read_selected_readonly_lineage(
    runtime: PostgresExecutionRuntime, peer: str, original_call_id: str
) -> ReadOnlyLineageV2 | None:
    selected = _selected(runtime._snapshot_for_peer(peer), original_call_id)
    if selected is not None and (
        selected.selection.tenant_id != runtime.tenant_id
        or selected.selection.principal_id != runtime.principal_id
    ):
        raise PermissionError("read-only lineage belongs to another user")
    return selected


def _call(
    runtime: PostgresExecutionRuntime, snapshot: Snapshot, original_call_id: str,
    *, executing_run_id: str | None = None, for_successor_admission: bool = False,
) -> tuple[str, ConversationHistoryQuery, str]:
    calls = [
        call for call in runtime._initialized(snapshot) if call.original_call_id == original_call_id
    ]
    if len(calls) != 1:
        raise LoopRejected("read-only original call is absent")
    call = calls[0]
    retry = call.call.retry_lineage
    if call.call.classification != "READ_ONLY" or not isinstance(retry, InitializedReadOnlyLineage):
        raise LoopRejected("call is not the registered history reader")
    if retry.lineage.max_attempts != 3:
        raise LoopRejected("unsupported read-only attempt budget")
    branch = runtime._call_states(snapshot).get(original_call_id)
    if branch is None or branch.state != "INITIALIZED":
        raise LoopRejected("read-only call branch already selected or cancelled")
    run_id = call.call.original.original_run_id
    if executing_run_id is not None and executing_run_id != run_id:
        from chiplog.composition.postgres_readonly_successor import require_successor_authority

        require_successor_authority(
            runtime, snapshot, executing_run_id, original_call_id,
            for_successor_admission=for_successor_admission,
        )
        run_id = executing_run_id
    else:
        runs = [run for run in runtime._runs(snapshot) if run.run_id == run_id]
        allowed = ("ACTIVE", "SUSPENDED") if for_successor_admission else ("ACTIVE",)
        if not runs or runs[-1].state not in allowed:
            raise LoopRejected("read-only execution requires an ACTIVE Run")
    tool = ReadOnlyHistoryToolCall.model_validate_json(
        base64.b64decode(call.call.canonical_call_base64, validate=True)
    )
    return retry.lineage.lineage_id, tool.arguments, run_id


async def _publish(
    runtime: PostgresExecutionRuntime,
    peer: str,
    previous: ReadOnlyLineageV2 | None,
    proposed: ReadOnlyLineageV2,
    phase: str,
) -> None:
    snapshot = runtime._snapshot_for_peer(peer)

    def guard(current: Snapshot) -> None:
        _, _, run_id = _call(
            runtime, current, proposed.original_call_id,
            executing_run_id=proposed.attempts[-1].run_id,
        )
        if phase == "accept":
            from chiplog.composition.postgres_run_recovery import _crossed_work_closed

            run = [row for row in runtime._runs(current) if row.run_id == run_id][-1]
            _crossed_work_closed(runtime, current, run)
        if _selected(current, proposed.original_call_id) != previous:
            raise LoopRejected("stale read-only lineage publication")

    await runtime.publish_execution_batch(
        peer,
        "readonly." + phase + ".v2",
        proposed.head,
        snapshot.head,
        (Record(proposed.head, "agent_loop", SCHEMA, proposed.canonical_bytes()),),
        guard,
    )


async def execute_readonly_call(
    runtime: PostgresExecutionRuntime,
    peer: str,
    original_call_id: str,
    *,
    expected_counter_head: str | None = None,
) -> ReadOnlyLineageV2:
    async with runtime._lane:
        snapshot = runtime._snapshot_for_peer(peer)
        previous = _selected(snapshot, original_call_id)
        if previous is not None and (
            previous.selection.tenant_id != runtime.tenant_id
            or previous.selection.principal_id != runtime.principal_id
        ):
            raise PermissionError("read-only lineage belongs to another user")
        if previous is not None and (previous.terminal or expected_counter_head is None):
            return previous
        if previous is not None and previous.head != expected_counter_head:
            raise LoopRejected("stale read-only counter head")
        if previous is None and expected_counter_head is not None:
            raise LoopRejected("read-only lineage is absent")
        lineage_id, query, run_id = _call(
            runtime, snapshot, original_call_id,
            executing_run_id=previous.attempts[-1].run_id if previous is not None else None,
        )
        current = previous
        if current is None:
            current = initialize_readonly_lineage(
                lineage_id=lineage_id,
                original_call_id=original_call_id,
                query=query,
                selection=runtime.select_history_snapshot(peer, query),
            )
        accepted = accept_readonly_attempt(current, run_id=run_id, expected_head=current.head)
        await _publish(runtime, peer, previous, accepted, "accept")
        return await _finish_accepted(runtime, peer, accepted)


async def _finish_accepted(
    runtime: PostgresExecutionRuntime, peer: str, accepted: ReadOnlyLineageV2
) -> ReadOnlyLineageV2:
    try:
        value = runtime.read_history_snapshot(peer, accepted.selection, accepted.query)
    except HistoryReadUnavailable, PostgresTransientReadError:
        outcome = ReadOnlyOutcomeV2(
            kind="FAILED_DEFINITE_RETRYABLE", reason="history temporarily unavailable"
        )
    else:
        outcome = ReadOnlyOutcomeV2(kind="SUCCEEDED", value=value)
    complete = record_readonly_outcome(accepted, outcome)
    await _publish(runtime, peer, accepted, complete, "outcome")
    return complete
