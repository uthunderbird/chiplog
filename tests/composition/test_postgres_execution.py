import asyncio
import json
import time
from pathlib import Path
from typing import Literal

import httpx
import pytest
from oauth_cli_kit.models import OAuthToken  # type: ignore[import-untyped]

from chiplog.adapters.driven.codex_auth import CodexTokenStorage, credential_identity
from chiplog.adapters.driven.codex_model import CodexModel
from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.capabilities.agent_loop.contracts import (
    BudgetPolicy,
    LoopHold,
    LoopRejected,
    ModelAttempt,
)
from chiplog.capabilities.agent_loop.execution_client_transitions import ExecutionAttempt
from chiplog.capabilities.agent_loop.execution_history_contracts import ExecutionRunRecordV3
from chiplog.capabilities.agent_loop.live_contract import LiveModelBinding
from chiplog.capabilities.agent_loop.readonly_history_tool_contracts import ConversationHistoryQuery
from chiplog.composition.postgres_execution import PostgresExecutionRuntime
from chiplog.platform.postgres import PostgresServer, Record, Snapshot
from tests.support.composition.postgres_execution import (
    IDENTITY,
    SECRET,
    bootstrap,
    opened,
    origin,
)


@pytest.mark.parametrize("version", ["v2", "v3"])
async def test_native_postgres_run_capture_seal_reopen_and_exact_replay(
    postgres_server: PostgresServer,
    version: Literal["v2", "v3"],
) -> None:
    database = await bootstrap(postgres_server)
    calls: list[dict[str, object]] = [
        {"call_id": "proposal", "tool": "propose_intent", "text": "Swim"}
    ]
    if version == "v3":
        calls.append(
            {
                "call_id": "history",
                "tool": "read_conversation_history",
                "arguments": {"limit": 5, "after_cursor": None},
            }
        )
    model = HermeticModel((json.dumps({"kind": "Continue", "tool_calls": calls}).encode(),))
    runtime = opened(database, model)
    created = await runtime.create_execution(
        "local-cli", "run", "Hi", BudgetPolicy(), execution_version=version
    )
    started = await runtime.begin_execution("local-cli", "run", created.head)
    captured = await runtime.capture_execution("local-cli", "run", started.head)
    assert captured.turns[-1].attempts[-1].state == "RESPONSE_CAPTURED"
    sealed = await runtime.seal_execution("local-cli", "run", captured.head)
    assert sealed.event == "ModelResponseSealed"
    assert len(model.requests) == 1
    originals = runtime.initialized_calls()
    assert len(originals) == len(calls)
    if version == "v3":
        assert isinstance(sealed, ExecutionRunRecordV3)
        assert originals[1].call.classification == "READ_ONLY"
        assert originals[1].call.retry_lineage.kind == "READ_ONLY_RETRY_LINEAGE"
        assert originals[1].call.retry_lineage.lineage.max_attempts == 3
    before = runtime.snapshot()
    assert await runtime.begin_execution("local-cli", "run", created.head) == started
    assert await runtime.capture_execution("local-cli", "run", started.head) == captured
    assert await runtime.seal_execution("local-cli", "run", captured.head) == sealed
    for action, head in (
        (runtime.begin_execution, created.head),
        (runtime.capture_execution, started.head),
        (runtime.seal_execution, captured.head),
    ):
        with pytest.raises(PermissionError, match="peer"):
            await action("wrong-peer", "run", head)
    with pytest.raises(PermissionError, match="peer"):
        await runtime.create_execution(
            "wrong-peer", "run", "Hi", BudgetPolicy(), execution_version=version
        )
    assert runtime.snapshot() == before and len(model.requests) == 1
    fresh_model = HermeticModel()
    reopened = opened(database, fresh_model)
    assert reopened.execution_record("run") == sealed
    assert await reopened.capture_execution("local-cli", "run", started.head) == captured
    assert reopened.initialized_calls() == originals
    assert reopened.read_user_history_at(before.head, "p")[0].text == "Hi"
    assert fresh_model.requests == []
    with pytest.raises(LoopHold, match="stale"):
        runtime.snapshot()


async def test_emitted_unknown_attempt_is_not_reissued_after_reopen(
    postgres_server: PostgresServer,
) -> None:
    class LostModel(HermeticModel):
        async def invoke(self, attempt: ModelAttempt | ExecutionAttempt) -> tuple[bytes, str]:
            await super().invoke(attempt)
            raise RuntimeError("response lost after SEND")

    database = await bootstrap(postgres_server)
    model = LostModel((b"lost",))
    runtime = opened(database, model)
    created = await runtime.create_execution("local-cli", "lost", "Hi", BudgetPolicy())
    started = await runtime.begin_execution("local-cli", "lost", created.head)
    with pytest.raises(RuntimeError, match="response lost"):
        await runtime.capture_execution("local-cli", "lost", started.head)
    assert (
        runtime.execution_record("lost").turns[-1].attempts[-1].state == "EMITTED_OUTCOME_UNKNOWN"
    )
    replacement = HermeticModel()
    reopened = opened(database, replacement)
    with pytest.raises(LoopHold, match="blind resend"):
        await reopened.capture_execution("local-cli", "lost", started.head)
    assert len(model.requests) == 1 and replacement.requests == []


