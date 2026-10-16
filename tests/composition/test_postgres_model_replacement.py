import asyncio
import json
from typing import Any, Literal

import pytest

from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.capabilities.agent_loop.contracts import (
    BudgetPolicy,
    LoopHold,
    LoopRejected,
    ModelAttempt,
)
from chiplog.capabilities.agent_loop.execution_client_transitions import ExecutionAttempt
from chiplog.composition.postgres_execution import PostgresExecutionRuntime
from chiplog.composition.postgres_model_recovery import (
    recover_model_response,
    replace_unstarted_model_attempt,
)
from chiplog.platform.postgres import PostgresServer
from tests.composition.test_postgres_execution import SECRET, bootstrap, opened, origin

RESPONSE = json.dumps({"kind": "Continue", "tool_calls": [{
    "call_id": "proposal", "tool": "propose_intent", "text": "replacement",
}]}).encode()


class PausedModel(HermeticModel):
    def __init__(self) -> None:
        super().__init__((RESPONSE,))
        self.entered, self.release = asyncio.Event(), asyncio.Event()

    async def invoke(self, attempt: ModelAttempt | ExecutionAttempt) -> tuple[bytes, str]:
        self.entered.set()
        await self.release.wait()
        return await super().invoke(attempt)


@pytest.mark.parametrize("version", ["v2", "v3"])
@pytest.mark.parametrize("rotate", [False, True])
async def test_fenced_unstarted_attempt_replaces_once_and_preserves_lineage(
    postgres_server: PostgresServer, version: Literal["v2", "v3"], rotate: bool,
) -> None:
    database = await bootstrap(postgres_server)
    old_model = PausedModel()
    old = opened(database, old_model)
    created = await old.create_execution(
        "local-cli", "run", "Hi", BudgetPolicy(), execution_version=version
    )
    started = await old.begin_execution("local-cli", "run", created.head)
    task = asyncio.create_task(old.capture_execution("local-cli", "run", started.head))
    await asyncio.wait_for(old_model.entered.wait(), timeout=300)
    original = old.execution_record("run")
    model = HermeticModel((RESPONSE,))
    if rotate:
        with old._transaction() as (connection, trust):
            old._actor(connection, trust, "local-cli")
            credential = trust.rotate_credential("credential:1", "rotated-credential")
        current = PostgresExecutionRuntime(
            database, "t", SECRET, principal_id="p", credential_id=credential.credential_id,
            session_id=credential.session_id, model=model, origin=origin(),
        )
    else:
        current = opened(database, model)
    try:
        captured = await replace_unstarted_model_attempt(
            current, "local-cli", "run", original.head
        )
    finally:
        old_model.release.set()
    with pytest.raises((LoopHold, PermissionError), match=r"stale|authenticated"):
        await task
    attempts = captured.turns[-1].attempts
    assert len(attempts) == 2 and captured.turns[-1].selector == 1
    assert attempts[0] == original.turns[-1].attempts[-1]
    assert attempts[1].lineage_id == attempts[0].lineage_id
    assert attempts[1].generation == 1
    assert attempts[1].worker_session == captured.worker_session == current.current_worker()
    if rotate:
        assert attempts[1].worker_session != attempts[0].worker_session
    assert json.loads(attempts[1].request)["manifest"] == attempts[1].manifest.digest()
    before = current.snapshot()
    assert await replace_unstarted_model_attempt(
        current, "local-cli", "run", original.head
    ) == captured
    assert current.snapshot() == before
    assert old_model.requests == [] and len(model.requests) == 1
    assert old_model.custody is not None
    with pytest.raises(LoopRejected, match="started custody"):
        old_model.custody.retain(original.turns[-1].attempts[-1], b"late", "unissued")
    assert current.snapshot() == before
    await current.seal_execution("local-cli", "run", captured.head)


