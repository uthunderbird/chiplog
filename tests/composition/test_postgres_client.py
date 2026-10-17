import sqlite3
from pathlib import Path
from typing import NoReturn

import pytest

from chiplog.adapters.driven.effects_hermetic import HermeticEffectsProvider
from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy
from chiplog.client import open_execution_client
from chiplog.composition.postgres_effects import PostgresEffectsBinding
from chiplog.composition.postgres_planning import PostgresPlanningRuntime
from chiplog.platform.postgres import PostgresServer
from tests.support.composition.postgres_completion import completion, origin
from tests.support.composition.postgres_effects import IDENTITY, KEY, PEER
from tests.support.composition.postgres_execution import SECRET
from tests.support.composition.postgres_planning import verifier


async def test_default_public_client_runs_native_postgres_completion_and_reopens(
    postgres_server: PostgresServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_sqlite(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("default native client attempted SQLite I/O")

    monkeypatch.setattr(sqlite3, "connect", reject_sqlite)
    database = postgres_server.user_database("t")
    database.provision()
    await PostgresPlanningRuntime(database, "t", SECRET).bootstrap(
        database_instance_id="db", token="token", **IDENTITY
    )
    transport = PostgresEffectsBinding(
        HermeticEffectsProvider(
            receipt_key=KEY,
            scenarios=("CONFIRM",),
            journal_path=tmp_path / "transfers.jsonl",
            observation_journal_path=tmp_path / "observations.jsonl",
        ),
        KEY,
    )
    async with open_execution_client(
        tenant_id="t",
        **IDENTITY,
        operator_secret=SECRET,
        model=HermeticModel((completion(),)),
        origin=origin(transport),
        effects_binding=transport,
        authenticate_entitlement=verifier,
    ) as client:
        assert client.database.name == database.name
        created = await client.create_execution(
            PEER, "run", "hello", BudgetPolicy(), execution_version="v3"
        )
        started = await client.begin_execution(PEER, "run", created.head)
        captured = await client.capture_execution(PEER, "run", started.head)
        source = await client.seal_execution(PEER, "run", captured.head)
        accepted = await client.accept_completion(PEER, "run", source.head)
        intents = client.delivery_intents(PEER, "run")
        assert len(intents) == 1 and intents[0].acceptance_head == accepted.head
        assert client.execution_record("run").state == "SUCCEEDED"
        before = client.snapshot()
    async with open_execution_client(
        tenant_id="t",
        **IDENTITY,
        operator_secret=SECRET,
        model=HermeticModel(),
        origin=origin(transport),
        effects_binding=transport,
        authenticate_entitlement=verifier,
    ) as client:
        assert await client.accept_completion(PEER, "run", source.head) == accepted
        assert client.delivery_intents(PEER, "run") == intents
        assert client.snapshot() == before
        assert transport.provider.transfers == ()
