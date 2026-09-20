"""Offline R14 loop assembly with the actual authenticated runtime worker.

This factory does not issue effects authority or enable expanded delivery completion.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.adapters.driven.loop_prompts import OwnedStaticPrompts
from chiplog.adapters.driven.loop_sqlite import SQLiteLoopStore
from chiplog.capabilities.agent_loop.application import AgentLoop
from chiplog.capabilities.agent_loop.contracts import EndpointSelection
from chiplog.composition.r13_workspace import R13Workspace
from chiplog.composition.r14_runtime import open_r14_runtime


@asynccontextmanager
async def open_r14_loop(
    database: Path,
    *,
    responses: tuple[bytes, ...],
    matches: tuple[str, ...] | None = None,
) -> AsyncIterator[AgentLoop]:
    tenant, principal = "hermetic-tenant", "hermetic-principal"
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
    async with open_r14_runtime(database, model=model) as runtime:
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
            worker_session=runtime.current_worker(),
            planning=runtime,
            workspace=R13Workspace(runtime),
            session=runtime,
        )


__all__ = ["open_r14_loop"]
