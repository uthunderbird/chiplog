"""Actual selected native cancellation builders shared by recovery readers."""

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.execution_contracts import (
    ConsequentialToolCall,
    ExecutionContinue,
    SelfEffectArguments,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.composition.r14_cancellation_contracts import CancelCallSubmission
from chiplog.composition.r14_execution_fanout_records import reference
from chiplog.composition.r14_loop_history import read_execution_recovery_history
from tests.support.h1_cli_execution import admit_runtime_slot


def native_response() -> bytes:
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


async def selected_native_cancelled(runtime, *, bound=True):
    wire = await admit_runtime_slot(runtime, "native-recovery")
    receipt = await runtime.drive_input(wire)
    started = await runtime.begin_execution(
        "hermetic-ingress", receipt.stable_run_lineage_id, receipt.selected_run_head.head
    )
    captured = await runtime.capture_execution(
        "hermetic-ingress", started.run_id, started.head, native_one_call_context=bound
    )
    sealed = await runtime.seal_execution("hermetic-ingress", captured.run_id, captured.head)
    _, inventory, _, _, _, _ = read_execution_recovery_history(runtime)
    row = inventory.ordered_calls[0]
    await runtime.cancel_execution_call(
        "hermetic-ingress",
        CancelCallSubmission(
            act_id="cancel-native-effect",
            original_call_id=row.original_call_id,
            initialized=reference(row.original_call_id, row.initialized_record),
            current_run=CallSubjectHead(
                subject_id=sealed.run_id,
                revision=Present(head=sealed.head, fingerprint=sealed.digest()),
            ),
        ),
    )
