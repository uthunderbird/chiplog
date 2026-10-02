"""Read-only native-v2 source facts for the first R14 cancellation slice.

The collector intentionally stops before ``ExecutionRecoveryCut`` construction:
the installed assembly has no authenticated frontier, frozen bindings, sources,
or recovery-owner route for that broader value.  A returned value is evidence for
a later reader only and cannot authorize preparation or publication.
"""

from __future__ import annotations

from base64 import b64decode
from dataclasses import dataclass

from chiplog.capabilities.agent_loop.call_acceptance_contracts import (
    CallSubjectHead,
    CancelledBeforeAcceptRecord,
    NotExecutedCallResultRecord,
)
from chiplog.capabilities.agent_loop.execution_contracts import ConsequentialToolCall
from chiplog.capabilities.agent_loop.recovery_contracts import NotApplicable, Present
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import InitializedCall
from chiplog.capabilities.agent_loop.recovery_frontier_registry_contracts import (
    RECOVERY_FRONTIER_REGISTRY_SCHEMA,
    decode_native_one_call_cancellation_frontier_registry_v1,
    execution_native_one_call_cancellation_frontier_registry_v1,
    frontier_registry_reference,
)
from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
from chiplog.composition.r14_execution_fanout_records import reference
from chiplog.composition.r14_loop_history import read_execution_recovery_history


@dataclass(frozen=True, slots=True)
class R14NativeCancelledCallSourceFacts:
    """One fully authenticated, pre-accept native cancellation; never a cut."""

    tenant_id: str
    database_id: str
    frontier_registry_bytes: bytes
    frontier_registry_reference: CallSubjectHead
    complete_ordered_native_response_seals: tuple[CallSubjectHead, ...]
    selected_response_seal: CallSubjectHead
    physical_response_seal: CallSubjectHead
    selected_initialized: CallSubjectHead
    physical_initialized: CallSubjectHead
    selected_sealed_run: CallSubjectHead
    physical_sealed_run: CallSubjectHead
    original_call_id: str
    initialized: CallSubjectHead
    cancellation_terminal: CancelledBeforeAcceptRecord
    not_executed_result: NotExecutedCallResultRecord


