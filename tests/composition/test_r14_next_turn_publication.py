"""Mounted selected native next Turn and replay."""

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from chiplog.architecture.r7_runtime import (
    R14_EXECUTION_NEXT_TURN_V27_PRODUCTION_MANIFEST,
)
from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.composition.common_cli_execution_runtime import open_common_cli_execution_runtime
from chiplog.composition.r14_loop_history import read_execution_history
from chiplog.composition.r14_native_recovery_cut import NativeRecoveryCutSource
from chiplog.composition.r14_next_turn_records import (
    NEXT_TURN_PUBLICATION,
    validate_selected_next_turn,
)
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from tests.support.native_cancelled import native_response, selected_native_cancelled


@pytest.mark.asyncio
async def test_selected_next_turn_restarts_and_replays(tmp_path: Path):
    database = tmp_path / "next-turn.sqlite"
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "custody"
    )
    source = NativeRecoveryCutSource()
    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        responses=(native_response(),),
        manifest=R14_EXECUTION_NEXT_TURN_V27_PRODUCTION_MANIFEST,
        recovery_cut_source=source,
    ) as runtime:
        await runtime.publish_native_recovery_profile()
        await selected_native_cancelled(runtime, bound=True)
        await runtime.publish_native_one_call_cancelled_accounting()
        await runtime.publish_execution_accounting_readiness()
        run = read_execution_history(runtime).records[-1]
        with pytest.raises(LoopRejected, match="stale or foreign"):
            await runtime.start_next_execution_turn(run.run_id, "stale-head")
        assert read_execution_history(runtime).records[-1] == run
        next_run = await runtime.start_next_execution_turn(run.run_id, run.head)
        assert next_run.event == "TurnStarted"
        assert tuple(turn.ordinal for turn in next_run.turns) == (1, 2)
        assert next_run.turns[-1].state == "PREPARING"
        assert read_execution_history(runtime).records[-1] == next_run
        assert await runtime.start_next_execution_turn(run.run_id, run.head) == next_run
        with pytest.raises(LoopRejected, match="changed replay request"):
            await runtime.start_next_execution_turn(run.run_id, next_run.head)
        selected = [
            json.loads(raw)
            for _, _, raw in runtime._loop_decisions().entries()
            if json.loads(raw).get("operation_kind") == NEXT_TURN_PUBLICATION
        ]
        assert len(selected) == 1
        batch = runtime._publication(selected[0])
        forged_record = replace(batch.records[0], fingerprint="0" * 64)
        with runtime._authority_gate().hold(), pytest.raises(ValueError):
            validate_selected_next_turn(
                runtime, selected[0], replace(batch, records=(forged_record,))
            )
        assert read_execution_history(runtime).records[-1] == next_run
    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        manifest=R14_EXECUTION_NEXT_TURN_V27_PRODUCTION_MANIFEST,
        recovery_cut_source=source,
    ) as runtime:
        assert read_execution_history(runtime).records[-1] == next_run
        assert await runtime.start_next_execution_turn(run.run_id, run.head) == next_run


@pytest.mark.asyncio
async def test_foreign_evidence_holds_next_turn_without_publication(tmp_path: Path):
    database = tmp_path / "next-turn-hold.sqlite"
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "custody-hold"
    )
    source = NativeRecoveryCutSource()
    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        responses=(native_response(),),
        manifest=R14_EXECUTION_NEXT_TURN_V27_PRODUCTION_MANIFEST,
        recovery_cut_source=source,
    ) as runtime:
        await runtime.publish_native_recovery_profile()
        await selected_native_cancelled(runtime, bound=True)
        await runtime.publish_native_one_call_cancelled_accounting()
        await runtime.publish_execution_accounting_readiness()
        run = read_execution_history(runtime).records[-1]
    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        manifest=R14_EXECUTION_NEXT_TURN_V27_PRODUCTION_MANIFEST,
        recovery_cut_source=source,
    ) as runtime:
        runtime.provision_retained("foreign-next-turn-evidence", b"foreign evidence")
        await runtime.allocate_receipt("foreign-next-turn-evidence")
        assert source.collect(runtime) is None
        with pytest.raises(LoopRejected, match="HOLD"):
            await runtime.start_next_execution_turn(run.run_id, run.head)
        assert read_execution_history(runtime).records[-1] == run
        assert not any(
            json.loads(raw).get("operation_kind") == NEXT_TURN_PUBLICATION
            for _, _, raw in runtime._loop_decisions().entries()
        )


@pytest.mark.asyncio
async def test_selected_next_turn_physical_record_tamper_blocks_restart(tmp_path: Path):
    database = tmp_path / "next-turn-physical-tamper.sqlite"
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "custody-tamper"
    )
    source = NativeRecoveryCutSource()
    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        responses=(native_response(),),
        manifest=R14_EXECUTION_NEXT_TURN_V27_PRODUCTION_MANIFEST,
        recovery_cut_source=source,
    ) as runtime:
        await runtime.publish_native_recovery_profile()
        await selected_native_cancelled(runtime, bound=True)
        await runtime.publish_native_one_call_cancelled_accounting()
        await runtime.publish_execution_accounting_readiness()
        cut = source.collect(runtime)
        assert cut is not None
        run = cut.complete_ordered_run_lineage[-1]
        next_run = await runtime.start_next_execution_turn(run.run_id, run.head)
        assert read_execution_history(runtime).records[-1] == next_run
    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        manifest=R14_EXECUTION_NEXT_TURN_V27_PRODUCTION_MANIFEST,
        recovery_cut_source=source,
    ) as runtime:
        assert read_execution_history(runtime).records[-1] == next_run
    with sqlite3.connect(database) as connection:
        selected = connection.execute(
            "SELECT p.record_ids, r.canonical_bytes FROM publications AS p "
            "JOIN records AS r ON r.tenant_id=p.tenant_id AND r.record_id=p.record_ids "
            "WHERE p.operation_kind=?",
            (NEXT_TURN_PUBLICATION,),
        ).fetchall()
        assert len(selected) == 1
        record_id, original = selected[0]
        assert record_id == next_run.head
        assert original == next_run.canonical_bytes()
        damaged = original[:-1] + (b" " if original[-1:] != b" " else b"\n")
        assert connection.execute(
            "UPDATE records SET canonical_bytes=? WHERE record_id=?",
            (damaged, record_id),
        ).rowcount == 1
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT canonical_bytes FROM records WHERE record_id=?", (record_id,)
        ).fetchone() == (damaged,)
        assert connection.execute(
            "SELECT count(*) FROM publications WHERE operation_kind=?",
            (NEXT_TURN_PUBLICATION,),
        ).fetchone() == (1,)
    with pytest.raises((ValueError, RuntimeError)):
        async with open_common_cli_execution_runtime(
            database,
            resources=resources,
            manifest=R14_EXECUTION_NEXT_TURN_V27_PRODUCTION_MANIFEST,
            recovery_cut_source=source,
        ):
            pytest.fail("tampered selected next Turn reopened")
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT count(*) FROM publications WHERE operation_kind=?",
            (NEXT_TURN_PUBLICATION,),
        ).fetchone() == (1,)
