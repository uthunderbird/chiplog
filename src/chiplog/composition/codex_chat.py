"""Explicit local dialogue using the existing durable loop and isolated owner."""

from __future__ import annotations

import os
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast

from chiplog.adapters.driven.codex_auth import CodexTokenStorage
from chiplog.adapters.driven.codex_model import CodexModel
from chiplog.adapters.driven.loop_prompts import OwnedStaticPrompts
from chiplog.adapters.driven.loop_sqlite import SQLiteLoopStore
from chiplog.architecture.r7_runtime import CODEX_CLI_MANIFEST
from chiplog.capabilities.agent_loop.application import AgentLoop
from chiplog.capabilities.agent_loop.contracts import (
    DurableCompanion,
    EndpointSelection,
    LoopRejected,
    RunRecord,
)
from chiplog.capabilities.agent_loop.live_contract import LiveModelBinding
from chiplog.composition.r7_planning import _open_runtime
from chiplog.composition.r13_runtime import R13Runtime


class LocalChatRuntime(R13Runtime):
    def companions(self, record: RunRecord) -> tuple[DurableCompanion, ...]:
        # Dialogue history is read from accepted Run records, not the hermetic workspace.
        return ()

    def validate(self, previous: RunRecord | None, proposed: RunRecord) -> None:
        if (
            proposed.policy.live_model is None
            or proposed.origin.provider != "local-cli"
            or proposed.tenant != self._tenant_id
            or proposed.planning_receipts
        ):
            raise LoopRejected("local dialogue requires a live binding and no planning authority")
        super().validate(previous, proposed)


def _operator_key(directory: Path) -> bytes:
    path = directory / "operator.key"
    try:
        with path.open("xb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(secrets.token_bytes(32))
    except FileExistsError:
        pass
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise LoopRejected("operator key must be a private regular file")
    value = path.read_bytes()
    if len(value) != 32:
        raise LoopRejected("operator key is corrupt")
    return value


@asynccontextmanager
async def open_codex_chat(
    storage: CodexTokenStorage, binding: LiveModelBinding
) -> AsyncIterator[AgentLoop]:
    storage.prepare()
    principal = f"uid:{os.getuid()}"
    tenant = "cli:" + binding.credential_identity
    database = storage.directory / ("chat-" + binding.credential_identity + ".sqlite3")
    model = CodexModel(storage, binding)
    async with _open_runtime(
        database,
        tenant_id=tenant,
        operator_secret=_operator_key(storage.directory),
        runtime_type=LocalChatRuntime,
        manifest=CODEX_CLI_MANIFEST,
        extra_leaves={"model": model},
    ) as opened:
        runtime = cast(LocalChatRuntime, opened)
        if runtime._trust.verify() is None:
            await runtime.bootstrap(
                database_instance_id="local-chat:" + binding.credential_identity,
                principal_id=principal,
                credential_id=principal,
                session_id="local-cli",
                token=secrets.token_hex(32),
            )
        model.session = runtime
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
            SQLiteLoopStore(database, runtime._appender, tenant, "r6", authority=runtime),
            model,
            OwnedStaticPrompts(),
            tenant=tenant,
            principal=principal,
            origin=origin,
            contour_head="local-cli-codex.v1",
            policy_head="explicit-chat-disclosure.v1",
            worker_session="local-cli",
            session=runtime,
        )
