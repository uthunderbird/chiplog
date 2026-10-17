import json
import time
from pathlib import Path

import pytest

from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.capabilities.agent_loop.contracts import LoopHold
from chiplog.capabilities.agent_loop.execution_client_transitions import ExecutionAttempt
from chiplog.capabilities.agent_loop.readonly_history_tool_contracts import ConversationHistoryQuery
from chiplog.capabilities.agent_loop.readonly_runtime_contracts import HistorySnapshotSelectionV2
from chiplog.capabilities.agent_loop.scheduler_client_contracts import (
    NativeSchedulerCycle,
    NativeSchedulerLeaseRequest,
)
from chiplog.composition.postgres_execution import PostgresExecutionRuntime
from chiplog.composition.postgres_scheduler import PostgresScheduler
from chiplog.platform.postgres import PostgresServer
from tests.support.composition.postgres_scheduler import (
    IDENTITY,
    PEER,
    SECRET,
    Clock,
    adopted,
    opened,
)
from tests.support.scheduler_execution import origin


@pytest.mark.parametrize(
    "scenario", ["EXPIRED_SEND", "COMPLETE", "EXPIRED_DELIVERY_PREPARE", "EXPIRED_DELIVERY_EMIT"]
)
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
    from chiplog.platform.postgres_gate import canonical
    from tests.support.composition.native_channel_delivery import binding
    from tests.support.composition.postgres_completion import eligibility
    from tests.support.composition.postgres_planning import verifier
    from tests.support.composition.postgres_scheduler import bound_origin
    from tests.support.deployment_gate import signature

    clock = Clock(11)
    transport = binding(tmp_path, with_effect=True)
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
        model=(
            model := HermeticModel(
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
    if scenario != "EXPIRED_SEND":
        claim = await emit_effect(runtime, transport, PEER, call.original_call_id)
        await reconcile_effect(runtime, transport, PEER, call.original_call_id)
        await resolve_original_effect(runtime, transport, PEER, call.original_call_id)
        result = await PostgresApplicationDriver(runtime).drive_run(PEER, execution.run_id)
        assert result.state == "SUCCEEDED"
        assert len(model.requests) == 2
        assert len(runtime.delivery_intents(PEER, execution.run_id)) == 1
        assert len(transport.provider.transfers) == 1
        delivery = runtime.delivery_intents(PEER, execution.run_id)[0]
        delivery_entitlement, delivery_gate = eligibility(
            delivery.canonical_bytes(), "delivery.first_send", "scheduled-delivery"
        )
        runtime.import_entitlement(
            delivery_entitlement,
            signature(delivery_entitlement),
            previous=canonical(entitlement.model_dump(mode="json")),
        )
        if scenario == "EXPIRED_DELIVERY_PREPARE":
            clock.now = 21
            before = runtime.snapshot()
            with pytest.raises(LoopHold, match="expired"):
                await runtime.commit_delivery_send(PEER, delivery.delivery_id, delivery_gate)
            assert runtime.snapshot() == before
            assert len(transport.provider.transfers) == 1
            return
        await runtime.commit_delivery_send(PEER, delivery.delivery_id, delivery_gate)
        if scenario == "EXPIRED_DELIVERY_EMIT":
            clock.now = 21
            before = runtime.snapshot()
            with pytest.raises(LoopHold, match="expired"):
                await runtime.emit_delivery(PEER, delivery.delivery_id)
            assert runtime.snapshot() == before
            assert len(transport.provider.transfers) == 1
            return
        delivery_claim = await runtime.emit_delivery(PEER, delivery.delivery_id)
        assert len(transport.provider.transfers) == 2
        assert transport.provider.transfers[-1].ticket.payload == delivery.delivery.rendered_bytes
        clock.now = 21
        assert await emit_effect(runtime, transport, PEER, call.original_call_id) == claim
        fresh = PostgresExecutionRuntime(
            runtime.database,
            "t",
            SECRET,
            principal_id="hermetic-principal",
            credential_id="c",
            session_id="s",
            model=HermeticModel(()),
            origin=bound_origin(transport),
            effects_binding=transport,
            authenticate_entitlement=verifier,
        )
        assert await PostgresApplicationDriver(fresh).drive_run(PEER, execution.run_id) == result
        assert await fresh.emit_delivery(PEER, delivery.delivery_id) == delivery_claim
        receipt = await fresh.reconcile_delivery(PEER, delivery.delivery_id)
        assert receipt is not None and receipt.outcome == "CONFIRMED"
        assert fresh.model.requests == [] and len(transport.provider.transfers) == 2
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


@pytest.mark.parametrize(
    "expire_before_accept", [True, False], ids=["expired-admission", "late-outcome"]
)
async def test_scheduled_readonly_fences_admission_and_retains_accepted_outcome(
    postgres_server: PostgresServer,
    monkeypatch: pytest.MonkeyPatch,
    expire_before_accept: bool,
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
    raw = json.dumps(
        {
            "kind": "Continue",
            "tool_calls": [
                {
                    "call_id": "history",
                    "tool": "read_conversation_history",
                    "arguments": {"limit": 10, "after_cursor": None},
                }
            ],
        }
    ).encode()
    runtime = PostgresExecutionRuntime(
        scheduler.runtime.database,
        "t",
        SECRET,
        **IDENTITY,
        model=HermeticModel((raw,)),
        origin=origin(),
    )
    scheduler = PostgresScheduler(runtime, clock=clock, lease_duration_ns=10)
    view = scheduler.lineage(runtime.snapshot(), execution.root.root_id)
    await scheduler.transition_scheduler_lease(
        PEER, NativeSchedulerLeaseRequest(act_id="acquire", transition="ACQUIRE", expected=view)
    )
    started = await runtime.begin_execution(PEER, execution.run_id, execution.initial_run.head)
    captured = await runtime.capture_execution(PEER, execution.run_id, started.head)
    await runtime.seal_execution(PEER, execution.run_id, captured.head)
    call = runtime.initialized_calls()[0]
    original_read = runtime.read_history_snapshot
    reads = 0

    def observed_read(
        peer: str, selection: HistorySnapshotSelectionV2, query: ConversationHistoryQuery
    ) -> bytes:
        nonlocal reads
        reads += 1
        value = original_read(peer, selection, query)
        clock.now = 21
        return value

    monkeypatch.setattr(runtime, "read_history_snapshot", observed_read)
    if expire_before_accept:
        clock.now = 21
        before = runtime.snapshot()
        with pytest.raises(LoopHold, match="expired"):
            await runtime.execute_readonly_call(PEER, call.original_call_id)
        assert runtime.snapshot() == before
        assert reads == 0
    else:
        result = await runtime.execute_readonly_call(PEER, call.original_call_id)
        assert result.state == "SUCCEEDED"
        assert result.attempts_consumed == 1
        assert clock.now == 21 and reads == 1
        assert await runtime.execute_readonly_call(PEER, call.original_call_id) == result
        assert reads == 1