async def test_batch_guard_atomicity_revocation_and_exclusive_model_owner(
    postgres_server: PostgresServer,
) -> None:
    database = await bootstrap(postgres_server)
    model = HermeticModel()
    runtime = opened(database, model)
    with pytest.raises(LoopRejected, match="already bound"):
        opened(database, model)
    before = runtime.snapshot()

    def reject(snapshot: Snapshot) -> None:
        assert snapshot == before
        raise LoopRejected("failed exact original binding")

    with pytest.raises(LoopRejected, match="exact original"):
        await runtime.publish_execution_batch(
            "local-cli",
            "test",
            "key",
            before.head,
            (Record("member", "owner", "schema", b"bytes"),),
            reject,
        )
    assert runtime.snapshot() == before
    runtime.revoke("credential:1")
    with pytest.raises(PermissionError, match="authenticated"):
        runtime.snapshot()


async def test_replacement_fences_old_model_paused_before_send(
    postgres_server: PostgresServer,
) -> None:
    class PausedModel(HermeticModel):
        def __init__(self) -> None:
            super().__init__((b"unused",))
            self.entered, self.release = asyncio.Event(), asyncio.Event()

        async def invoke(self, attempt: ModelAttempt | ExecutionAttempt) -> tuple[bytes, str]:
            self.entered.set()
            await self.release.wait()
            return await super().invoke(attempt)

    database = await bootstrap(postgres_server)
    model = PausedModel()
    runtime = opened(database, model)
    created = await runtime.create_execution("local-cli", "paused", "Hi", BudgetPolicy())
    started = await runtime.begin_execution("local-cli", "paused", created.head)
    task = asyncio.create_task(runtime.capture_execution("local-cli", "paused", started.head))
    await model.entered.wait()
    with pytest.raises(LoopRejected, match="already bound"):
        opened(database, model)
    fresh = opened(database, HermeticModel())
    model.release.set()
    with pytest.raises(LoopHold, match="stale"):
        await task
    assert model.requests == []
    assert (
        fresh.execution_record("paused").turns[-1].attempts[-1].state == "EMITTED_OUTCOME_UNKNOWN"
    )


async def test_native_calls_select_competing_branch_and_reopen_terminal(
    postgres_server: PostgresServer,
) -> None:
    database = await bootstrap(postgres_server)
    response = {
        "kind": "Continue",
        "tool_calls": [
            {
                "call_id": label,
                "tool": "request_self_effect",
                "arguments": {"payload": "aGVsbG8=", "bundle_members": [label]},
            }
            for label in ("accept", "cancel", "unknown")
        ],
    }
    runtime = opened(database, HermeticModel((json.dumps(response).encode(),)))
    created = await runtime.create_execution("local-cli", "calls", "Hi", BudgetPolicy())
    started = await runtime.begin_execution("local-cli", "calls", created.head)
    captured = await runtime.capture_execution("local-cli", "calls", started.head)
    await runtime.seal_execution("local-cli", "calls", captured.head)
    accepted_call, cancelled_call, unknown_call = runtime.initialized_calls()
    before = runtime.snapshot()
    for call in (accepted_call, unknown_call):
        with pytest.raises(LoopRejected, match="preview adoption"):
            await runtime.accept_execution_call("local-cli", call.original_call_id, "accept-act")
        with pytest.raises(LoopRejected, match="resolver"):
            await runtime.record_execution_call_outcome(
                "local-cli",
                call.original_call_id,
                runtime.call_state(call.original_call_id).head,
                outcome="SUCCEEDED",
                result=b"caller flag",
            )
    assert runtime.snapshot() == before
    cancelled = await runtime.cancel_execution_call(
        "local-cli", cancelled_call.original_call_id, "cancel-act"
    )
    reopened = opened(database, HermeticModel())
    before = reopened.snapshot()
    assert (
        await reopened.cancel_execution_call("local-cli", cancelled.original_call_id, "cancel-act")
        == cancelled
    )
    with pytest.raises(LoopRejected):
        await reopened.accept_execution_call(
            "local-cli", cancelled.original_call_id, "wrong-accept"
        )
    assert reopened.snapshot() == before


