import json

import pytest

from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy, LoopHold, LoopRejected
from chiplog.composition.postgres_readonly_successor import retry_readonly_in_successor
from chiplog.platform.postgres import PostgresServer
from tests.support.composition.postgres_readonly import PEER, opened, selected_call
from tests.support.composition.postgres_readonly_successor import (
    unavailable,
)


async def test_successors_share_counter_and_snapshot_after_reopen(
    postgres_server: PostgresServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, runtime, original, model = await selected_call(postgres_server)
    monkeypatch.setattr(runtime, "read_history_snapshot", unavailable)
    first = await runtime.execute_readonly_call(PEER, original)
    source = runtime.execution_record("run")
    runtime = opened(database, HermeticModel())
    monkeypatch.setattr(runtime, "read_history_snapshot", unavailable)
    second = await retry_readonly_in_successor(
        runtime, PEER, original, first.head, source.head, "successor-2"
    )
    assert second.state == "RETRY_PENDING" and second.attempts_consumed == 2
    assert runtime.execution_record("run").state == "SUPERSEDED"
    successor = runtime.execution_record("successor-2")
    assert successor.turns == source.turns and successor.state == "ACTIVE"
    with pytest.raises(LoopHold):
        await runtime.continue_execution(PEER, successor.run_id, successor.head)
    runtime = opened(database, HermeticModel())
    monkeypatch.setattr(runtime, "read_history_snapshot", unavailable)
    third = await retry_readonly_in_successor(
        runtime, PEER, original, second.head, successor.head, "successor-3"
    )
    assert third.budget_exhausted and third.attempts_consumed == 3
    assert third.selection == first.selection and third.query == first.query
    assert third.lineage_id == first.lineage_id
    assert [attempt.run_id for attempt in third.attempts] == ["run", "successor-2", "successor-3"]
    before = runtime.snapshot()
    assert (
        await retry_readonly_in_successor(
            runtime, PEER, original, first.head, source.head, "successor-2"
        )
        == third
    )
    assert runtime.snapshot() == before
    with pytest.raises(LoopRejected):
        await retry_readonly_in_successor(
            runtime,
            PEER,
            original,
            third.head,
            runtime.execution_record("successor-3").head,
            "fourth",
        )
    assert runtime.snapshot() == before
    assert len(model.requests) == 1


async def test_successful_successor_continues_model_with_original_seal(
    postgres_server: PostgresServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, runtime, original, original_model = await selected_call(postgres_server)
    with monkeypatch.context() as patched:
        patched.setattr(runtime, "read_history_snapshot", unavailable)
        first = await runtime.execute_readonly_call(PEER, original)
    source = runtime.execution_record("run")
    response = json.dumps(
        {
            "kind": "Continue",
            "tool_calls": [
                {
                    "call_id": "next",
                    "tool": "propose_intent",
                    "text": "next",
                }
            ],
        }
    ).encode()
    model = HermeticModel((response,))
    runtime = opened(database, model)
    complete = await retry_readonly_in_successor(
        runtime, PEER, original, first.head, source.head, "successor"
    )
    assert complete.state == "SUCCEEDED"
    successor = runtime.execution_record("successor")
    continued = await runtime.continue_execution(PEER, successor.run_id, successor.head)
    captured = await runtime.capture_execution(PEER, continued.run_id, continued.head)
    sealed = await runtime.seal_execution(PEER, captured.run_id, captured.head)
    assert sealed.turns[0] == source.turns[0]
    assert sealed.turns[1].turn_id == "successor/turn/2"
    assert complete.selection.snapshot_id in model.requests[0].request
    calls = runtime.initialized_calls()
    assert calls[0].call.original.original_run_id == "run"
    assert calls[1].call.original.original_run_id == "successor"
    assert len(original_model.requests) == len(model.requests) == 1
    before = runtime.snapshot()
    reopened = opened(database, HermeticModel())
    assert (
        await retry_readonly_in_successor(
            reopened, PEER, original, first.head, source.head, "successor"
        )
        == complete
    )
    assert reopened.snapshot() == before


async def test_accepted_successor_interruption_reopens_without_another_read(
    postgres_server: PostgresServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, runtime, original, _ = await selected_call(postgres_server)
    with monkeypatch.context() as patched:
        patched.setattr(runtime, "read_history_snapshot", unavailable)
        first = await runtime.execute_readonly_call(PEER, original)
    source = runtime.execution_record("run")
    reads: list[str] = []

    def interrupted(*args: object) -> bytes:
        assert runtime.execution_record("run").state == "SUPERSEDED"
        assert runtime.execution_record("successor").state == "ACTIVE"
        accepted = [
            record
            for record in runtime.snapshot().batches[-1].records
            if record.schema_id == "chiplog.readonly.lineage.v2"
        ]
        assert len(accepted) == 1
        reads.append("read")
        raise RuntimeError("interrupted accepted read")

    monkeypatch.setattr(runtime, "read_history_snapshot", interrupted)
    with pytest.raises(RuntimeError, match="interrupted"):
        await retry_readonly_in_successor(
            runtime, PEER, original, first.head, source.head, "successor"
        )
    reopened = opened(database, HermeticModel())
    monkeypatch.setattr(reopened, "read_history_snapshot", interrupted)
    selected = await retry_readonly_in_successor(
        reopened, PEER, original, first.head, source.head, "successor"
    )
    assert selected.state == "ACCEPTED" and selected.attempts_consumed == 2
    assert reads == ["read"]
    with pytest.raises(LoopRejected):
        await retry_readonly_in_successor(
            reopened,
            PEER,
            original,
            selected.head,
            reopened.execution_record("successor").head,
            "third",
        )


async def test_stale_pending_and_worker_cannot_select_successor(
    postgres_server: PostgresServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, stale, original, _ = await selected_call(postgres_server)
    monkeypatch.setattr(stale, "read_history_snapshot", unavailable)
    first = await stale.execute_readonly_call(PEER, original)
    source = stale.execution_record("run")
    current = opened(database, HermeticModel())
    before = current.snapshot()
    with pytest.raises(LoopHold, match="stale"):
        await retry_readonly_in_successor(
            stale, PEER, original, first.head, source.head, "successor"
        )
    with pytest.raises(PermissionError, match="peer"):
        await retry_readonly_in_successor(
            current, "wrong-peer", original, first.head, source.head, "successor"
        )
    with pytest.raises(LoopRejected, match="stale"):
        await retry_readonly_in_successor(
            current, PEER, original, "wrong-pending", source.head, "successor"
        )
    assert current.snapshot() == before


async def test_inherited_turn_origin_survives_four_edges_across_two_lineages(
    postgres_server: PostgresServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, runtime, original, _ = await selected_call(postgres_server)
    monkeypatch.setattr(runtime, "read_history_snapshot", unavailable)
    pending = await runtime.execute_readonly_call(PEER, original)
    history = json.dumps(
        {
            "kind": "Continue",
            "tool_calls": [
                {
                    "call_id": "history-again",
                    "tool": "read_conversation_history",
                    "arguments": {"limit": 10, "after_cursor": None},
                }
            ],
        }
    ).encode()
    proposal = json.dumps(
        {
            "kind": "Continue",
            "tool_calls": [
                {
                    "call_id": "next",
                    "tool": "propose_intent",
                    "text": "next",
                }
            ],
        }
    ).encode()
    model = HermeticModel((history, proposal))
    runtime = opened(database, model)
    monkeypatch.setattr(runtime, "read_history_snapshot", unavailable)
    source = runtime.execution_record("run")
    for successor_id in ("first-2", "first-3"):
        pending = await retry_readonly_in_successor(
            runtime, PEER, original, pending.head, source.head, successor_id
        )
        source = runtime.execution_record(successor_id)
    assert pending.budget_exhausted
    continued = await runtime.continue_execution(PEER, source.run_id, source.head)
    captured = await runtime.capture_execution(PEER, continued.run_id, continued.head)
    sealed = await runtime.seal_execution(PEER, captured.run_id, captured.head)
    second_original = runtime.initialized_calls()[-1].original_call_id
    assert second_original != original
    pending = await runtime.execute_readonly_call(PEER, second_original)
    source = sealed
    for successor_id in ("second-2", "second-3"):
        pending = await retry_readonly_in_successor(
            runtime, PEER, second_original, pending.head, source.head, successor_id
        )
        source = runtime.execution_record(successor_id)
    assert pending.budget_exhausted and pending.attempts_consumed == 3
    continued = await runtime.continue_execution(PEER, source.run_id, source.head)
    await runtime.capture_execution(PEER, continued.run_id, continued.head)
    assert len(model.requests) == 2
    assert original in model.requests[-1].request
    assert second_original in model.requests[-1].request


async def test_pending_sibling_holds_and_successor_acceptance_is_one_batch(
    postgres_server: PostgresServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.support.composition.postgres_execution import bootstrap
    from tests.support.composition.postgres_execution import opened as execution_opened

    database = await bootstrap(postgres_server)
    response = json.dumps(
        {
            "kind": "Continue",
            "tool_calls": [
                {
                    "call_id": "history",
                    "tool": "read_conversation_history",
                    "arguments": {"limit": 10, "after_cursor": None},
                },
                {
                    "call_id": "effect",
                    "tool": "request_self_effect",
                    "arguments": {"payload": "aGVsbG8=", "bundle_members": ["effect"]},
                },
            ],
        }
    ).encode()
    runtime = execution_opened(database, HermeticModel((response,)))
    created = await runtime.create_execution(PEER, "mixed", "Read history", BudgetPolicy())
    started = await runtime.begin_execution(PEER, created.run_id, created.head)
    captured = await runtime.capture_execution(PEER, started.run_id, started.head)
    sealed = await runtime.seal_execution(PEER, captured.run_id, captured.head)
    original, sibling = runtime.initialized_calls()
    with monkeypatch.context() as patched:
        patched.setattr(runtime, "read_history_snapshot", unavailable)
        pending = await runtime.execute_readonly_call(PEER, original.original_call_id)
    before = runtime.snapshot()
    with pytest.raises(LoopHold, match="pending"):
        await retry_readonly_in_successor(
            runtime, PEER, original.original_call_id, pending.head, sealed.head, "successor"
        )
    assert runtime.snapshot() == before
    await runtime.cancel_execution_call(PEER, sibling.original_call_id, "cancel-sibling")
    terminal = await retry_readonly_in_successor(
        runtime, PEER, original.original_call_id, pending.head, sealed.head, "successor"
    )
    assert terminal.state == "SUCCEEDED"
    batches = runtime.snapshot().batches
    admission = [batch for batch in batches if batch.operation == "readonly.successor.v2"]
    assert len(admission) == 1
    assert len(admission[0].records) == 4
    assert {record.schema_id for record in admission[0].records} == {
        "chiplog.agent-loop.execution-record.v3",
        "chiplog.readonly.successor.v2",
        "chiplog.readonly.lineage.v2",
    }
    assert (
        sum(
            record.schema_id == "chiplog.conversation.entry.pg.v1"
            for batch in batches
            for record in batch.records
        )
        == 1
    )