async def test_replacement_publication_ack_loss_never_becomes_a_send_permit(
    postgres_server: PostgresServer, monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = await bootstrap(postgres_server)
    old_model = PausedModel()
    old = opened(database, old_model)
    created = await old.create_execution("local-cli", "run", "Hi", BudgetPolicy())
    started = await old.begin_execution("local-cli", "run", created.head)
    task = asyncio.create_task(old.capture_execution("local-cli", "run", started.head))
    await asyncio.wait_for(old_model.entered.wait(), timeout=300)
    original = old.execution_record("run")
    model = HermeticModel((RESPONSE,))
    current = opened(database, model)
    publish = current.publish_execution_batch

    async def lost_ack(*args: Any, **kwargs: Any) -> Any:
        result = await publish(*args, **kwargs)
        if args[1] == "execution.emit":
            raise RuntimeError("replacement acknowledgement lost")
        return result

    try:
        with monkeypatch.context() as patched:
            patched.setattr(current, "publish_execution_batch", lost_ack)
            with pytest.raises(RuntimeError, match="acknowledgement lost"):
                await replace_unstarted_model_attempt(current, "local-cli", "run", original.head)
    finally:
        old_model.release.set()
    with pytest.raises(LoopHold):
        await task
    emitted = current.execution_record("run")
    assert emitted.turns[-1].attempts[-1].generation == 1
    assert await replace_unstarted_model_attempt(
        current, "local-cli", "run", original.head
    ) == emitted
    assert model.requests == old_model.requests == []
    with pytest.raises(LoopHold, match="unknown"):
        await recover_model_response(current, "local-cli", "run", emitted.head)
    # The new generation can itself be fenced when independently still unstarted.
    captured = await replace_unstarted_model_attempt(current, "local-cli", "run", emitted.head)
    assert captured.turns[-1].attempts[-1].generation == 2
    assert len(model.requests) == 1


async def test_started_transport_forbids_replacement_and_retained_response_recovers(
    postgres_server: PostgresServer,
) -> None:
    class ReturnedModel(HermeticModel):
        def __init__(self) -> None:
            super().__init__((RESPONSE,))
            self.entered, self.release = asyncio.Event(), asyncio.Event()

        async def invoke(self, attempt: ModelAttempt | ExecutionAttempt) -> tuple[bytes, str]:
            result = await super().invoke(attempt)
            self.entered.set()
            await self.release.wait()
            return result

    database = await bootstrap(postgres_server)
    model = ReturnedModel()
    old = opened(database, model)
    created = await old.create_execution("local-cli", "run", "Hi", BudgetPolicy())
    started = await old.begin_execution("local-cli", "run", created.head)
    task = asyncio.create_task(old.capture_execution("local-cli", "run", started.head))
    await asyncio.wait_for(model.entered.wait(), timeout=300)
    original = old.execution_record("run")
    current = opened(database, HermeticModel())
    before = current.snapshot()
    try:
        with pytest.raises(LoopHold, match="may have started"):
            await replace_unstarted_model_attempt(current, "local-cli", "run", original.head)
        assert current.snapshot() == before
        recovered = await recover_model_response(current, "local-cli", "run", original.head)
        assert recovered.turns[-1].attempts[-1].generation == 0
    finally:
        model.release.set()
    with pytest.raises(LoopHold, match="stale"):
        await task
    assert len(model.requests) == 1


async def test_explicit_zero_model_retry_budget_denies_fenced_replacement(
    postgres_server: PostgresServer,
) -> None:
    database = await bootstrap(postgres_server)
    old_model = PausedModel()
    old = opened(database, old_model)
    created = await old.create_execution(
        "local-cli", "run", "Hi", BudgetPolicy(max_model_retries=0)
    )
    started = await old.begin_execution("local-cli", "run", created.head)
    task = asyncio.create_task(old.capture_execution("local-cli", "run", started.head))
    await asyncio.wait_for(old_model.entered.wait(), timeout=300)
    original = old.execution_record("run")
    model = HermeticModel((RESPONSE,))
    current = opened(database, model)
    before = current.snapshot()
    try:
        with pytest.raises(LoopRejected, match="retry budget"):
            await replace_unstarted_model_attempt(current, "local-cli", "run", original.head)
    finally:
        old_model.release.set()
        with pytest.raises(LoopHold):
            await task
    assert current.snapshot() == before
    assert model.requests == old_model.requests == []
