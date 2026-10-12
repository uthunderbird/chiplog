"""Mounted CLI successor publication and exact replay after runtime reopen."""

import pytest

from chiplog.architecture.r7_runtime import R14_EXECUTION_SUCCESSOR_V38_PRODUCTION_MANIFEST
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.composition.common_cli_execution_runtime import (
    SelectedExecutionReceiptV1,
    open_common_cli_execution_runtime,
)
from chiplog.composition.r14_loop_history import read_execution_history
from chiplog.composition.r14_native_recovery_cut import NativeRecoveryCutSource
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
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
        print(
            "SUCCESSOR_CUSTODY_FRAME_INPUT",
            len(runtime._trust.capture_verified_observation().snapshot_bytes),
        )
        request = await admit_runtime_slot(
            runtime, "successor-ingress", b"continue with the follow-up task"
        )
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
