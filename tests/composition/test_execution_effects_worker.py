"""Actual selected executable history feeds effects worker capture without renewal."""

import json
import sqlite3
from pathlib import Path

import pytest

from chiplog.adapters.driven.effects_broker import EffectsIntegrityError
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy, LoopRejected
from chiplog.capabilities.agent_loop.execution_contracts import (
    ConsequentialToolCall,
    ExecutionContinue,
    SelfEffectArguments,
)
from chiplog.composition.r13 import open_r13_loop
from chiplog.composition.r14_call_dispatch_policy import policy_reference as call_policy
from chiplog.composition.r14_execution_runtime import open_execution_runtime
from chiplog.composition.r14_loop_history import read_execution_call_history
from chiplog.composition.r16_dispatch_inputs import capture_dispatch
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.composition.r16_dispatch_runtime import open_execution_dispatch_runtime
from chiplog.composition.r16_effects import EffectsCurrentWorkerHold, read_materialized_effects


async def test_execution_worker_requires_registered_active_selected_run_and_current_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "worker.sqlite"
    async with open_execution_runtime(database) as runtime:
        created = await runtime.create_execution("hermetic-ingress", "run", "Plan", BudgetPolicy())
        with pytest.raises(EffectsCurrentWorkerHold):
            read_materialized_effects(runtime, runtime._owner_decisions(), run_id="run")
        active = await runtime.begin_execution("hermetic-ingress", "run", created.head)
        cut = read_materialized_effects(runtime, runtime._owner_decisions(), run_id="run")
        assert cut.worker is not None and cut.worker.run == active
        assert cut.worker.fence.run_head == active.head
        assert cut.latest_runs == (active,)
        with monkeypatch.context() as patch:
            patch.setattr(runtime, "_record_schema_variants", ())
            with pytest.raises(EffectsIntegrityError):
                read_materialized_effects(runtime, runtime._owner_decisions(), run_id="run")
    async with open_execution_runtime(database) as reopened:
        assert read_materialized_effects(reopened, reopened._owner_decisions()).latest_runs == (
            active,
        )
        with pytest.raises(EffectsCurrentWorkerHold):
            read_materialized_effects(reopened, reopened._owner_decisions(), run_id="run")


async def test_execution_worker_never_uses_corrupted_physical_run(tmp_path: Path) -> None:
    database = tmp_path / "corrupt.sqlite"
    async with open_execution_runtime(database) as runtime:
        created = await runtime.create_execution("hermetic-ingress", "run", "Plan", BudgetPolicy())
        active = await runtime.begin_execution("hermetic-ingress", "run", created.head)
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE records SET canonical_bytes=? WHERE record_id=?", (b"{}", active.head)
            )
        with pytest.raises(EffectsIntegrityError):
            read_materialized_effects(runtime, runtime._owner_decisions(), run_id="run")


async def test_execution_worker_preserves_legacy_history_and_rejects_legacy_corruption(
    tmp_path: Path,
) -> None:
    database = tmp_path / "mixed.sqlite"
    async with open_r13_loop(database, responses=()) as loop:
        legacy = await loop.create("legacy", "Original request", BudgetPolicy())
    async with open_execution_runtime(database) as runtime:
        created = await runtime.create_execution(
            "hermetic-ingress", "execution", "New request", BudgetPolicy()
        )
        active = await runtime.begin_execution("hermetic-ingress", "execution", created.head)
        cut = read_materialized_effects(runtime, runtime._owner_decisions(), run_id="execution")
        assert cut.worker is not None and cut.worker.run == active
        by_id = {run.run_id: run for run in cut.latest_runs}
        assert set(by_id) == {"legacy", "execution"}
        assert by_id["legacy"].head == legacy.head
        assert by_id["execution"] == active
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE records SET canonical_bytes=? WHERE record_id=?", (b"{}", legacy.head)
            )
        with pytest.raises(EffectsIntegrityError):
            read_materialized_effects(runtime, runtime._owner_decisions(), run_id="execution")


async def test_combined_assembly_uses_original_dispatch_provider_and_selected_execution_history(
    tmp_path: Path,
) -> None:
    database = tmp_path / "combined.sqlite"
    custody = tmp_path / "custody.json"
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1, custody_path=custody)
    response = ExecutionContinue(
        kind="Continue",
        tool_calls=(
            ConsequentialToolCall(
                call_id="effect",
                tool="request_self_effect",
                arguments=SelfEffectArguments(payload=b"\xff\x00exact", bundle_members=("first",)),
            ),
        ),
    )
    async with open_execution_dispatch_runtime(
        database,
        resources=resources,
        responses=(json.dumps(response.model_dump(mode="json")).encode(),),
    ) as runtime:
        assert runtime._require_dispatch_resources() is resources
        assert (
            runtime._supervisor.runtime()._realized_leaves["effects_transport"]
            is resources.require_original_provider()
        )
        created = await runtime.create_execution("hermetic-ingress", "run", "Plan", BudgetPolicy())
        started = await runtime.begin_execution("hermetic-ingress", "run", created.head)
        captured = await runtime.capture_execution("hermetic-ingress", "run", started.head)
        sealed = await runtime.seal_execution("hermetic-ingress", "run", captured.head)
        history = read_execution_call_history(runtime)
        assert len(history[1].ordered_calls) == 1
        assert history[1].ordered_calls[0].initialized_record.call.classification == "CONSEQUENTIAL"
        cut = read_materialized_effects(runtime, runtime._owner_decisions(), run_id="run")
        assert cut.worker is not None and cut.worker.run == sealed
        observed = await runtime._execution_actor("hermetic-ingress")
        with runtime._authority_gate().hold():
            original_call_id = history[1].ordered_calls[0].original_call_id
            captured_call = capture_dispatch(
                runtime, resources, observed, "run", original_call_id=original_call_id
            )
            assert json.loads(captured_call.resources.grant_bytes)[
                "policy"
            ] == call_policy().model_dump(mode="json")
            assert captured_call.resources == resources.observe_call()
            assert captured_call.cut.worker is not None
            assert captured_call.cut.worker.run == sealed
            with pytest.raises(LoopRejected, match="exact selected consequential"):
                capture_dispatch(runtime, resources, observed, "run", original_call_id="foreign")
        assert resources.require_original_provider().transfers == ()
    reopened_resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=custody
    )
    async with open_execution_dispatch_runtime(database, resources=reopened_resources) as reopened:
        assert read_execution_call_history(reopened) == history
        assert read_materialized_effects(reopened, reopened._owner_decisions()).latest_runs == (
            sealed,
        )
        assert reopened_resources.grant_identities == resources.grant_identities
        with pytest.raises(EffectsCurrentWorkerHold):
            read_materialized_effects(reopened, reopened._owner_decisions(), run_id="run")
