import asyncio
import json
import time
from pathlib import Path
from typing import Any, Literal

import httpx
import pytest
from oauth_cli_kit.models import OAuthToken  # type: ignore[import-untyped]

from chiplog.adapters.driven.codex_auth import CodexTokenStorage, credential_identity
from chiplog.adapters.driven.codex_model import CodexModel
from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy, LoopHold, LoopRejected
from chiplog.capabilities.agent_loop.live_contract import LiveModelBinding
from chiplog.composition.postgres_execution import PostgresExecutionRuntime
from chiplog.composition.postgres_model_recovery import recover_model_response
from chiplog.platform.postgres import PostgresServer
from tests.composition.test_postgres_execution import IDENTITY, SECRET, bootstrap, opened, origin


@pytest.mark.parametrize("version", ["v2", "v3"])
@pytest.mark.parametrize("fault", ["before_capture", "lost_capture_ack"])
async def test_retained_model_response_recovers_after_reopen_without_resend(
    postgres_server: PostgresServer,
    monkeypatch: pytest.MonkeyPatch,
    version: Literal["v2", "v3"],
    fault: str,
) -> None:
    database = await bootstrap(postgres_server)
    raw = json.dumps(
        {
            "kind": "Continue",
            "tool_calls": [
                {
                    "call_id": "proposal",
                    "tool": "propose_intent",
                    "text": "hello",
                }
            ],
        }
    ).encode()
    model = HermeticModel((raw,))
    runtime = opened(database, model)
    created = await runtime.create_execution(
        "local-cli", "run", "Hi", BudgetPolicy(), execution_version=version
    )
    started = await runtime.begin_execution("local-cli", "run", created.head)
    publish = runtime.publish_execution_batch

    async def fail_capture(*args: Any, **kwargs: Any) -> Any:
        if args[1] == "execution.capture":
            if fault == "lost_capture_ack":
                await publish(*args, **kwargs)
            raise RuntimeError("capture interrupted")
        return await publish(*args, **kwargs)

    monkeypatch.setattr(runtime, "publish_execution_batch", fail_capture)
    with pytest.raises(RuntimeError, match="capture interrupted"):
        await runtime.capture_execution("local-cli", "run", started.head)
    emitted = next(
        run for run in runtime._runs(runtime.snapshot()) if run.event == "ModelAttemptEmitted"
    )
    fresh_model = HermeticModel()
    reopened = opened(database, fresh_model)
    recovered = await recover_model_response(reopened, "local-cli", "run", emitted.head)
    attempt = recovered.turns[-1].attempts[-1]
    assert attempt.state == "RESPONSE_CAPTURED"
    assert attempt.attempt_id == model.requests[0].attempt_id
    assert attempt.manifest == model.requests[0].manifest
    sealed = await reopened.seal_execution("local-cli", "run", recovered.head)
    assert sealed.turns[-1].state == "ACCEPTED"
    before = reopened.snapshot()
    assert await recover_model_response(reopened, "local-cli", "run", emitted.head) == recovered
    assert reopened.snapshot() == before
    assert len(model.requests) == 1 and fresh_model.requests == []


