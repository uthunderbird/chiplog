import json
from dataclasses import replace

import pytest

from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy, LoopHold
from chiplog.capabilities.agent_loop.readonly_history_tool_contracts import ConversationHistoryQuery
from chiplog.capabilities.agent_loop.readonly_runtime_contracts import HistorySnapshotSelectionV2
from chiplog.composition.postgres_readonly import HistoryReadUnavailable, execute_readonly_call
from chiplog.platform.postgres import PostgresServer, PostgresTransientReadError
from tests.support.composition.postgres_execution import bootstrap, opened


def proposal(label: str) -> bytes:
    return json.dumps(
        {
            "kind": "Continue",
            "tool_calls": [
                {
                    "call_id": label,
                    "tool": "propose_intent",
                    "text": label,
                }
            ],
        }
    ).encode()


async def test_two_seals_continue_reopen_and_replay_in_original_run(
    postgres_server: PostgresServer,
) -> None:
    database = await bootstrap(postgres_server)
    model = HermeticModel((proposal("first"), proposal("second")))
    runtime = opened(database, model)
    created = await runtime.create_execution("local-cli", "run", "Help", BudgetPolicy())
    started = await runtime.begin_execution("local-cli", "run", created.head)
    captured = await runtime.capture_execution("local-cli", "run", started.head)
    first = await runtime.seal_execution("local-cli", "run", captured.head)
    continued = await runtime.continue_execution("local-cli", "run", first.head)
    assert continued.turns[-1].ordinal == 2
    captured_second = await runtime.capture_execution("local-cli", "run", continued.head)
    second = await runtime.seal_execution("local-cli", "run", captured_second.head)
    assert len(model.requests) == 2
    assert "first" in model.requests[1].request
    assert second.turns[0].response_seal == first.turns[0].response_seal
    assert second.turns[1].response_seal != first.turns[0].response_seal
    assert len(runtime.initialized_calls()) == 2
    before = runtime.snapshot()
    reopened = opened(database, HermeticModel())
    assert reopened.execution_record("run") == second
    assert await reopened.continue_execution("local-cli", "run", first.head) == continued
    assert reopened.snapshot() == before


async def test_readonly_pending_holds_then_same_seal_terminal_allows_continuation(
    postgres_server: PostgresServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = await bootstrap(postgres_server)
    response = json.dumps(
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
    model = HermeticModel((response, proposal("next")))
    runtime = opened(database, model)
    created = await runtime.create_execution("local-cli", "run", "Read history", BudgetPolicy())
    started = await runtime.begin_execution("local-cli", "run", created.head)
    captured = await runtime.capture_execution("local-cli", "run", started.head)
    sealed = await runtime.seal_execution("local-cli", "run", captured.head)
    original = runtime.initialized_calls()[0].original_call_id
    with pytest.raises(LoopHold, match="read-only"):
        await runtime.continue_execution("local-cli", "run", sealed.head)

    def unavailable(*args: object) -> bytes:
        raise HistoryReadUnavailable("definite temporary read failure")

    with monkeypatch.context() as patched:
        patched.setattr(runtime, "read_history_snapshot", unavailable)
        pending = await execute_readonly_call(runtime, "local-cli", original)
    assert pending.state == "RETRY_PENDING"
    with pytest.raises(LoopHold, match="read-only"):
        await runtime.continue_execution("local-cli", "run", sealed.head)
    terminal = await execute_readonly_call(
        runtime, "local-cli", original, expected_counter_head=pending.head
    )
    assert terminal.state == "SUCCEEDED"
    continued = await runtime.continue_execution("local-cli", "run", sealed.head)
    assert continued.turns[-1].ordinal == 2
    await runtime.capture_execution("local-cli", "run", continued.head)
    assert terminal.selection.snapshot_id in model.requests[-1].request


async def test_real_postgres_read_lock_timeout_consumes_one_retryable_attempt(
    postgres_server: PostgresServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = await bootstrap(postgres_server)
    response = json.dumps(
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
    runtime = opened(database, HermeticModel((response,)))
    created = await runtime.create_execution("local-cli", "run", "Read history", BudgetPolicy())
    started = await runtime.begin_execution("local-cli", "run", created.head)
    captured = await runtime.capture_execution("local-cli", "run", started.head)
    await runtime.seal_execution("local-cli", "run", captured.head)
    original = runtime.initialized_calls()[0].original_call_id
    runtime.database = replace(
        database,
        server=PostgresServer(database.server.conninfo + " options='-c lock_timeout=25ms'"),
    )
    actual_read = runtime.read_history_snapshot
    observed: list[str] = []

    def blocked_read(
        peer: str, selection: HistorySnapshotSelectionV2, query: ConversationHistoryQuery
    ) -> bytes:
        with database.connect() as blocker:
            blocker.execute("SELECT head FROM user_state FOR UPDATE").fetchone()
            try:
                return actual_read(peer, selection, query)
            except PostgresTransientReadError as error:
                observed.append(type(error.__cause__).__name__)
                raise

    with monkeypatch.context() as patched:
        patched.setattr(runtime, "read_history_snapshot", blocked_read)
        failed = await runtime.execute_readonly_call("local-cli", original)
    assert observed == ["LockNotAvailable"]
    assert failed.state == "RETRY_PENDING" and failed.attempts_consumed == 1
    success = await runtime.execute_readonly_call(
        "local-cli", original, expected_counter_head=failed.head
    )
    assert success.state == "SUCCEEDED" and success.attempts_consumed == 2
