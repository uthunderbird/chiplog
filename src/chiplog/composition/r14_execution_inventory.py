"""Complete call inventory across both registered Run/fanout schemas."""

from chiplog.capabilities.agent_loop.call_acceptance_contracts import (
    CallInventorySnapshot,
    CallLifecycleObservation,
)
from chiplog.capabilities.agent_loop.contracts import LoopSnapshot, RunRecord
from chiplog.capabilities.agent_loop.recovery_contracts import Absent
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import (
    ConsequentialAcceptedCall,
    InitializedCall,
)

from .r14_acceptance_v2_contracts import RetainedAcceptancePreparationV2
from .r14_acceptance_v2_records import build_acceptance_envelope
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
    acceptances: tuple[RetainedAcceptancePreparationV2, ...] = (),
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
    return accepted_inventory(
        CallInventorySnapshot(
            tenant_id=tenant,
            tenant_commit_sequence=snapshot.tenant_head,
            ordered_calls=tuple(observations[key] for key in sorted(observations)),
        ),
        acceptances,
    )


def accepted_inventory(
    inventory: CallInventorySnapshot,
    acceptances: tuple[RetainedAcceptancePreparationV2, ...],
) -> CallInventorySnapshot:
    """Fold verified byte graphs; selected provenance and causal cuts belong to history."""
    inventory = CallInventorySnapshot.model_validate_json(inventory.canonical_bytes())
    identities = tuple(row.original_call_id for row in inventory.ordered_calls)
    if identities != tuple(sorted(set(identities))):
        raise ValueError("unordered or duplicate call inventory")
    observations = {row.original_call_id: row for row in inventory.ordered_calls}
    previous_cut = -1
    for retained in acceptances:
        envelope = build_acceptance_envelope(retained)
        if (
            envelope.tenant_id != inventory.tenant_id
            or not previous_cut < envelope.expected_tenant_head < inventory.tenant_commit_sequence
        ):
            raise ValueError("acceptance is not in strictly preceding tenant history")
        previous_cut = envelope.expected_tenant_head
        row = observations.get(envelope.original_call_id)
        binding = retained.loop_request.binding
        if (
            row is None
            or row.initialized_record != retained.loop_request.initialized_record
            or row.initialized != binding.initialized
            or not isinstance(row.acceptance, InitializedCall)
            or row.acceptance.initialized != row.initialized.revision
            or not isinstance(row.terminal, Absent)
        ):
            raise ValueError("acceptance lacks its exact live initialized branch")
        manifest = tuple(
            member.revision for member in retained.loop_proposal.complete_acceptance_manifest
        )
        observations[row.original_call_id] = row.model_copy(
            update={
                "acceptance": ConsequentialAcceptedCall(
                    initialized=row.initialized.revision,
                    accepted=manifest[0],
                    execution_intent=manifest[1],
                    external_effect_intent=manifest[2],
                    complete_acceptance_manifest=manifest,
                )
            }
        )
    return inventory.model_copy(
        update={"ordered_calls": tuple(observations[key] for key in identities)}
    )
