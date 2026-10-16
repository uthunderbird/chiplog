import asyncio
from pathlib import Path

import pytest

from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy, LoopHold, LoopRejected
from chiplog.composition.postgres_effects import (
    commit_effect_send,
    emit_effect,
    reconcile_effect,
    resolve_original_effect,
)
from chiplog.composition.postgres_execution import PostgresExecutionRuntime
from chiplog.composition.postgres_readonly_successor import retry_readonly_in_successor
from chiplog.composition.postgres_run_recovery import (
    abort_execution,
    resume_execution,
    suspend_execution,
)
from chiplog.platform.postgres import PostgresServer
from tests.composition.test_postgres_effects import IDENTITY, PEER, setup_effect
from tests.composition.test_postgres_execution import SECRET, bootstrap, opened, origin
from tests.composition.test_postgres_model_replacement import RESPONSE, PausedModel
from tests.composition.test_postgres_planning import verifier
from tests.composition.test_postgres_readonly import opened as readonly_opened
from tests.composition.test_postgres_readonly import selected_call
from tests.composition.test_postgres_readonly_successor import unavailable
from tests.support.deployment_gate import signature


@pytest.mark.parametrize("terminate", [False, True])
async def test_original_effect_recovers_after_suspend_or_abort_without_resend(
    postgres_server: PostgresServer, tmp_path: Path, terminate: bool,
) -> None:
    runtime, binding, original, _, current, request = await setup_effect(
        postgres_server, tmp_path, "LOST_RESPONSE_AFTER_EFFECT"
    )
    runtime.import_entitlement(current, signature(current))
    await commit_effect_send(runtime, binding, PEER, original, request)
    claim = await emit_effect(runtime, binding, PEER, original)
    source = runtime.execution_record("run")
    assert source.original_obligations and source.no_retry_references
    transition = abort_execution if terminate else suspend_execution
    stopped = await transition(runtime, PEER, "run", source.head)
    fresh = PostgresExecutionRuntime(
        runtime.database, "t", SECRET, **IDENTITY, model=HermeticModel((RESPONSE,)),
        origin=origin(), effects_binding=binding, authenticate_entitlement=verifier,
    )
    if not terminate:
        before = fresh.snapshot()
        with pytest.raises(LoopHold):
            await resume_execution(fresh, PEER, "run", stopped.head)
        assert fresh.snapshot() == before
    await reconcile_effect(fresh, binding, PEER, original)
    closure = await resolve_original_effect(fresh, binding, PEER, original)
    assert closure.original_call_id == original
    if terminate:
        assert fresh.execution_record("run") == stopped
        assert stopped.state == "ABORTED"
        with pytest.raises(LoopRejected):
            await fresh.continue_execution(PEER, "run", stopped.head)
    else:
        resumed = await resume_execution(fresh, PEER, "run", stopped.head)
        assert resumed.state == "ACTIVE" and resumed.turns == source.turns
        assert resumed.original_obligations == source.original_obligations
        assert resumed.no_retry_references == source.no_retry_references
        next_turn = await fresh.continue_execution(PEER, "run", resumed.head)
        await fresh.capture_execution(PEER, "run", next_turn.head)
    assert await emit_effect(fresh, binding, PEER, original) == claim
    assert len(binding.provider.transfers) == 1


async def test_resume_preserves_unclaimed_effect_as_actionable_pending_work(
    postgres_server: PostgresServer, tmp_path: Path,
) -> None:
    runtime, binding, original, _, current, request = await setup_effect(
        postgres_server, tmp_path, "CONFIRM"
    )
    source = runtime.execution_record("run")
    suspended = await suspend_execution(runtime, PEER, "run", source.head)
    with pytest.raises(LoopHold):
        await commit_effect_send(runtime, binding, PEER, original, request)
    resumed = await resume_execution(runtime, PEER, "run", suspended.head)
    assert not resumed.original_obligations and not resumed.no_retry_references
    with pytest.raises(LoopHold):
        await runtime.continue_execution(PEER, "run", resumed.head)
    runtime.import_entitlement(current, signature(current))
    await commit_effect_send(runtime, binding, PEER, original, request)
    await emit_effect(runtime, binding, PEER, original)
    assert len(binding.provider.transfers) == 1


@pytest.mark.parametrize("after_successor", [False, True])
async def test_suspended_readonly_source_selects_active_successor_with_same_counter(
    postgres_server: PostgresServer, monkeypatch: pytest.MonkeyPatch, after_successor: bool,
) -> None:
    database, runtime, original, _ = await selected_call(postgres_server)
    with monkeypatch.context() as patched:
        patched.setattr(runtime, "read_history_snapshot", unavailable)
        pending = await runtime.execute_readonly_call(PEER, original)
        if after_successor:
            pending = await retry_readonly_in_successor(
                runtime, PEER, original, pending.head,
                runtime.execution_record("run").head, "first-successor",
            )
    source = runtime.execution_record(pending.attempts[-1].run_id)
    suspended = await suspend_execution(runtime, PEER, source.run_id, source.head)
    runtime = readonly_opened(database, HermeticModel())
    terminal = await retry_readonly_in_successor(
        runtime, PEER, original, pending.head, suspended.head, "next-successor"
    )
    assert terminal.state == "SUCCEEDED"
    assert terminal.attempts_consumed == pending.attempts_consumed + 1
    assert terminal.lineage_id == pending.lineage_id and terminal.selection == pending.selection
    successor = runtime.execution_record("next-successor")
    assert successor.state == "ACTIVE"
    continued = await runtime.continue_execution(PEER, successor.run_id, successor.head)
    assert continued.turns[-1].ordinal == 2


async def test_abort_interrupts_paused_model_without_waiting_for_lane_or_allowing_start(
    postgres_server: PostgresServer,
) -> None:
    database = await bootstrap(postgres_server)
    model = PausedModel()
    runtime = opened(database, model)
    created = await runtime.create_execution(PEER, "run", "Hi", BudgetPolicy())
    started = await runtime.begin_execution(PEER, "run", created.head)
    task = asyncio.create_task(runtime.capture_execution(PEER, "run", started.head))
    await asyncio.wait_for(model.entered.wait(), timeout=300)
    emitted = runtime.execution_record("run")
    try:
        stopped = await asyncio.wait_for(
            abort_execution(runtime, PEER, "run", emitted.head), timeout=300
        )
    finally:
        model.release.set()
    with pytest.raises(LoopHold, match="ACTIVE"):
        await task
    assert runtime.execution_record("run") == stopped and stopped.state == "ABORTED"
    assert model.requests == []


async def test_resume_keeps_readonly_pending_and_does_not_reset_its_counter(
    postgres_server: PostgresServer, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, runtime, original, model = await selected_call(postgres_server)
    with monkeypatch.context() as patched:
        patched.setattr(runtime, "read_history_snapshot", unavailable)
        pending = await runtime.execute_readonly_call(PEER, original)
    suspended = await suspend_execution(
        runtime, PEER, "run", runtime.execution_record("run").head
    )
    resumed = await resume_execution(runtime, PEER, "run", suspended.head)
    with pytest.raises(LoopHold):
        await runtime.continue_execution(PEER, "run", resumed.head)
    complete = await runtime.execute_readonly_call(
        PEER, original, expected_counter_head=pending.head
    )
    assert complete.state == "SUCCEEDED" and complete.attempts_consumed == 2
    assert complete.selection == pending.selection and complete.lineage_id == pending.lineage_id
    assert len(model.requests) == 1