class R14NativeCancelledCallSource:
    """Collect only one exact selected native ``request_self_effect`` cancellation.

    ``None`` is the explicit hold result.  The API has no caller-selected locator:
    it always enumerates the complete mounted selected/physical history itself.
    """

    def __init__(self, runtime: CommonCliExecutionRuntime) -> None:
        if type(runtime) is not CommonCliExecutionRuntime:
            raise TypeError("native cancellation source requires the installed CLI runtime")
        self._runtime = runtime

    def collect(self) -> R14NativeCancelledCallSourceFacts | None:
        gate = self._runtime._authority_gate()
        try:
            with gate.hold():
                self._runtime._check_database_identity()
                facts = self._collect_held()
                self._runtime._check_database_identity()
                return facts
        except (OSError, RuntimeError, TypeError, ValueError):
            return None

    def _collect_held(self) -> R14NativeCancelledCallSourceFacts | None:
        snapshot, inventory, legacy_seals, seals, cancellations, acceptances = (
            read_execution_recovery_history(self._runtime)
        )
        # This is a full enumeration predicate, including the one-seal case.
        ordered_seals = tuple(
            reference(
                item.proposal.fan_out.response_seal.response_seal_id,
                item.proposal.fan_out.response_seal,
            )
            for item in seals
        )
        if legacy_seals or len(seals) != 1 or len(ordered_seals) != 1:
            return None
        seal = seals[0]
        initialized_records = seal.proposal.fan_out.initialized_records
        if len(initialized_records) != 1 or len(inventory.ordered_calls) != 1:
            return None
        # The registry is a fixed profile, rather than a registry shaped by this
        # selected history.  Decode it anyway so this seam never turns a local
        # factory call into an unverified profile assertion.
        registry = execution_native_one_call_cancellation_frontier_registry_v1()
        registry_raw = registry.canonical_bytes()
        registry_ref = frontier_registry_reference(registry)
        try:
            if (
                decode_native_one_call_cancellation_frontier_registry_v1(
                    RECOVERY_FRONTIER_REGISTRY_SCHEMA,
                    registry_raw,
                    expected_reference=registry_ref,
                )
                != registry
            ):
                return None
        except ValueError:
            return None
        initialized_record = initialized_records[0]
        try:
            tool = ConsequentialToolCall.model_validate_json(
                b64decode(initialized_record.call.canonical_call_base64, validate=True)
            )
        except ValueError:
            return None
        if (
            tool.canonical_bytes()
            != b64decode(initialized_record.call.canonical_call_base64, validate=True)
            or tool.tool != "request_self_effect"
            or initialized_record.call.classification != "CONSEQUENTIAL"
            or not isinstance(initialized_record.call.retry_lineage, NotApplicable)
        ):
            return None
        row = inventory.ordered_calls[0]
        initialized = reference(initialized_record.original_call_id, initialized_record)
        selected_response_seal = ordered_seals[0]
        selected_sealed_run = reference(seal.proposal.sealed_run.run_id, seal.proposal.sealed_run)
        physical_run = next(
            (
                run
                for run in snapshot.records
                if run.head == seal.proposal.sealed_run.head
            ),
            None,
        )
        if physical_run is None:
            return None
        physical_sealed_run = reference(physical_run.run_id, physical_run)
        physical_turn = physical_run.turns[-1] if physical_run.turns else None
        physical_response_seal = physical_turn.response_seal if physical_turn is not None else None
        physical_initialized_calls = (
            physical_turn.initialized_calls if physical_turn is not None else None
        )
        if (
            row.original_call_id != initialized_record.original_call_id
            or row.initialized != initialized
            or row.initialized_record != initialized_record
            or not isinstance(row.acceptance, InitializedCall)
            or row.acceptance.initialized != initialized.revision
            or len(acceptances) != 0
            or len(cancellations) != 1
            or not isinstance(row.terminal, Present)
            or selected_sealed_run != physical_sealed_run
            or physical_response_seal != selected_response_seal
            or physical_initialized_calls != (initialized,)
        ):
            return None
        cancellation = cancellations[0]
        terminal, result = cancellation.proposal.terminal, cancellation.proposal.result
        terminal_ref = reference(terminal.terminal_id, terminal)
        if (
            cancellation.request.original_call_id != initialized_record.original_call_id
            or cancellation.request.initialized != initialized
            or cancellation.request.initialized_record != initialized_record
            or cancellation.proposal.complete_ordered_record_manifest
            != (terminal_ref, reference(result.result_id, result))
            or terminal.original_call_id != initialized_record.original_call_id
            or terminal.initialized != initialized
            or result.original_call_id != initialized_record.original_call_id
            or result.initialized != initialized
            or result.terminal != terminal_ref
            or result.outcome != "NOT_EXECUTED"
            or row.terminal != terminal_ref.revision
            or snapshot.tenant_head != inventory.tenant_commit_sequence
        ):
            return None
        return R14NativeCancelledCallSourceFacts(
            tenant_id=self._runtime._tenant_id,
            database_id=self._runtime._database_identity[0],
            frontier_registry_bytes=registry_raw,
            frontier_registry_reference=registry_ref,
            complete_ordered_native_response_seals=ordered_seals,
            selected_response_seal=selected_response_seal,
            physical_response_seal=physical_response_seal,
            selected_initialized=initialized,
            physical_initialized=physical_initialized_calls[0],
            selected_sealed_run=selected_sealed_run,
            physical_sealed_run=physical_sealed_run,
            original_call_id=initialized_record.original_call_id,
            initialized=initialized,
            cancellation_terminal=terminal,
            not_executed_result=result,
        )
