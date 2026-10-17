"""Shared builders extracted without changing their behavior."""

import json

from tests.support.scheduler_execution import origin

from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy
from chiplog.composition.postgres_execution import PostgresExecutionRuntime
from chiplog.composition.postgres_planning import PostgresPlanningRuntime
from chiplog.platform.postgres import PostgresServer, UserDatabase

SECRET = b"readonly-client-fixture"


IDENTITY = {"principal_id": "principal", "credential_id": "credential", "session_id": "session"}


PEER = "local-cli"


def opened(database: UserDatabase, model: HermeticModel) -> PostgresExecutionRuntime:
    return PostgresExecutionRuntime(
        database,
        "account",
        SECRET,
        principal_id=IDENTITY["principal_id"],
        credential_id=IDENTITY["credential_id"],
        session_id=IDENTITY["session_id"],
        model=model,
        origin=origin(),
    )


async def selected_call(
    server: PostgresServer,
) -> tuple[UserDatabase, PostgresExecutionRuntime, str, HermeticModel]:
    database = server.user_database("account")
    database.provision()
    await PostgresPlanningRuntime(database, "account", SECRET).bootstrap(
        database_instance_id="readonly-db", token="token", **IDENTITY
    )
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
    model = HermeticModel((response,))
    runtime = opened(database, model)
    created = await runtime.create_execution(PEER, "run", "Read history", BudgetPolicy())
    started = await runtime.begin_execution(PEER, "run", created.head)
    captured = await runtime.capture_execution(PEER, "run", started.head)
    await runtime.seal_execution(PEER, "run", captured.head)
    call = runtime.initialized_calls()[0]
    assert call.call.classification == "READ_ONLY"
    return database, runtime, call.original_call_id, model
