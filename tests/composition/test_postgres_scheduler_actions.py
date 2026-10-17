import pytest

from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.capabilities.agent_loop.contracts import LoopHold
from chiplog.capabilities.agent_loop.execution_client_transitions import ExecutionAttempt
from chiplog.capabilities.agent_loop.scheduler_client_contracts import (
    NativeSchedulerCycle,
    NativeSchedulerLeaseRequest,
)
from chiplog.composition.postgres_execution import PostgresExecutionRuntime
from chiplog.composition.postgres_scheduler import PostgresScheduler
from chiplog.platform.postgres import PostgresServer
from tests.composition.test_postgres_scheduler import (
    IDENTITY,
    PEER,
    SECRET,
    Clock,
    adopted,
    opened,
)
from tests.support.scheduler_execution import origin


@pytest.mark.parametrize("expiry", ["BEFORE_START", "AFTER_RESPONSE"])
async def test_actual_model_start_checks_lease_but_late_response_is_retained(
    postgres_server: PostgresServer,
    monkeypatch: pytest.MonkeyPatch,
    expiry: str,
) -> None:
    clock = Clock(11)
    scheduler = await opened(postgres_server, clock)
    selected = await adopted(scheduler)
    cycle = await scheduler.scheduler_cycle(
        PEER, NativeSchedulerCycle(act_id="tick", schedule=selected.schedule)
    )
    assert cycle.result.kind == "ORDINARY"
    execution = cycle.result.members[0].execution
    assert execution.kind == "EXECUTION"
    model = HermeticModel(
        (
            b'{"kind":"Continue","tool_calls":['
            b'{"call_id":"proposal","tool":"propose_intent","text":"Review"}]}',
        )
    )
    runtime = PostgresExecutionRuntime(
        scheduler.runtime.database, "t", SECRET, **IDENTITY, model=model, origin=origin()
    )
    scheduler = PostgresScheduler(runtime, clock=clock, lease_duration_ns=10)
    view = scheduler.lineage(runtime.snapshot(), execution.root.root_id)
    before = runtime.snapshot()
    with pytest.raises(LoopHold):
        await runtime.begin_execution(PEER, execution.run_id, execution.initial_run.head)
    assert runtime.snapshot() == before
    await scheduler.transition_scheduler_lease(
        PEER, NativeSchedulerLeaseRequest(act_id="acquire", transition="ACQUIRE", expected=view)
    )
    started = await runtime.begin_execution(PEER, execution.run_id, execution.initial_run.head)
    invoke = model.invoke

    async def observed(attempt: ExecutionAttempt) -> tuple[bytes, str]:
        if expiry == "BEFORE_START":
            clock.now = 21
        result = await invoke(attempt)
        clock.now = 21
        return result

    monkeypatch.setattr(model, "invoke", observed)
    if expiry == "BEFORE_START":
        with pytest.raises(LoopHold, match="expired"):
            await runtime.capture_execution(PEER, execution.run_id, started.head)
        assert model.requests == []
    else:
        captured = await runtime.capture_execution(PEER, execution.run_id, started.head)
        assert captured.turns[-1].attempts[-1].state == "RESPONSE_CAPTURED"
        assert len(model.requests) == 1
        with pytest.raises(LoopHold, match="expired"):
            await runtime.seal_execution(PEER, execution.run_id, captured.head)
