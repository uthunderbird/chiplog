"""Atomic pending retry succession; inherited Turns keep their original identities."""

from __future__ import annotations

from typing import TYPE_CHECKING

from chiplog.capabilities.agent_loop.contracts import LoopHold, LoopRejected
from chiplog.capabilities.agent_loop.execution_client_transitions import (
    ExecutionRun,
    advance,
    headed,
)
from chiplog.capabilities.agent_loop.readonly_runtime import accept_readonly_attempt
from chiplog.capabilities.agent_loop.readonly_runtime_contracts import ReadOnlyLineageV2
from chiplog.capabilities.agent_loop.readonly_successor_contracts import ReadOnlySuccessorV2
from chiplog.platform.postgres import PostgresIntegrityError, Record, Snapshot

if TYPE_CHECKING:
    from chiplog.composition.postgres_execution import PostgresExecutionRuntime

SCHEMA = "chiplog.readonly.successor.v2"


def _edge(snapshot: Snapshot, run_id: str) -> ReadOnlySuccessorV2 | None:
    found = []
    for batch in snapshot.batches:
        for record in batch.records:
            if record.schema_id != SCHEMA:
                continue
            value = ReadOnlySuccessorV2.model_validate_json(record.canonical_bytes)
            if record.owner != "agent_loop" or record.record_id != value.edge_id:
                raise PostgresIntegrityError("read-only successor identity differs")
            if value.successor_run_id == run_id:
                ids = {member.record_id for member in batch.records}
                if not {value.successor_active_head, value.accepted_head} <= ids:
                    raise PostgresIntegrityError("read-only successor batch is incomplete")
                found.append(value)
    if len(found) > 1:
        raise PostgresIntegrityError("read-only successor has multiple selected sources")
    return found[0] if found else None


def selected_turn_origin(
    runtime: PostgresExecutionRuntime, snapshot: Snapshot, run: ExecutionRun, turn_index: int
) -> str:
    """Resolve only inherited prefixes; a new Turn always belongs to its actual Run."""
    runs = {row.head: row for row in runtime._runs(snapshot)}
    seen: set[str] = set()
    while True:
        if run.run_id in seen:
            raise PostgresIntegrityError("read-only successor ancestry cycle")
        seen.add(run.run_id)
        edge = _edge(snapshot, run.run_id)
        if edge is None or turn_index >= edge.inherited_turn_count:
            return run.run_id
        source = runs.get(edge.source_run_head)
        initial = runs.get(edge.successor_active_head)
        if (
            source is None or source.run_id != edge.source_run_id
            or initial is None or initial.run_id != run.run_id
            or len(source.turns) != edge.inherited_turn_count
            or initial.turns != source.turns
            or run.turns[:edge.inherited_turn_count] != source.turns
        ):
            raise PostgresIntegrityError("inherited Turn differs from selected successor source")
        run = source


def require_successor_authority(
    runtime: PostgresExecutionRuntime, snapshot: Snapshot, run_id: str, original_call_id: str,
    *, for_successor_admission: bool = False,
) -> ExecutionRun:
    from chiplog.composition.postgres_readonly import _selected

    edge = _edge(snapshot, run_id)
    lineage = _selected(snapshot, original_call_id)
    rows = [row for row in runtime._runs(snapshot) if row.run_id == run_id]
    if (
        edge is None or edge.original_call_id != original_call_id
        or lineage is None or not lineage.attempts
        or lineage.lineage_id != edge.lineage_id
        or lineage.attempts[-1].run_id != run_id
        or not rows or rows[-1].state not in (
            ("ACTIVE", "SUSPENDED") if for_successor_admission else ("ACTIVE",)
        )
    ):
        raise LoopHold("read-only successor execution authority is absent")
    return rows[-1]


