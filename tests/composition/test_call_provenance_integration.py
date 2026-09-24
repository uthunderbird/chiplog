"""Integration witness for accepted call history across equal ingress bytes."""

from pathlib import Path

from chiplog.capabilities.agent_loop.contracts import BudgetPolicy
from chiplog.capabilities.agent_loop.execution_contracts import (
    ConsequentialToolCall,
    ExecutionContinue,
    SelfEffectArguments,
)
from chiplog.composition.r13_workspace import R13Workspace, _history
from chiplog.composition.r14_call_acceptance_port import (
    CallAcceptanceAdoption,
    CallAcceptanceTarget,
)
from chiplog.composition.r14_call_preview import execution_run_head
from chiplog.composition.r14_loop_history import read_execution_call_history
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.composition.r16_dispatch_runtime import open_execution_dispatch_runtime


async def test_accepted_call_survives_equal_ingress_and_later_sealed_fanout(tmp_path: Path) -> None:
    database = tmp_path / "accepted-provenance.sqlite"
    response = (
        ExecutionContinue(
            kind="Continue",
            tool_calls=(
                ConsequentialToolCall(
                    call_id="effect",
                    tool="request_self_effect",
                    arguments=SelfEffectArguments(payload=b"Plan", bundle_members=("one",)),
                ),
            ),
        )
        .model_dump_json()
        .encode()
    )
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "custody.json"
    )
    async with open_execution_dispatch_runtime(
        database, resources=resources, responses=(response, response)
    ) as runtime:
        first = await runtime.create_execution("hermetic-ingress", "first", "Plan", BudgetPolicy())
        started = await runtime.begin_execution("hermetic-ingress", "first", first.head)
        captured = await runtime.capture_execution("hermetic-ingress", "first", started.head)
        sealed = await runtime.seal_execution("hermetic-ingress", "first", captured.head)
        original = read_execution_call_history(runtime)[1].ordered_calls[0]
        preview = await runtime.preview_call_acceptance(
            "hermetic-ingress",
            CallAcceptanceTarget(
                original_call_id=original.original_call_id,
                initialized=original.initialized,
                current_run=execution_run_head(sealed),
            ),
        )
        accepted = await runtime.accept_call(
            "hermetic-ingress",
            CallAcceptanceAdoption(
                act_id="first-adoption",
                preview_bytes=preview.canonical_bytes(),
            ),
        )
        accepted_row = read_execution_call_history(runtime)[1].ordered_calls[0]
        assert accepted_row.acceptance.kind == "CONSEQUENTIAL_ACCEPTED"
        assert accepted_row.acceptance.external_effect_intent == accepted.external_intent.revision
        second = await runtime.create_execution(
            "hermetic-ingress", "second", "Plan", BudgetPolicy()
        )
        started_second = await runtime.begin_execution("hermetic-ingress", "second", second.head)
        captured_second = await runtime.capture_execution(
            "hermetic-ingress", "second", started_second.head
        )
        sealed_second = await runtime.seal_execution(
            "hermetic-ingress", "second", captured_second.head
        )
        await R13Workspace(runtime).context(sealed_second)
        entries = _history(runtime)
        assert tuple(entry.entry_id for entry in entries) == ("first/ingress", "second/ingress")
        assert tuple(entry.accepted_bytes for entry in entries) == (b"Plan", b"Plan")
        assert entries[0].envelope.sources != entries[1].envelope.sources
        history = read_execution_call_history(runtime)
        rows = history[1].ordered_calls
        assert len(rows) == 2
        selected_first = next(
            row for row in rows if row.original_call_id == original.original_call_id
        )
        selected_second = next(
            row for row in rows if row.original_call_id != original.original_call_id
        )
        assert selected_first == accepted_row
        assert selected_first.acceptance.kind == "CONSEQUENTIAL_ACCEPTED"
        assert selected_second.acceptance.kind == "INITIALIZED"
        assert selected_second.initialized_record.call.original.original_run_id == "second"
        assert selected_first.initialized != selected_second.initialized
        assert accepted.original_call_id == selected_first.original_call_id
        assert selected_first.acceptance.accepted == accepted.accepted.revision
        assert selected_first.acceptance.execution_intent == accepted.execution_intent.revision
    restarted_resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "custody.json"
    )
    async with open_execution_dispatch_runtime(database, resources=restarted_resources) as reopened:
        assert _history(reopened) == entries
        assert read_execution_call_history(reopened) == history
        await R13Workspace(reopened).context(sealed_second)
