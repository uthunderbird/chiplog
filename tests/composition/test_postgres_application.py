import json
from pathlib import Path

import pytest

from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy, LoopHold
from chiplog.composition.postgres_application import PostgresApplicationDriver
from chiplog.platform.postgres import PostgresServer
from tests.support.composition.postgres_completion import completion, opened, prepared
from tests.support.composition.postgres_effects import PEER


async def test_driver_accepts_captured_completion_and_reopen_never_calls_model(
    postgres_server: PostgresServer,
    tmp_path: Path,
) -> None:
    runtime, binding, sealed = await prepared(postgres_server, tmp_path)
    driver = PostgresApplicationDriver(runtime)
    completed = await driver.drive_run(PEER, sealed.run_id)
    assert completed.state == "SUCCEEDED"
    assert len(runtime.model.requests) == 1
    intents = runtime.delivery_intents(PEER, sealed.run_id)
    assert len(intents) == 1
    reopened = opened(runtime, binding, HermeticModel(()))
    assert await PostgresApplicationDriver(reopened).drive_run(PEER, sealed.run_id) == completed
    assert reopened.model.requests == []
    assert reopened.delivery_intents(PEER, sealed.run_id) == intents


async def test_driver_runs_from_creation_holds_for_adoption_and_resumes_after_cancel(
    postgres_server: PostgresServer,
    tmp_path: Path,
) -> None:
    runtime, binding, _ = await prepared(postgres_server, tmp_path)
    completed = json.loads(completion(2))
    completed.update(run_id="another", turn_id="another/turn/2")
    model = HermeticModel(
        (
            json.dumps(
                {
                    "kind": "Continue",
                    "tool_calls": [
                        {
                            "call_id": "effect",
                            "tool": "request_self_effect",
                            "arguments": {"payload": "aGVsbG8=", "bundle_members": ["one"]},
                        },
                    ],
                }
            ).encode(),
            json.dumps(completed).encode(),
        )
    )
    runtime = opened(runtime, binding, model)
    await runtime.create_execution(PEER, "another", "Review", BudgetPolicy())
    driver = PostgresApplicationDriver(runtime)
    with pytest.raises(LoopHold, match="explicit user adoption"):
        await driver.drive_run(PEER, "another")
    assert len(model.requests) == 1
    call = next(
        call
        for call in runtime.initialized_calls()
        if call.call.original.original_run_id == "another"
    )
    assert runtime.call_state(call.original_call_id).state == "INITIALIZED"
    await runtime.cancel_execution_call(PEER, call.original_call_id, "user-cancel")
    result = await driver.drive_run(PEER, "another")
    assert result.state == "SUCCEEDED"
    assert len(model.requests) == 2
    assert len(runtime.delivery_intents(PEER, "another")) == 1