async def test_actual_codex_response_custody_survives_lost_capture(
    postgres_server: PostgresServer,
    tmp_path: Path,
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
    requests: list[str] = []
    original_client = httpx.AsyncClient
    raw = json.dumps(
        {
            "kind": "Continue",
            "tool_calls": [
                {
                    "call_id": "proposal",
                    "tool": "propose_intent",
                    "text": "hello",
                }
            ],
        }
    )

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request.method)
        event = {
            "type": "response.completed",
            "response": {
                "id": "retained-response",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": raw}],
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
        "local-cli", "live", "Hi", BudgetPolicy(live_model=binding)
    )
    started = await runtime.begin_execution("local-cli", "live", created.head)
    publish = runtime.publish_execution_batch

    async def fail_capture(*args: Any, **kwargs: Any) -> Any:
        if args[1] == "execution.capture":
            raise RuntimeError("lost capture")
        return await publish(*args, **kwargs)

    monkeypatch.setattr(runtime, "publish_execution_batch", fail_capture)
    with pytest.raises(RuntimeError, match="lost capture"):
        await runtime.capture_execution("local-cli", "live", started.head)
    emitted = runtime.execution_record("live")
    reopened = PostgresExecutionRuntime(
        database, "t", SECRET, **IDENTITY, model=CodexModel(storage, binding), origin=origin()
    )
    recovered = await recover_model_response(reopened, "local-cli", "live", emitted.head)
    assert recovered.turns[-1].attempts[-1].receipt == "retained-response"
    await reopened.seal_execution("local-cli", "live", recovered.head)
    assert requests == ["POST"]


async def test_started_without_retained_response_holds_and_cannot_start_again(
    postgres_server: PostgresServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = await bootstrap(postgres_server)
    model = HermeticModel((b"not retained",))
    runtime = opened(database, model)
    created = await runtime.create_execution("local-cli", "run", "Hi", BudgetPolicy())
    started = await runtime.begin_execution("local-cli", "run", created.head)
    custody = model.custody
    assert custody is not None

    def lose_before_retain(*args: object) -> None:
        raise RuntimeError("response lost before custody commit")

    monkeypatch.setattr(custody, "retain", lose_before_retain)
    with pytest.raises(RuntimeError, match="response lost"):
        await runtime.capture_execution("local-cli", "run", started.head)
    attempt = model.requests[0]
    with pytest.raises(LoopHold, match="already started"):
        custody.start(attempt)
    emitted = runtime.execution_record("run")
    reopened = opened(database, HermeticModel())
    before = reopened.snapshot()
    with pytest.raises(LoopRejected, match="unknown"):
        await recover_model_response(reopened, "local-cli", "run", emitted.head)
    assert reopened.snapshot() == before
    assert len(model.requests) == 1


@pytest.mark.parametrize("stop", ["takeover", "abort"])
async def test_actual_late_codex_response_retains_facts_after_worker_takeover_or_abort(
    postgres_server: PostgresServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stop: Literal["takeover", "abort"],
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
    entered, release = asyncio.Event(), asyncio.Event()
    requests: list[str] = []
    original_client = httpx.AsyncClient

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request.method)
        entered.set()
        await release.wait()
        raw = json.dumps(
            {
                "kind": "Continue",
                "tool_calls": [
                    {
                        "call_id": "proposal",
                        "tool": "propose_intent",
                        "text": "late",
                    }
                ],
            }
        )
        event = {
            "type": "response.completed",
            "response": {
                "id": "late-response",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": raw}],
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
        "local-cli", "late", "Hi", BudgetPolicy(live_model=binding)
    )
    started = await runtime.begin_execution("local-cli", "late", created.head)
    task = asyncio.create_task(runtime.capture_execution("local-cli", "late", started.head))
    await asyncio.wait_for(entered.wait(), timeout=300)
    emitted = runtime.execution_record("late")
    if stop == "takeover":
        reopened = PostgresExecutionRuntime(
            database, "t", SECRET, **IDENTITY, model=CodexModel(storage, binding), origin=origin()
        )
        stopped = None
    else:
        from chiplog.composition.postgres_run_recovery import abort_execution

        stopped = await abort_execution(runtime, "local-cli", "late", emitted.head)
        reopened = runtime
    release.set()
    with pytest.raises(LoopHold, match=r"stale|advanced"):
        await task
    if stop == "takeover":
        assert reopened.execution_record("late") == emitted
        recovered = await recover_model_response(reopened, "local-cli", "late", emitted.head)
        assert recovered.turns[-1].attempts[-1].receipt == "late-response"
        assert recovered.turns[-1].attempts[-1].manifest == emitted.turns[-1].attempts[-1].manifest
    else:
        assert reopened.execution_record("late") == stopped
        assert reopened._model_custody.retained(emitted)[1] == "late-response"
        before = reopened.snapshot()
        with pytest.raises(LoopRejected, match="stale"):
            await recover_model_response(reopened, "local-cli", "late", emitted.head)
        assert reopened.snapshot() == before
    assert requests == ["POST"]


async def test_start_ack_loss_does_not_invoke_transport_or_authorize_another_start(
    postgres_server: PostgresServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = await bootstrap(postgres_server)
    model = HermeticModel((b"never sent",))
    runtime = opened(database, model)
    custody = model.custody
    assert custody is not None
    real_start = custody.start

    def lose_start_ack(attempt: Any) -> None:
        real_start(attempt)
        raise RuntimeError("lost start acknowledgement")

    monkeypatch.setattr(custody, "start", lose_start_ack)
    created = await runtime.create_execution("local-cli", "run", "Hi", BudgetPolicy())
    started = await runtime.begin_execution("local-cli", "run", created.head)
    with pytest.raises(RuntimeError, match="lost start acknowledgement"):
        await runtime.capture_execution("local-cli", "run", started.head)
    emitted = runtime.execution_record("run")
    with pytest.raises(LoopHold, match="already started"):
        real_start(emitted.turns[-1].attempts[-1])
    reopened = opened(database, HermeticModel())
    with pytest.raises(LoopHold, match="unknown"):
        await recover_model_response(reopened, "local-cli", "run", emitted.head)
    assert model.requests == []
