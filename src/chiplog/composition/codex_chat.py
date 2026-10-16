"""Explicit dialogue on the user's PostgreSQL database and durable loop."""

from __future__ import annotations

import os
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from chiplog.adapters.driven.codex_auth import CodexTokenStorage
from chiplog.adapters.driven.codex_model import CodexModel
from chiplog.adapters.driven.loop_postgres import PostgresLoopStore
from chiplog.adapters.driven.loop_prompts import OwnedStaticPrompts
from chiplog.capabilities.agent_loop.application import AgentLoop
from chiplog.capabilities.agent_loop.contracts import EndpointSelection, LoopRejected
from chiplog.capabilities.agent_loop.live_contract import LiveModelBinding
from chiplog.platform.postgres import PostgresServer, UserDatabase


class PostgresChatSession:
    """Opening a fresh client fences the previous model worker before SEND."""

    def __init__(self, database: UserDatabase) -> None:
        self.database = database
        self.worker = "local-cli:" + secrets.token_hex(16)
        with database.connect() as connection:
            database._check_identity(connection)
            connection.execute("UPDATE user_state SET worker = %s", (self.worker,))

    def current_worker(self) -> str:
        with self.database.read() as connection:
            row = connection.execute("SELECT worker FROM user_state").fetchone()
            if row is None or not isinstance(row[0], str):
                raise LoopRejected("missing current chat worker")
            return row[0]


@asynccontextmanager
async def open_codex_chat(
    storage: CodexTokenStorage, binding: LiveModelBinding
) -> AsyncIterator[AgentLoop]:
    storage.prepare()
    principal = f"uid:{os.getuid()}"
    tenant = "cli:" + binding.credential_identity
    database = PostgresServer.from_environment().user_database(tenant)
    database.provision()
    source = storage.directory / ("chat-" + binding.credential_identity + ".sqlite3")
    if source.exists():
        with database.read() as connection:
            imported = connection.execute(
                "SELECT 1 FROM migrations WHERE source_kind=%s", ("sqlite.local-chat.v1",)
            ).fetchone()
        if imported is None:
            raise LoopRejected(
                "Existing SQLite dialogue requires explicit migrate-sqlite-chat --tenant "
                + tenant
                + " --source "
                + str(source)
            )
    session = PostgresChatSession(database)
    model = CodexModel(storage, binding)
    model.session = session
    origin = EndpointSelection(
        kind="ORIGIN_EXACT",
        ingress_binding_head=principal,
        endpoint_head=principal,
        endpoint_id="local-cli",
        provider="local-cli",
        recipient=principal,
        canonical_address="local://" + principal,
        credential_binding_head=binding.credential_identity,
    )
    yield AgentLoop(
        PostgresLoopStore(database, tenant, local_chat=True),
        model,
        OwnedStaticPrompts(),
        tenant=tenant,
        principal=principal,
        origin=origin,
        contour_head="local-cli-codex.v1",
        policy_head="explicit-chat-disclosure.v1",
        worker_session="local-cli",
        session=session,
    )
