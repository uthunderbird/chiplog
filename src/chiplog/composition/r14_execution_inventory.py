"""Complete call inventory across both registered Run/fanout schemas."""

from chiplog.capabilities.agent_loop.call_acceptance_contracts import (
    CallInventorySnapshot,
    CallLifecycleObservation,
)
from chiplog.capabilities.agent_loop.contracts import LoopSnapshot, RunRecord
from chiplog.capabilities.agent_loop.recovery_contracts import Absent
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import InitializedCall

from .r14_cancellation_contracts import RetainedCancellationPreparation
from .r14_execution_fanout_contracts import RetainedExecutionFanOutPreparation
from .r14_execution_fanout_records import build_envelope, reference
from .r14_execution_transition_records import ExecutionHistorySnapshot
from .r14_fanout_contracts import RetainedFanOutPreparation
from .r14_fanout_records import inventory_from_history


def execution_inventory(
    tenant: str,
    snapshot: ExecutionHistorySnapshot,
    legacy: tuple[RetainedFanOutPreparation, ...],
    cancellations: tuple[RetainedCancellationPreparation, ...],
    executions: tuple[RetainedExecutionFanOutPreparation, ...],
) -> CallInventorySnapshot:
    snapshot = ExecutionHistorySnapshot.model_validate_json(snapshot.canonical_bytes())
    if any(run.tenant != tenant for run in snapshot.records):
        raise ValueError("foreign Run in mixed inventory")
    history = {run.head: index for index, run in enumerate(snapshot.records)}
    if len(history) != len(snapshot.records):
        raise ValueError("duplicate Run head in mixed inventory")
    old = inventory_from_history(
        tenant,
        LoopSnapshot(
            tenant_head=snapshot.tenant_head,
            records=tuple(run for run in snapshot.records if isinstance(run, RunRecord)),
        ),
        legacy,
        cancellations,
    )
    observations = {row.original_call_id: row for row in old.ordered_calls}
    captures: set[str] = set()
    for evidence in executions:
        build_envelope(evidence)
        captured, sealed = evidence.request.captured_run, evidence.proposal.sealed_run
        if (
            captured.head in captures
            or captured.head not in history
            or sealed.head not in history
            or history[captured.head] >= history[sealed.head]
            or snapshot.records[history[captured.head]] != captured
            or snapshot.records[history[sealed.head]] != sealed
        ):
            raise ValueError("execution inventory lacks exact unique captured/sealed history")
        captures.add(captured.head)
        for initialized in evidence.proposal.fan_out.initialized_records:
            identity = initialized.original_call_id
            if identity in observations:
                raise ValueError("duplicate original call across registered schemas")
            ref = reference(identity, initialized)
            observations[identity] = CallLifecycleObservation(
                original_call_id=identity,
                initialized=ref,
                initialized_record=initialized,
                acceptance=InitializedCall(initialized=ref.revision),
                terminal=Absent(),
            )
    return CallInventorySnapshot(
        tenant_id=tenant,
        tenant_commit_sequence=snapshot.tenant_head,
        ordered_calls=tuple(observations[key] for key in sorted(observations)),
    )
