import json

import pytest

from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.readonly_history_tool_contracts import ConversationHistoryQuery
from chiplog.capabilities.agent_loop.readonly_runtime_contracts import HistorySnapshotSelectionV2
from chiplog.composition.postgres_readonly import (
    HistoryReadUnavailable,
    execute_readonly_call,
    read_selected_readonly_lineage,
)
from chiplog.platform.postgres import PostgresServer
from tests.support.composition.postgres_readonly import (
    PEER,
    opened,
    selected_call,
)


async def test_history_retry_reopens_with_shared_three_attempt_budget(
    postgres_server: PostgresServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, runtime, original, model = await selected_call(postgres_server)
    reads: list[int] = []

    def unavailable(
        peer: str, selection: HistorySnapshotSelectionV2, query: ConversationHistoryQuery
    ) -> bytes:
        persisted = read_selected_readonly_lineage(runtime, peer, original)
        assert persisted is not None and persisted.state == "ACCEPTED"
        assert persisted.selection == selection and persisted.query == query
        reads.append(persisted.attempts_consumed)
        raise HistoryReadUnavailable("temporarily unavailable")

    monkeypatch.setattr(runtime, "read_history_snapshot", unavailable)
    first = await execute_readonly_call(runtime, PEER, original)
    assert first.state == "RETRY_PENDING" and first.attempts_consumed == 1
    fresh_model = HermeticModel()
    runtime = opened(database, fresh_model)
    monkeypatch.setattr(runtime, "read_history_snapshot", unavailable)
    second = await execute_readonly_call(runtime, PEER, original, expected_counter_head=first.head)
    third = await execute_readonly_call(runtime, PEER, original, expected_counter_head=second.head)
    assert third.state == "FAILED_DEFINITE_FINAL" and third.budget_exhausted
    assert third.selection == first.selection and third.lineage_id == first.lineage_id
    before = runtime.snapshot().head
    assert (
        await execute_readonly_call(runtime, PEER, original, expected_counter_head=third.head)
        == third
    )
    assert runtime.snapshot().head == before
    assert reads == [1, 2, 3]
    assert len(model.requests) == 1 and fresh_model.requests == []
    runtime = opened(database, HermeticModel())
    assert read_selected_readonly_lineage(runtime, PEER, original) == third


async def test_real_history_success_replays_without_another_read_or_model_request(
    postgres_server: PostgresServer,
) -> None:
    database, runtime, original, model = await selected_call(postgres_server)
    complete = await execute_readonly_call(runtime, PEER, original)
    assert complete.terminal and complete.state == "SUCCEEDED"
    outcome = complete.attempts[-1].outcome
    assert outcome is not None
    result = json.loads(outcome.value)
    assert [entry["text"] for entry in result["entries"]] == ["Read history"]
    fresh_model = HermeticModel()
    reopened = opened(database, fresh_model)
    before = reopened.snapshot()
    assert await execute_readonly_call(reopened, PEER, original) == complete
    with pytest.raises(PermissionError, match="peer"):
        await execute_readonly_call(reopened, "wrong-peer", original)
    assert reopened.snapshot() == before
    assert len(model.requests) == 1 and fresh_model.requests == []
    with pytest.raises(LoopRejected):
        await reopened.cancel_execution_call(PEER, original, "cancel-after-read")


async def test_accepted_without_outcome_reopens_on_hold_without_reinvoking_reader(
    postgres_server: PostgresServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, runtime, original, _ = await selected_call(postgres_server)
    reads: list[str] = []

    def interrupted(
        peer: str, selection: HistorySnapshotSelectionV2, query: ConversationHistoryQuery
    ) -> bytes:
        reads.append(selection.snapshot_id)
        raise RuntimeError("reader interrupted before outcome")

    monkeypatch.setattr(runtime, "read_history_snapshot", interrupted)
    with pytest.raises(RuntimeError, match="interrupted"):
        await execute_readonly_call(runtime, PEER, original)
    reopened = opened(database, HermeticModel())
    monkeypatch.setattr(reopened, "read_history_snapshot", interrupted)
    pending = await execute_readonly_call(reopened, PEER, original)
    assert pending.state == "ACCEPTED" and pending.attempts_consumed == 1
    with pytest.raises(LoopRejected, match="unresolved"):
        await execute_readonly_call(reopened, PEER, original, expected_counter_head=pending.head)
    assert len(reads) == 1


async def test_cancelled_before_accept_call_does_not_read_history(
    postgres_server: PostgresServer,
) -> None:
    _, runtime, original, _ = await selected_call(postgres_server)
    await runtime.cancel_execution_call(PEER, original, "cancel-first")
    before = runtime.snapshot()
    with pytest.raises(LoopRejected, match=r"branch|cancel"):
        await execute_readonly_call(runtime, PEER, original)
    assert runtime.snapshot() == before
