"""Terminal shared history lineage reaches actual completion through successor."""

import json
from pathlib import Path
from typing import Literal

import pytest

from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.capabilities.agent_loop.contracts import LoopHold, LoopRejected
from chiplog.composition import postgres_readonly
from chiplog.platform.postgres import PostgresServer
from tests.composition.test_postgres_completion import opened
from tests.composition.test_postgres_effects import PEER
from tests.composition.test_postgres_readonly_carried_work import pending_after_closed_effect


@pytest.mark.parametrize("terminal", ["success", "exhausted"])
async def test_terminal_readonly_successor_completes_and_reopens_without_another_attempt(
    postgres_server: PostgresServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    terminal: Literal["success", "exhausted"],
) -> None:
    runtime, binding, _, call_id, first, reads = await pending_after_closed_effect(
        postgres_server, tmp_path, monkeypatch
    )
    source = runtime.execution_record("run")
    original_source_head = source.head
    before = runtime.snapshot()
    with pytest.raises(LoopHold):
        await runtime.continue_execution(PEER, source.run_id, source.head)
    with pytest.raises(LoopRejected):
        await runtime.accept_completion(PEER, source.run_id, source.head)
    assert runtime.snapshot() == before
    if terminal == "exhausted":

        def unavailable(*args: object) -> bytes:
            reads.append("read")
            raise postgres_readonly.HistoryReadUnavailable("definite retryable failure")

        monkeypatch.setattr(runtime, "read_history_snapshot", unavailable)
    second = await runtime.retry_readonly_in_successor(
        PEER, call_id, first.head, source.head, "successor-2"
    )
    if terminal == "exhausted":
        assert second.state == "RETRY_PENDING" and second.attempts_consumed == 2
        source = runtime.execution_record("successor-2")
        before = runtime.snapshot()
        with pytest.raises(LoopHold):
            await runtime.continue_execution(PEER, source.run_id, source.head)
        assert runtime.snapshot() == before
        selected = await runtime.retry_readonly_in_successor(
            PEER, call_id, second.head, source.head, "successor-3"
        )
        assert selected.state == "FAILED_DEFINITE_FINAL" and selected.budget_exhausted
    else:
        selected = second
        assert selected.state == "SUCCEEDED"
    assert selected.selection == first.selection and selected.query == first.query
    source = runtime.execution_record(selected.attempts[-1].run_id)
    raw = json.dumps(
        {
            "kind": "Complete",
            "tenant": "t",
            "run_id": source.run_id,
            "turn_id": f"{source.run_id}/turn/{len(source.turns) + 1}",
            "deliveries": [
                {"payload": [{"kind": "NonAuthoritativeText", "text": "history handled"}]}
            ],
        }
    ).encode()
    model = HermeticModel((raw,))
    runtime = opened(runtime, binding, model)
    continued = await runtime.continue_execution(PEER, source.run_id, source.head)
    captured = await runtime.capture_execution(PEER, source.run_id, continued.head)
    sealed = await runtime.seal_execution(PEER, source.run_id, captured.head)
    accepted = await runtime.accept_completion(PEER, source.run_id, sealed.head)
    assert runtime.execution_record(source.run_id).state == "SUCCEEDED"
    assert accepted.source_run_head == sealed.head
    assert len(model.requests) == 1
    assert len(reads) == selected.attempts_consumed
    assert len(binding.provider.transfers) == 1
    before = runtime.snapshot()
    runtime = opened(runtime, binding, HermeticModel())

    def no_read(*args: object) -> bytes:
        pytest.fail("terminal read-only replay attempted another history read")

    monkeypatch.setattr(runtime, "read_history_snapshot", no_read)
    assert await runtime.execute_readonly_call(PEER, call_id) == selected
    assert (
        await runtime.retry_readonly_in_successor(
            PEER, call_id, first.head, original_source_head, "successor-2"
        )
        == selected
    )
    assert await runtime.accept_completion(PEER, source.run_id, sealed.head) == accepted
    assert runtime.snapshot() == before
    assert len(reads) == selected.attempts_consumed
    assert len(binding.provider.transfers) == 1