async def retry_readonly_in_successor(
    runtime: PostgresExecutionRuntime,
    peer: str,
    original_call_id: str,
    expected_pending_head: str,
    expected_predecessor_run_head: str,
    successor_run_id: str,
) -> ReadOnlyLineageV2:
    from chiplog.composition.postgres_readonly import (
        SCHEMA as LINEAGE_SCHEMA,
    )
    from chiplog.composition.postgres_readonly import (
        _call,
        _finish_accepted,
        _selected,
    )

    async with runtime._lane:
        snapshot = runtime._snapshot_for_peer(peer)
        replay = _edge(snapshot, successor_run_id)
        if replay is not None:
            if (
                replay.original_call_id != original_call_id
                or replay.pending_head != expected_pending_head
                or replay.source_run_head != expected_predecessor_run_head
            ):
                raise LoopRejected("successor replay differs from selected admission")
            selected = _selected(snapshot, original_call_id)
            if selected is None:
                raise PostgresIntegrityError("successor lineage is absent")
            if (
                selected.selection.tenant_id != runtime.tenant_id
                or selected.selection.principal_id != runtime.principal_id
            ):
                raise PermissionError("read-only successor belongs to another user")
            return selected

        def admission(current: Snapshot) -> tuple[ReadOnlyLineageV2, ExecutionRun]:
            previous = _selected(current, original_call_id)
            if previous is None or previous.head != expected_pending_head:
                raise LoopRejected("stale selected read-only pending head")
            if previous.state != "RETRY_PENDING":
                raise LoopHold("successor requires a definite retryable pending outcome")
            run_id = previous.attempts[-1].run_id
            _call(
                runtime, current, original_call_id, executing_run_id=run_id,
                for_successor_admission=True,
            )
            all_runs = runtime._runs(current)
            rows = [row for row in all_runs if row.run_id == run_id]
            if not rows or rows[-1].head != expected_predecessor_run_head:
                raise LoopRejected("stale successor predecessor Run")
            source = rows[-1]
            from chiplog.composition.postgres_run_recovery import _crossed_work_closed

            _crossed_work_closed(runtime, current, source)
            if any(row.run_id == successor_run_id for row in all_runs):
                raise LoopRejected("successor Run identity already exists")
            if source.root_binding != "NOT_APPLICABLE":
                raise LoopHold("scheduler successor requires scheduler admission")
            # Check exactly the selected seal; never omit an unfinished sibling.
            for index in range(len(source.turns)):
                turn = source.turns[index]
                if turn.initialized_calls is None:
                    continue
                if any(ref.subject_id == original_call_id for ref in turn.initialized_calls):
                    runtime._closed_call_results(
                        current, source, index, pending_readonly_call=original_call_id
                    )
                    return previous, source
            raise LoopRejected("pending original call is absent from the selected seal")

        previous, source = admission(snapshot)
        superseded = advance(source, "ReadOnlySuccessorSelected", state="SUPERSEDED")
        successor = headed(source.model_copy(update={
            "run_id": successor_run_id, "head": "pending", "predecessor": None,
            "state": "ACTIVE",
            "worker_session": runtime.current_worker(), "event": "ReadOnlySuccessorActivated",
        }), "loop:")
        accepted = accept_readonly_attempt(
            previous, run_id=successor_run_id, expected_head=previous.head
        )
        edge = ReadOnlySuccessorV2(
            edge_id=previous.lineage_id + ":successor:" + str(accepted.attempts_consumed),
            original_call_id=original_call_id, lineage_id=previous.lineage_id,
            pending_head=previous.head, source_run_id=source.run_id, source_run_head=source.head,
            successor_run_id=successor_run_id, successor_active_head=successor.head,
            accepted_head=accepted.head, inherited_turn_count=len(source.turns),
        )

        def guard(current: Snapshot) -> None:
            admission(current)

        result = await runtime.publish_execution_batch(
            peer, "readonly.successor.v2", previous.head, snapshot.head,
            (
                Record(
                    superseded.head, "agent_loop", superseded.schema_id,
                    superseded.canonical_bytes(),
                ),
                Record(
                    successor.head, "agent_loop", successor.schema_id, successor.canonical_bytes()
                ),
                Record(edge.edge_id, "agent_loop", edge.schema_id, edge.canonical_bytes()),
                Record(accepted.head, "agent_loop", LINEAGE_SCHEMA, accepted.canonical_bytes()),
            ),
            guard,
        )
        if result.disposition == "REPLAY":
            selected = _selected(runtime._snapshot_for_peer(peer), original_call_id)
            if selected is None:
                raise PostgresIntegrityError("replayed successor lineage is absent")
            return selected
        return await _finish_accepted(runtime, peer, accepted)
