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
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import InitializedCall
from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
from chiplog.composition.r14_execution_fanout_records import reference
from chiplog.composition.r14_loop_history import read_execution_recovery_history


@dataclass(frozen=True, slots=True)
class R14NativeCancelledCallSourceFacts:
    """One fully authenticated, pre-accept native cancellation; never a cut."""

    tenant_id: str
    database_id: str
    complete_ordered_native_response_seals: tuple[CallSubjectHead, ...]
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
        ):
            return None
        row = inventory.ordered_calls[0]
        initialized = reference(initialized_record.original_call_id, initialized_record)
        if (
            row.original_call_id != initialized_record.original_call_id
            or row.initialized != initialized
            or row.initialized_record != initialized_record
            or not isinstance(row.acceptance, InitializedCall)
            or row.acceptance.initialized != initialized.revision
            or len(acceptances) != 0
            or len(cancellations) != 1
            or not isinstance(row.terminal, Present)
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
            complete_ordered_native_response_seals=ordered_seals,
            original_call_id=initialized_record.original_call_id,
            initialized=initialized,
            cancellation_terminal=terminal,
            not_executed_result=result,
        )
