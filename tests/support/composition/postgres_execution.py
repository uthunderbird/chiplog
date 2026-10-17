"""Shared builders extracted without changing their behavior."""

import hashlib
from typing import TypedDict

from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.capabilities.agent_loop.delivery_contracts import (
    ExactHead,
    OriginSelection,
    ProviderRecipient,
)
from chiplog.composition.postgres_execution import PostgresExecutionRuntime
from chiplog.composition.postgres_planning import PostgresPlanningRuntime
from chiplog.platform.postgres import PostgresServer, UserDatabase

SECRET = b"postgres-execution-fixture"


class RuntimeIdentity(TypedDict):
    principal_id: str
    credential_id: str
    session_id: str


IDENTITY: RuntimeIdentity = {"principal_id": "p", "credential_id": "c", "session_id": "s"}


def origin() -> OriginSelection:
    def exact(identity: str) -> ExactHead:
        return ExactHead(
            identity=identity,
            head=identity,
            fingerprint=hashlib.sha256(identity.encode()).hexdigest(),
        )

    return OriginSelection(
        ingress_binding=exact("ingress"),
        recipient=ProviderRecipient(
            provider_id="local-cli",
            account_id="t",
            recipient_id="p",
            endpoint=exact("hermetic-model"),
            canonical_address=b"local://p",
            credential_binding=exact("c"),
        ),
    )


async def bootstrap(server: PostgresServer) -> UserDatabase:
    database = server.user_database("t")
    database.provision()
    await PostgresPlanningRuntime(database, "t", SECRET).bootstrap(
        database_instance_id="db", token="token", **IDENTITY
    )
    return database


def opened(database: UserDatabase, model: HermeticModel) -> PostgresExecutionRuntime:
    return PostgresExecutionRuntime(database, "t", SECRET, **IDENTITY, model=model, origin=origin())
