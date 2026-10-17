"""Mounted publication and canonical restart observation of native accounting."""

import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import TypeAdapter

from chiplog.adapters.driven.loop_sqlite import LoopIntegrityError
from chiplog.architecture.r7_runtime import (
    R14_R17_H1_NATIVE_ONE_CALL_ACCOUNTING_V24_PRODUCTION_MANIFEST,
)
from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.composition import r14_native_accounting_history as accounting_history
from chiplog.composition.common_cli_execution_runtime import open_common_cli_execution_runtime
from chiplog.composition.r14_loop_history import (
    read_execution_history,
    read_execution_recovery_history,
)
from chiplog.composition.r14_native_accounting_records import (
    RetainedNativeOneCallAccountingPublication,
)
from chiplog.composition.r14_native_execution_record_source import (
    _fingerprint,
    _R14NativeExecutionRecordSource,
)
from chiplog.composition.r14_native_one_call_accounting_producer import (
    R14NativeOneCallCancelledAccountingV24Producer,
)
from chiplog.composition.r14_native_selected_accounting_source import (
    R14NativeOneCallCancelledFacts,
)
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform._sqlite import LostCommitAcknowledgement
from chiplog.platform.workspace_snapshot import read_connection
from tests.composition.test_r14_native_one_call_accounting_producer import (
    _response,
    _selected_cancelled,
)


async def test_selected_native_accounting_survives_restart(tmp_path: Path) -> None:
    database = tmp_path / "native-accounting.sqlite"
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "custody"
    )
    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        responses=(_response(),),
        manifest=R14_R17_H1_NATIVE_ONE_CALL_ACCOUNTING_V24_PRODUCTION_MANIFEST,
    ) as runtime:
        await _selected_cancelled(runtime)
        before = read_execution_history(runtime).records
        assert before[-1].state == "ACTIVE"
        assert runtime.read_native_one_call_cancelled_accounting() is None
        selected = await runtime.publish_native_one_call_cancelled_accounting()
        assert runtime.read_native_one_call_cancelled_accounting() == selected
        assert read_execution_history(runtime).records == before
    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        manifest=R14_R17_H1_NATIVE_ONE_CALL_ACCOUNTING_V24_PRODUCTION_MANIFEST,
    ) as reopened:
        assert reopened.read_native_one_call_cancelled_accounting() == selected
        assert read_execution_history(reopened).records == before
        assert await reopened.publish_native_one_call_cancelled_accounting() == selected


async def test_native_accounting_refuses_unselected_and_changed_post_ipc_source(
    tmp_path: Path,
) -> None:
    database = tmp_path / "native-accounting-stale.sqlite"
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "custody"
    )
    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        responses=(_response(),),
        manifest=R14_R17_H1_NATIVE_ONE_CALL_ACCOUNTING_V24_PRODUCTION_MANIFEST,
    ) as runtime:
        with pytest.raises(LoopRejected):
            await runtime.publish_native_one_call_cancelled_accounting()
        await _selected_cancelled(runtime)
        original = R14NativeOneCallCancelledAccountingV24Producer.prepare_exchange

        # A changed source after IPC is tested by replacing the acquisition on
        # the outer writer call, while leaving the owner exchange real.
        def stale_after_exchange(self, supplied):
            if getattr(self, "_after_owner_for_test", False):
                return None
            return acquire(self, supplied)

        acquire = R14NativeOneCallCancelledAccountingV24Producer._acquire_held

        async def complete_then_stale(self, facts):
            result = await original(self, facts)
            self._after_owner_for_test = True
            return result

        with (
            patch.object(
                R14NativeOneCallCancelledAccountingV24Producer,
                "prepare_exchange",
                complete_then_stale,
            ),
            patch.object(
                R14NativeOneCallCancelledAccountingV24Producer,
                "_acquire_held",
                stale_after_exchange,
            ),
            pytest.raises(LoopRejected, match="source changed"),
        ):
            await runtime.publish_native_one_call_cancelled_accounting()
        assert runtime.read_native_one_call_cancelled_accounting() is None


@pytest.mark.parametrize("fault", ("before_commit", "after_commit"))
async def test_native_accounting_selected_crash_recovers(
    tmp_path: Path,
    fault: str,
) -> None:
    database = tmp_path / f"native-accounting-{fault}.sqlite"
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "custody"
    )
    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        responses=(_response(),),
        manifest=R14_R17_H1_NATIVE_ONE_CALL_ACCOUNTING_V24_PRODUCTION_MANIFEST,
    ) as runtime:
        await _selected_cancelled(runtime)
        submit = runtime._appender.submit

        async def crash(command):
            return await submit(replace(command, fault=fault))

        failure = RuntimeError if fault == "before_commit" else LostCommitAcknowledgement
        with patch.object(runtime._appender, "submit", side_effect=crash), pytest.raises(failure):
            await runtime.publish_native_one_call_cancelled_accounting()
    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        manifest=R14_R17_H1_NATIVE_ONE_CALL_ACCOUNTING_V24_PRODUCTION_MANIFEST,
    ) as reopened:
        selected = reopened.read_native_one_call_cancelled_accounting()
        assert selected is not None
        assert await reopened.publish_native_one_call_cancelled_accounting() == selected


