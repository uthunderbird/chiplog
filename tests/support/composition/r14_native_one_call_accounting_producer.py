"""Shared builders extracted without changing their behavior."""

from __future__ import annotations

from tests.support.h1_cli_execution import admit_runtime_slot

from chiplog.capabilities.agent_loop.call_acceptance_contracts import (
    CallSubjectHead,
)
from chiplog.capabilities.agent_loop.execution_contracts import (
    ConsequentialToolCall,
    ExecutionContinue,
    SelfEffectArguments,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.composition.common_cli_execution_runtime import (
    CommonCliExecutionRuntime,
)
from chiplog.composition.common_execution_driver_contracts import SelectedExecutionReceiptV1
from chiplog.composition.r14_cancellation_contracts import CancelCallSubmission
from chiplog.composition.r14_execution_fanout_records import reference
from chiplog.composition.r14_loop_history import read_execution_recovery_history


def _response() -> bytes:
    return ExecutionContinue(
        kind="Continue",
        tool_calls=(
            ConsequentialToolCall(
                call_id="effect",
                tool="request_self_effect",
                arguments=SelfEffectArguments(payload=b"effect", bundle_members=("one",)),
            ),
        ),
    ).canonical_bytes()


async def _selected_cancelled(runtime: CommonCliExecutionRuntime) -> None:
    request = await admit_runtime_slot(runtime, "native-accounting")
    initial = await runtime.drive_input(request)
    assert isinstance(initial, SelectedExecutionReceiptV1)
    run_id = initial.stable_run_lineage_id
    started = await runtime.begin_execution(
        "hermetic-ingress", run_id, initial.selected_run_head.head
    )
    captured = await runtime.capture_execution("hermetic-ingress", run_id, started.head)
    sealed = await runtime.seal_execution("hermetic-ingress", run_id, captured.head)
    _, inventory, _, _, _, _ = read_execution_recovery_history(runtime)
    row = inventory.ordered_calls[0]
    await runtime.cancel_execution_call(
        "hermetic-ingress",
        CancelCallSubmission(
            act_id="cancel-native-effect",
            original_call_id=row.original_call_id,
            initialized=reference(row.initialized_record.original_call_id, row.initialized_record),
            current_run=CallSubjectHead(
                subject_id=sealed.run_id,
                revision=Present(head=sealed.head, fingerprint=sealed.digest()),
            ),
        ),
    )