async def test_history_snapshot_and_cursor_keep_original_frontier_after_reopen(
    postgres_server: PostgresServer,
) -> None:
    database = await bootstrap(postgres_server)
    runtime = opened(database, HermeticModel())
    await runtime.create_execution("local-cli", "one", "first", BudgetPolicy())
    await runtime.create_execution("local-cli", "two", "second", BudgetPolicy())
    query = ConversationHistoryQuery(limit=1, after_cursor=None)
    selection = runtime.select_history_snapshot("local-cli", query)
    first = json.loads(runtime.read_history_snapshot("local-cli", selection, query))
    assert first["entries"][0]["text"] == "first"
    next_query = query.model_copy(update={"after_cursor": first["next_cursor"]})
    await runtime.create_execution("local-cli", "three", "new after snapshot", BudgetPolicy())
    reopened = opened(database, HermeticModel())
    assert reopened.select_history_snapshot("local-cli", next_query) == selection
    second = json.loads(reopened.read_history_snapshot("local-cli", selection, next_query))
    assert [entry["text"] for entry in second["entries"]] == ["second"]
    assert second["next_cursor"] is None
    with pytest.raises(PermissionError, match="peer"):
        reopened.read_history_snapshot("wrong-peer", selection, query)
    with pytest.raises(PermissionError, match="selection"):
        reopened.read_history_snapshot(
            "local-cli", selection.model_copy(update={"principal_id": "other"}), query
        )
    reopened.revoke("credential:1")
    with pytest.raises(PermissionError, match="authenticated"):
        reopened.read_history_snapshot("local-cli", selection, query)


async def test_native_v3_uses_actual_codex_adapter_after_durable_emission(
    tmp_path: Path,
    postgres_server: PostgresServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = await bootstrap(postgres_server)
    storage = CodexTokenStorage(tmp_path / "oauth")
    token = OAuthToken(
        "test-access", "test-refresh", int(time.time() * 1000) + 600000, "native-account"
    )
    storage.save(token)
    binding = LiveModelBinding(credential_identity=credential_identity(token))
    model = CodexModel(storage, binding)
    runtime = PostgresExecutionRuntime(
        database, "t", SECRET, **IDENTITY, model=model, origin=origin()
    )
    requests = 0
    original_client = httpx.AsyncClient

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        assert (
            runtime.execution_record("live").turns[-1].attempts[-1].state
            == "EMITTED_OUTCOME_UNKNOWN"
        )
        assert request.headers["authorization"] == "Bearer test-access"
        requests += 1
        reply = {
            "kind": "Continue",
            "tool_calls": [
                {
                    "call_id": "history",
                    "tool": "read_conversation_history",
                    "arguments": {"limit": 1, "after_cursor": None},
                }
            ],
        }
        event = {
            "type": "response.completed",
            "response": {
                "id": "native-response",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": json.dumps(reply)}],
                    }
                ],
            },
        }
        return httpx.Response(200, content="data: " + json.dumps(event) + "\n\n")

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    created = await runtime.create_execution(
        "local-cli", "live", "Read history", BudgetPolicy(live_model=binding)
    )
    started = await runtime.begin_execution("local-cli", "live", created.head)
    captured = await runtime.capture_execution("local-cli", "live", started.head)
    await runtime.seal_execution("local-cli", "live", captured.head)
    assert runtime.initialized_calls()[0].call.classification == "READ_ONLY"
    assert await runtime.capture_execution("local-cli", "live", started.head) == captured
    assert requests == 1


@pytest.mark.parametrize("operation", ["begin", "seal"])
@pytest.mark.parametrize("interference", ["head", "revocation"])
async def test_execution_preparation_cannot_publish_after_head_change_or_revocation(
    postgres_server: PostgresServer,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    interference: str,
) -> None:
    database = await bootstrap(postgres_server)
    response = {
        "kind": "Continue",
        "tool_calls": [{"call_id": "proposal", "tool": "propose_intent", "text": "Swim"}],
    }
    runtime = opened(database, HermeticModel((json.dumps(response).encode(),)))
    selected = await runtime.create_execution(
        "local-cli", "run", "Hi", BudgetPolicy(), execution_version="v3"
    )
    if operation == "seal":
        started = await runtime.begin_execution("local-cli", "run", selected.head)
        selected = await runtime.capture_execution("local-cli", "run", started.head)

    original_snapshot = runtime._snapshot_for_peer
    changed: Snapshot | None = None

    def snapshot_then_change(peer: str) -> Snapshot:
        nonlocal changed
        snapshot = original_snapshot(peer)
        if changed is None:
            if interference == "head":
                result = database.publish(
                    "test.competing",
                    "competing",
                    "competing",
                    (Record("competing", "test", "test", b"concurrent publication"),),
                    snapshot.head,
                )
                assert result.disposition == "COMMITTED"
            else:
                runtime.revoke("credential:1")
            changed = database.snapshot()
        return snapshot

    monkeypatch.setattr(runtime, "_snapshot_for_peer", snapshot_then_change)
    action = runtime.begin_execution if operation == "begin" else runtime.seal_execution
    error = LoopRejected if interference == "head" else PermissionError
    message = "stale execution tenant head" if interference == "head" else "authenticated"
    with pytest.raises(error, match=message):
        await action("local-cli", "run", selected.head)
    assert changed is not None
    assert database.snapshot() == changed