async def test_native_accounting_rejects_changed_physical_record(tmp_path: Path) -> None:
    database = tmp_path / "native-accounting-physical.sqlite"
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "custody"
    )
    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        responses=(_response(),),
        manifest=R14_R17_H1_NATIVE_ONE_CALL_ACCOUNTING_V24_PRODUCTION_MANIFEST,
    ) as runtime:
        await _selected_cancelled(runtime)
        selected = await runtime.publish_native_one_call_cancelled_accounting()
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE records SET canonical_bytes=? WHERE tenant_id=? AND record_id=?",
                (b"{}", runtime._tenant_id, selected.record_id),
            )
        with pytest.raises(LoopIntegrityError):
            runtime.read_native_one_call_cancelled_accounting()


@pytest.mark.parametrize("mutation", ("omit", "rival"))
async def test_native_accounting_rejects_incomplete_or_rival_source_manifest(
    tmp_path: Path,
    mutation: str,
) -> None:
    database = tmp_path / f"native-accounting-{mutation}.sqlite"
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "custody"
    )
    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        responses=(_response(),),
        manifest=R14_R17_H1_NATIVE_ONE_CALL_ACCOUNTING_V24_PRODUCTION_MANIFEST,
    ) as runtime:
        await _selected_cancelled(runtime)
        collect = _R14NativeExecutionRecordSource.collect_held

        def changed(self, selected, stamp):
            source = collect(self, selected, stamp)
            assert source is not None
            members = source.ordered_members
            if mutation == "omit":
                members = members[:-1]
            else:
                members = (*members[:-1], replace(members[-1], record_id="result:rival"))
            fingerprint = _fingerprint(
                source.tenant_id,
                source.source_stamp,
                source.original_prepared_run,
                source.selected_response_seal,
                source.initialized,
                source.terminal,
                source.result,
                source.complete_ordered_run_lineage,
                members,
            )
            return replace(source, ordered_members=members, fingerprint=fingerprint)

        with (
            patch.object(_R14NativeExecutionRecordSource, "collect_held", changed),
            pytest.raises(LoopRejected, match="owner preparation is unavailable"),
        ):
            await runtime.publish_native_one_call_cancelled_accounting()
        assert runtime.read_native_one_call_cancelled_accounting() is None


@pytest.mark.parametrize("mutation", ("decision", "response", "issuance"))
async def test_native_accounting_history_rejects_rival_prepare_projection(
    tmp_path: Path,
    mutation: str,
) -> None:
    database = tmp_path / f"native-accounting-prepare-{mutation}.sqlite"
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "custody"
    )
    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        responses=(_response(),),
        manifest=R14_R17_H1_NATIVE_ONE_CALL_ACCOUNTING_V24_PRODUCTION_MANIFEST,
    ) as runtime:
        await _selected_cancelled(runtime)
        await runtime.publish_native_one_call_cancelled_accounting()
        snapshot, _inventory, _legacy, fanouts, cancellations, _acceptances = (
            read_execution_recovery_history(runtime)
        )
        with runtime._authority_gate().hold():
            entry = next(
                item
                for _, _, raw in runtime._loop_decisions().entries()
                if (item := json.loads(raw)).get("native_one_call_accounting_evidence")
            )
            command = runtime._publication(entry)
            evidence = RetainedNativeOneCallAccountingPublication.model_validate_json(
                entry["native_one_call_accounting_evidence"]
            )
            adapter = TypeAdapter(R14NativeOneCallCancelledFacts)
            selected = adapter.validate_json(evidence.selected_source_bytes)
            prepare = selected.source.original_prepare
            if mutation == "decision":
                bad_prepare = replace(prepare, selected_prepare_decision_id="decision:rival")
            elif mutation == "response":
                bad_prepare = replace(prepare, selected_response_seal=prepare.prepared_run)
            else:
                bad_prepare = replace(prepare, workspace_issuance_bytes=b"{}")
            bad_selected = replace(
                selected,
                source=replace(selected.source, original_prepare=bad_prepare),
            )
            bad_evidence = evidence.model_copy(
                update={"selected_source_bytes": adapter.dump_json(bad_selected)}
            )
            with (
                patch.object(
                    accounting_history,
                    "retained_selected_native_accounting",
                    return_value=bad_evidence,
                ),
                read_connection(runtime._database) as connection,
                pytest.raises(ValueError, match="original Prepare"),
            ):
                accounting_history.decode_selected_native_accounting(
                    runtime,
                    connection,
                    command,
                    entry,
                    list(fanouts),
                    list(cancellations),
                    list(snapshot.records),
                )
