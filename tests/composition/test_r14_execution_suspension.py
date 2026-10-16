"""Mounted suspension admission fails closed before a complete selected cut exists."""

import pytest

from chiplog.architecture.r7_runtime import (
    R14_EXECUTION_SAME_RUN_RESUME_V36_PRODUCTION_MANIFEST,
    R14_EXECUTION_SUSPENSION_V35_PRODUCTION_MANIFEST,
)
from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.composition import r14_execution_suspension as suspension_module
from chiplog.composition.common_cli_execution_runtime import open_common_cli_execution_runtime
from chiplog.composition.r14_loop_history import read_execution_history
from chiplog.composition.r14_native_recovery_cut import NativeRecoveryCutSource
from chiplog.composition.r14_recovery_profile_publication import publish_native_recovery_profile
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform.workspace_snapshot import read_connection
from tests.support.native_cancelled import native_response, selected_native_cancelled


async def test_mounted_suspension_holds_without_authenticated_active_run(tmp_path):
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "custody"
    )
    async with open_common_cli_execution_runtime(
        tmp_path / "suspension.sqlite",
        resources=resources,
        manifest=R14_EXECUTION_SUSPENSION_V35_PRODUCTION_MANIFEST,
    ) as runtime:
        before = runtime._commitment_journal.load(runtime._tenant_id)
        with pytest.raises(LoopRejected, match="complete authenticated suspension cut"):
            await runtime.suspend_execution("missing-run", "missing-head", ())
        assert runtime._commitment_journal.load(runtime._tenant_id) == before
        assert runtime.read_selected_execution_suspension() is None


async def test_mounted_same_run_resume_reopens_the_selected_suspension(tmp_path, monkeypatch):
    database = tmp_path / "resume.sqlite"
    custody = tmp_path / "custody"
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1, custody_path=custody)
    source = NativeRecoveryCutSource()
    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        responses=(native_response(),),
        manifest=R14_EXECUTION_SAME_RUN_RESUME_V36_PRODUCTION_MANIFEST,
        recovery_cut_source=source,
    ) as runtime:
        await publish_native_recovery_profile(runtime, version="v4")
        await selected_native_cancelled(runtime)
        await runtime.publish_native_one_call_cancelled_accounting()
        history = read_execution_history(runtime)
        run = next(row for row in reversed(history.records) if isinstance(row, ExecutionRunRecord))
        recovery_source = runtime._full_recovery_cut_source
        collect_held_calls = []

        class CountingRecoverySource:
            def collect_held(self, actual_runtime):
                collect_held_calls.append(actual_runtime)
                return recovery_source.collect_held(actual_runtime)

            def authenticate_selected_held(self, actual_runtime, evidence, entry):
                return recovery_source.authenticate_selected_held(
                    actual_runtime, evidence, entry
                )

        runtime._full_recovery_cut_source = CountingRecoverySource()
        suspension = await runtime.suspend_execution(run.run_id, run.head, ())
        # Pre-acceptance cancellation still needs the fresh dispatch-resource
        # currentness check in the guard, so retain its second source read.
        assert collect_held_calls == [runtime, runtime]
        runtime._full_recovery_cut_source = recovery_source
        suspended_head = suspension.pair.suspended_run.revision.head

        retained_reads = []
        decode_retained = suspension_module._retained
        suspended_run = next(
            row
            for row in read_execution_history(runtime).records
            if isinstance(row, ExecutionRunRecord) and row.head == suspended_head
        )

        def count_retained_reads(entry):
            retained_reads.append(entry.get("operation_id"))
            return decode_retained(entry)

        with monkeypatch.context() as patch:
            patch.setattr(suspension_module, "_retained", count_retained_reads)
            selected = source._selected_suspension_for_run(runtime, suspended_run)
        assert selected is not None
        assert retained_reads == [suspension.pair.suspension_command_id]

        retained_reads = []
        decode_retained = suspension_module._retained

        def count_retained_reads(entry):
            retained_reads.append(entry.get("operation_id"))
            return decode_retained(entry)

        with monkeypatch.context() as patch:
            patch.setattr(suspension_module, "_retained", count_retained_reads)
            read_execution_history(runtime)
        assert retained_reads == [suspension.pair.suspension_command_id]

        resumed = await runtime.resume_execution(
            run.run_id, suspended_head, b"resume-the-same-run"
        )

        assert resumed.run_id == run.run_id
        assert resumed.state == "ACTIVE"
        assert resumed.predecessor == suspended_head
        assert resumed.turns == run.turns
        assert runtime.read_selected_execution_resume() == resumed

        selected_after_resume = runtime._loop_decisions().entries()
        commitment_after_resume = runtime._commitment_journal.load(runtime._tenant_id)
        with read_connection(runtime._database) as connection:
            physical_after_resume = (
                connection.execute(
                    "SELECT head FROM tenant_heads WHERE tenant_id=?",
                    (runtime._tenant_id,),
                ).fetchall(),
                connection.execute(
                    "SELECT record_id,owner,schema_id,canonical_bytes,commit_sequence "
                    "FROM records WHERE tenant_id=? ORDER BY commit_sequence,record_id",
                    (runtime._tenant_id,),
                ).fetchall(),
            )

        assert (
            await runtime.resume_execution(run.run_id, suspended_head, b"resume-the-same-run")
            == resumed
        )
        assert runtime._loop_decisions().entries() == selected_after_resume
        assert runtime._commitment_journal.load(runtime._tenant_id) == commitment_after_resume

        with pytest.raises(LoopRejected, match="changed replay request"):
            await runtime.resume_execution(run.run_id, suspended_head, b"resume-the-same-run!")
        assert runtime.read_selected_execution_resume() == resumed
        assert runtime._loop_decisions().entries() == selected_after_resume
        assert runtime._commitment_journal.load(runtime._tenant_id) == commitment_after_resume
        with read_connection(runtime._database) as connection:
            physical_after_rejection = (
                connection.execute(
                    "SELECT head FROM tenant_heads WHERE tenant_id=?",
                    (runtime._tenant_id,),
                ).fetchall(),
                connection.execute(
                    "SELECT record_id,owner,schema_id,canonical_bytes,commit_sequence "
                    "FROM records WHERE tenant_id=? ORDER BY commit_sequence,record_id",
                    (runtime._tenant_id,),
                ).fetchall(),
            )
        assert physical_after_rejection == physical_after_resume

    resources_after_reopen = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=custody
    )
    async with open_common_cli_execution_runtime(
        database,
        resources=resources_after_reopen,
        manifest=R14_EXECUTION_SAME_RUN_RESUME_V36_PRODUCTION_MANIFEST,
        recovery_cut_source=source,
    ) as reopened:
        assert reopened.read_selected_execution_resume() == resumed
        assert reopened.read_selected_execution_suspension() == suspension
