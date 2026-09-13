"""Canonical offline R13 loop assembly for production and evaluation entrypoints."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.adapters.driven.loop_prompts import OwnedStaticPrompts
from chiplog.adapters.driven.loop_sqlite import SQLiteLoopStore
from chiplog.capabilities.agent_loop.application import AgentLoop
from chiplog.capabilities.agent_loop.contracts import EndpointSelection, LoopRejected
from chiplog.composition.r13_runtime import open_r13_runtime
from chiplog.composition.r13_workspace import R13Workspace


@asynccontextmanager
async def open_r13_loop(
    database: Path,
    *,
    responses: tuple[bytes, ...],
    matches: tuple[str, ...] | None = None,
    tenant: str = "hermetic-tenant",
    principal: str = "hermetic-principal",
    worker_session: str = "hermetic-session",
) -> AsyncIterator[AgentLoop]:
    if (
        tenant != "hermetic-tenant"
        or principal != "hermetic-principal"
        or worker_session != "hermetic-session"
    ):
        raise LoopRejected("unregistered offline peer; real exposure HOLD")
    origin = EndpointSelection(
        kind="ORIGIN_EXACT",
        ingress_binding_head="hermetic-ingress-v1",
        endpoint_head="hermetic-endpoint-v1",
        endpoint_id="hermetic-local",
        provider="hermetic-local",
        recipient=principal,
        canonical_address="local://hermetic-principal",
        credential_binding_head="hermetic-v1",
    )
    model = HermeticModel(responses, matches)
    async with open_r13_runtime(database, model=model) as runtime:
        model.session = runtime
        yield AgentLoop(
            SQLiteLoopStore(database, runtime._appender, tenant, "r6", authority=runtime),
            model,
            OwnedStaticPrompts(),
            tenant=tenant,
            principal=principal,
            origin=origin,
            contour_head="hermetic-contour-v1",
            policy_head="hermetic-policy-v1",
            worker_session=worker_session,
            planning=runtime,
            workspace=R13Workspace(runtime),
            session=runtime,
        )


__all__ = ["open_r13_loop"]
