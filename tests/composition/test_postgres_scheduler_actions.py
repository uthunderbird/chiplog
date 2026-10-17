import json
import time
from pathlib import Path

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


@pytest.mark.parametrize("scenario", ["EXPIRED_SEND", "COMPLETE"])
async def test_scheduled_effect_send_cannot_claim_after_expiry_or_takeover(
    postgres_server: PostgresServer,
    tmp_path: Path,
    scenario: str,
) -> None:
    from chiplog.composition.postgres_application import PostgresApplicationDriver
    from chiplog.composition.postgres_effects import (
        adopt_effect,
        commit_effect_send,
        emit_effect,
        preview_effect,
        reconcile_effect,
        resolve_original_effect,
    )
    from tests.composition.test_native_channel_delivery import binding
    from tests.composition.test_postgres_completion import eligibility
    from tests.composition.test_postgres_planning import verifier
    from tests.composition.test_postgres_scheduler import bound_origin
    from tests.support.deployment_gate import signature

    clock = Clock(11)
    transport = binding(tmp_path)
    scheduler = await opened(postgres_server, clock, transport)
    selected = await adopted(scheduler)
    cycle = await scheduler.scheduler_cycle(
        PEER, NativeSchedulerCycle(act_id="tick", schedule=selected.schedule)
    )
    assert cycle.result.kind == "ORDINARY"
    execution = cycle.result.members[0].execution
    assert execution.kind == "EXECUTION"
    response = json.dumps(
        {
            "kind": "Continue",
            "tool_calls": [
                {
                    "call_id": "effect",
                    "tool": "request_self_effect",
                    "arguments": {"payload": "aGVsbG8=", "bundle_members": ["m"]},
                }
            ],
        }
    ).encode()
    runtime = PostgresExecutionRuntime(
        scheduler.runtime.database,
        "t",
        SECRET,
        principal_id="hermetic-principal",
        credential_id="c",
        session_id="s",
        model=HermeticModel(
            (
                response,
                json.dumps(
                    {
                        "kind": "Complete",
                        "tenant": "t",
                        "run_id": execution.run_id,
                        "turn_id": execution.run_id + "/turn/2",
                        "deliveries": [
                            {"payload": [{"kind": "NonAuthoritativeText", "text": "Done"}]}
                        ],
                    }
                ).encode(),
            )
        ),
        origin=bound_origin(transport),
        effects_binding=transport,
        authenticate_entitlement=verifier,
    )
    scheduler = PostgresScheduler(runtime, clock=clock, lease_duration_ns=10)
    view = scheduler.lineage(runtime.snapshot(), execution.root.root_id)
    acquired = await scheduler.transition_scheduler_lease(
        PEER, NativeSchedulerLeaseRequest(act_id="acquire", transition="ACQUIRE", expected=view)
    )
    with pytest.raises(LoopHold, match="explicit user adoption"):
        await PostgresApplicationDriver(runtime).drive_run(PEER, execution.run_id)
    call = runtime.initialized_calls()[0]
    preview = await preview_effect(
        runtime, transport, PEER, call.original_call_id, time.time_ns() + 10**12
    )
    intent = await adopt_effect(runtime, transport, PEER, preview.head, "user-adopt")
    entitlement, gate = eligibility(
        intent.canonical_bytes(), "effects.first_send", "scheduled-send"
    )
    runtime.import_entitlement(entitlement, signature(entitlement))
    await commit_effect_send(runtime, transport, PEER, call.original_call_id, gate)
    if scenario == "COMPLETE":
        claim = await emit_effect(runtime, transport, PEER, call.original_call_id)
        await reconcile_effect(runtime, transport, PEER, call.original_call_id)
        await resolve_original_effect(runtime, transport, PEER, call.original_call_id)
        result = await PostgresApplicationDriver(runtime).drive_run(PEER, execution.run_id)
        assert result.state == "SUCCEEDED"
        assert len(runtime.model.requests) == 2
        assert len(runtime.delivery_intents(PEER, execution.run_id)) == 1
        assert len(transport.provider.transfers) == 1
        clock.now = 21
        assert await emit_effect(runtime, transport, PEER, call.original_call_id) == claim
        assert len(transport.provider.transfers) == 1
        return
    clock.now = 21
    before = runtime.snapshot()
    with pytest.raises(LoopHold, match="expired"):
        await emit_effect(runtime, transport, PEER, call.original_call_id)
    assert runtime.snapshot() == before and transport.provider.transfers == ()
    await scheduler.transition_scheduler_lease(
        PEER,
        NativeSchedulerLeaseRequest(
            act_id="takeover", transition="TAKEOVER", expected=acquired.lineage
        ),
    )
    with pytest.raises(LoopHold, match="fence"):
        await emit_effect(runtime, transport, PEER, call.original_call_id)
    assert transport.provider.transfers == ()


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
