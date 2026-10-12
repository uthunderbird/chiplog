"""Mounted CLI successor publication and exact replay after runtime reopen."""

import pytest

from chiplog.architecture.r7_runtime import R14_EXECUTION_SUCCESSOR_V38_PRODUCTION_MANIFEST
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.composition.common_cli_execution_runtime import (
    SelectedExecutionReceiptV1,
    open_common_cli_execution_runtime,
)
from chiplog.composition.r14_loop_history import read_execution_history
from chiplog.composition.r14_native_recovery_cut import (
    NativeRecoveryCutSource,
    _current_successor_ingress_occurrences,
)
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.composition.r16_native_inventory import collect_r16_native_inventory
from tests.support.h1_cli_execution import admit_runtime_slot
from tests.support.native_cancelled import native_response, selected_native_cancelled


@pytest.mark.asyncio
async def test_new_authenticated_ingress_selects_and_replays_suspended_run_successor(
    tmp_path,
) -> None:
    database = tmp_path / "successor.sqlite"
    custody = tmp_path / "custody"
    source = NativeRecoveryCutSource()
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1, custody_path=custody)
    manifest = R14_EXECUTION_SUCCESSOR_V38_PRODUCTION_MANIFEST

    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        responses=(native_response(),),
        manifest=manifest,
        recovery_cut_source=source,
    ) as runtime:
        await runtime.publish_native_recovery_profile(version="v4")
        await selected_native_cancelled(runtime)
        await runtime.publish_native_one_call_cancelled_accounting()
        previous = next(
            row
            for row in reversed(read_execution_history(runtime).records)
            if isinstance(row, ExecutionRunRecord)
        )
        suspension = await runtime.suspend_execution(previous.run_id, previous.head, ())
        assert suspension.pair.suspended_run.subject_id == previous.run_id

    successor_resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=custody
    )
    async with open_common_cli_execution_runtime(
        database,
        resources=successor_resources,
        manifest=manifest,
        recovery_cut_source=source,
    ) as runtime:
        request = await admit_runtime_slot(
            runtime, "successor-ingress", b"continue with the follow-up task"
        )
        admitted = runtime._selected_input(request, successor_resources.observe())
        with runtime._authority_gate().hold():
            inventory = collect_r16_native_inventory(runtime)
            selected_commands, selected_records = _current_successor_ingress_occurrences(
                runtime, inventory, request, admitted
            )
            assert len(selected_commands) == 3
            assert len(selected_records) >= 3
            assert _current_successor_ingress_occurrences(
                runtime,
                inventory,
                request.model_copy(update={"identity": request.identity.model_copy(
                    update={"original_ingress_request_fingerprint": "0" * 64}
                )}),
                admitted,
            ) is None
            assert _current_successor_ingress_occurrences(
                runtime,
                inventory,
                request,
                admitted.model_copy(update={"raw_input_bytes": b"forged input"}),
            ) is None
        receipt = await runtime.drive_input(request)
        assert isinstance(receipt, SelectedExecutionReceiptV1)
        assert receipt.selected_run_state == "ACTIVE"
        assert receipt.stable_run_lineage_id != previous.run_id

        successor = next(
            row
            for row in reversed(read_execution_history(runtime).records)
            if isinstance(row, ExecutionRunRecord)
        )
        assert successor.run_id == receipt.stable_run_lineage_id
        assert successor.state == "ACTIVE"
        assert len(successor.turns) == 1
        assert successor.turns[0].state == "PREPARING"
        decisions = runtime._loop_decisions().entries()

    reopened_resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=custody
    )
    async with open_common_cli_execution_runtime(
        database,
        resources=reopened_resources,
        manifest=manifest,
        recovery_cut_source=source,
    ) as reopened:
        replay = await reopened.drive_input(request)
        assert isinstance(replay, SelectedExecutionReceiptV1)
        assert replay.disposition == "EXACT_REPLAY"
        assert replay.stable_run_lineage_id == receipt.stable_run_lineage_id
        assert replay.selected_run_head == receipt.selected_run_head
        assert reopened._loop_decisions().entries() == decisions
