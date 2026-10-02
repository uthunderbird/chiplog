"""Mounted native context trust lineage and no-execution boundary."""

from pathlib import Path

import pytest

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.deployment_trust.native_one_call_scope_contracts import (
    NativeOneCallPolicyV1,
)
from chiplog.composition.common_cli_execution_runtime import open_common_cli_execution_runtime
from chiplog.composition.r14_loop_history import read_execution_history
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.composition.r17_native_one_call_scope import NativeOneCallScopeService
from tests.support.h1_cli_execution import admit_runtime_slot


async def test_native_scope_selected_reopen_and_refuses_execution(tmp_path: Path) -> None:
    database = tmp_path / "scope.sqlite"
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "custody"
    )
    async with open_common_cli_execution_runtime(database, resources=resources) as runtime:
        wire = await admit_runtime_slot(runtime, "context-scope")
        receipt = await runtime.drive_input(wire)
        run = read_execution_history(runtime).records[-1]
        service = NativeOneCallScopeService(runtime)
        issued = service.issue_for_run(run, "native-prepare-once")
        assert service.read_current(issued.anchor) == issued
        assert service.read_historical(issued.anchor) == issued
        from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
            HermeticOutputScopeAnchorV1,
        )

        with pytest.raises(TypeError, match="distinct trust anchor"):
            service.read_historical(
                HermeticOutputScopeAnchorV1(
                    owner_id="deployment_trust",
                    decision=issued.anchor.decision,
                    record_ordinal=1,
                    record_type_id="chiplog.deployment_trust.hermetic_output_scope",
                    schema_id=issued.anchor.schema_id,
                    record=issued.anchor.record,
                    scope_revision=0,
                    predecessor=None,
                    selected_resource_observation_ref=issued.anchor.selected_resource_observation_ref,
                )
            )
        from chiplog.capabilities.deployment_trust.native_one_call_scope_contracts import (
            IssueNativeOneCallContextScopeV1,
        )

        original = issued.scope
        intent = IssueNativeOneCallContextScopeV1(
            database_id=original.database_id,
            scope_id="rival-scope",
            expected_trust_observation=service._observation(),
            expected_scope_predecessor=None,
            expected_revision=0,
            authenticated_cli_ref=original.authenticated_cli_ref,
            admitted_authentication_ref=original.admitted_authentication,
            selected_resource_observation_ref=original.selected_resource_observation_ref,
            worker_session_id=original.worker_session_id,
            planned_run_id=original.planned_run_id,
            planned_run_head=original.planned_run_head,
            planned_prepare_command_id="rival-prepare",
        )
        before = runtime._trust._journal.entries()
        resource_ref = original.selected_resource_observation_ref
        mutations = (
            {"database_id": "wrong-database"},
            {"worker_session_id": "wrong-session"},
            {"expected_trust_observation": original.issuance_trust_observation},
            {
                "selected_resource_observation_ref": resource_ref.model_copy(
                    update={"signed_observation_fingerprint": "0" * 64}
                )
            },
            {"admitted_authentication_ref": issued.anchor.record},
        )
        for mutation in mutations:
            with pytest.raises(LoopRejected):
                service.issue(intent.model_copy(update=mutation))
            assert runtime._trust._journal.entries() == before
        with pytest.raises(LoopRejected, match="already issued"):
            service.issue(intent)
        materializer = runtime._trust._materializer
        row = materializer._connection.execute(
            "SELECT canonical_bytes FROM trust_records WHERE decision_id=? AND ordinal=1",
            (issued.decision_id,),
        ).fetchone()
        materializer._connection.execute(
            "DELETE FROM trust_records WHERE decision_id=? AND ordinal=1", (issued.decision_id,)
        )
        materializer._connection.commit()
        with pytest.raises(RuntimeError, match="materialization"):
            service.read_historical(issued.anchor)
        materializer._connection.execute(
            "INSERT INTO trust_records VALUES (?,1,?)", (issued.decision_id, row[0])
        )
        materializer._connection.commit()
        materializer._connection.execute(
            "INSERT INTO trust_records VALUES (?,2,?)", (issued.decision_id, row[0])
        )
        materializer._connection.commit()
        with pytest.raises(RuntimeError, match="materialization"):
            service.read_historical(issued.anchor)
        materializer._connection.execute(
            "DELETE FROM trust_records WHERE decision_id=? AND ordinal=2", (issued.decision_id,)
        )
        materializer._connection.commit()

        assert issued.scope.planned_run_id == receipt.stable_run_lineage_id
        policy = NativeOneCallPolicyV1.model_validate_json(
            issued.scope.disclosure_policy.canonical_source_bytes
        )
        assert policy.call_count == 1
        assert (
            not policy.send_authorized
            and not policy.execution_authorized
            and not policy.disclosure_authorized
        )
        with pytest.raises(LoopRejected, match="no SEND"):
            service.require_execution_authority(issued.anchor)
        with pytest.raises(LoopRejected, match="already has"):
            service.issue_for_run(run, "native-prepare-once")
        rival = issued.anchor.model_copy(update={"record": issued.anchor.decision})
        with pytest.raises(LoopRejected, match="missing or rival"):
            service.read_historical(rival)
    async with open_common_cli_execution_runtime(database, resources=resources) as runtime:
        assert NativeOneCallScopeService(runtime).read_current(issued.anchor) == issued


async def test_older_current_run_retains_actual_issuance_inventory(tmp_path: Path) -> None:
    database = tmp_path / "older-run.sqlite"
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "custody"
    )
    async with open_common_cli_execution_runtime(database, resources=resources) as runtime:
        first = await admit_runtime_slot(runtime, "first-input")
        await runtime.drive_input(first)
        run = read_execution_history(runtime).records[-1]
        second = await admit_runtime_slot(runtime, "second-input")
        await runtime.drive_input(second)
        service = NativeOneCallScopeService(runtime)
        issued = service.issue_for_run(run, "prepare-first-run")
        assert service.read_current(issued.anchor) == issued
        assert service.read_historical(issued.anchor) == issued
    async with open_common_cli_execution_runtime(database, resources=resources) as runtime:
        assert NativeOneCallScopeService(runtime).read_historical(issued.anchor) == issued
