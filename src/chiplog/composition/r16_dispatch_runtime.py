"""Explicit offline dispatch assembly; historical profile-7 remains unchanged."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import ClassVar, cast

from chiplog.adapters.driven.effects_hermetic import IssuedEffectSendTicket
from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.adapters.driven.loop_prompts import OwnedStaticPrompts
from chiplog.adapters.driven.loop_sqlite import SQLiteLoopStore
from chiplog.architecture.r7_runtime import R16_DISPATCH_PRODUCTION_MANIFEST
from chiplog.capabilities.agent_loop.application import AgentLoop
from chiplog.capabilities.agent_loop.contracts import EndpointSelection, ProposalDisplay
from chiplog.composition.r7_planning import _open_runtime
from chiplog.composition.r13_workspace import R13Workspace
from chiplog.composition.r16_denial_publication import R16PlanningRuntime
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform._owner_publication_contracts import BrokerPublicationResult

_RESOURCES: ContextVar[HermeticDispatchResources | None] = ContextVar(
    "dispatch_resources", default=None
)


@contextmanager
def _configured(resources: HermeticDispatchResources) -> Iterator[None]:
    token = _RESOURCES.set(resources)
    try:
        yield
    finally:
        _RESOURCES.reset(token)


class R16DispatchRuntime(R16PlanningRuntime):
    _dispatch_resources: HermeticDispatchResources
    _original_dispatch_resources: HermeticDispatchResources
    _dispatch_permits: dict[int, tuple[object, IssuedEffectSendTicket]]
    _record_contracts: ClassVar[dict[str, str]] = {
        **R16PlanningRuntime._record_contracts,
        "broker_dispatch": "chiplog.broker-dispatch.send-consumption.v1",
    }
    _record_schema_variants: ClassVar[tuple[tuple[str, str], ...]] = (
        *R16PlanningRuntime._record_schema_variants,
        ("effects", "chiplog.effects.dispatch-record.v2"),
    )

    def _bind_appender(self) -> None:
        resources = _RESOURCES.get()
        if resources is None:
            raise ValueError("dispatch assembly lacks its independently held offline resources")
        self._original_dispatch_resources = resources
        self._dispatch_resources = resources
        self._dispatch_permits = {}
        resources.bind(self._authority_gate())
        super()._bind_appender()

    def _require_dispatch_resources(self) -> HermeticDispatchResources:
        if self._dispatch_resources is not self._original_dispatch_resources:
            raise ValueError("dispatch resource identity differs from original custody")
        return self._original_dispatch_resources

    async def preview_dispatch(self, proposal_id: str) -> ProposalDisplay:
        from chiplog.composition.r16_dispatch_publication import preview_dispatch

        return await preview_dispatch(self, proposal_id)

    async def adopt_dispatch(
        self,
        peer: str,
        display_id: str,
        display_digest: str,
        act_id: str,
        worker_run_id: str,
    ) -> BrokerPublicationResult:
        from chiplog.composition.r16_dispatch_publication import publish_dispatch

        return await publish_dispatch(
            self,
            peer,
            act_id=act_id,
            worker_run_id=worker_run_id,
            operation="PUBLISH",
            subject_id=display_id,
            display_digest=display_digest,
        )

    async def authorize_dispatch(
        self,
        peer: str,
        intent_id: str,
        act_id: str,
        worker_run_id: str,
    ) -> BrokerPublicationResult:
        from chiplog.composition.r16_dispatch_publication import publish_dispatch

        return await publish_dispatch(
            self,
            peer,
            act_id=act_id,
            worker_run_id=worker_run_id,
            operation="AUTHORIZE",
            subject_id=intent_id,
        )

    async def commit_first_send(
        self,
        peer: str,
        intent_id: str,
        act_id: str,
        worker_run_id: str,
    ) -> BrokerPublicationResult:
        from chiplog.composition.r16_dispatch_publication import publish_dispatch

        return await publish_dispatch(
            self,
            peer,
            act_id=act_id,
            worker_run_id=worker_run_id,
            operation="COMMIT_FIRST",
            subject_id=intent_id,
        )

    async def _prepare_startup(self) -> None:
        from chiplog.composition.r16_dispatch_history import validate_selected_dispatch_sources
        from chiplog.composition.r16_dispatch_outbox import validate_consumptions

        with self._authority_gate().hold():
            validate_selected_dispatch_sources(self)
            validate_consumptions(self)
        await super()._prepare_startup()

    async def emit_committed(self, peer: str, intent_id: str) -> bytes | None:
        from chiplog.composition.r16_dispatch_outbox import consume_and_emit

        return await consume_and_emit(self, peer, intent_id)


@asynccontextmanager
async def open_dispatch_runtime(
    database: Path,
    *,
    resources: HermeticDispatchResources,
    model: HermeticModel | None = None,
) -> AsyncIterator[R16DispatchRuntime]:
    with _configured(resources):
        async with _open_runtime(
            database,
            tenant_id="hermetic-tenant",
            operator_secret=b"r13-hermetic-only",
            runtime_type=R16DispatchRuntime,
            manifest=R16_DISPATCH_PRODUCTION_MANIFEST,
            extra_leaves={
                "model": model if model is not None else HermeticModel(),
                "effects_transport": resources.require_original_provider(),
            },
        ) as opened:
            runtime = cast(R16DispatchRuntime, opened)
            if runtime._trust.verify() is None:
                await runtime.bootstrap(
                    database_instance_id="hermetic-database",
                    principal_id="hermetic-principal",
                    credential_id="hermetic-credential",
                    session_id="hermetic-session",
                    token="hermetic-bootstrap",
                )
            yield runtime


@asynccontextmanager
async def open_dispatch_loop(
    database: Path,
    *,
    resources: HermeticDispatchResources,
    responses: tuple[bytes, ...],
) -> AsyncIterator[AgentLoop]:
    model = HermeticModel(responses)
    async with open_dispatch_runtime(database, resources=resources, model=model) as runtime:
        model.session = runtime
        origin = EndpointSelection(
            kind="ORIGIN_EXACT",
            ingress_binding_head="hermetic-ingress-v1",
            endpoint_head="hermetic-endpoint-v1",
            endpoint_id="hermetic-local",
            provider="hermetic-local",
            recipient="hermetic-principal",
            canonical_address="local://hermetic-principal",
            credential_binding_head="hermetic-v1",
        )
        yield AgentLoop(
            SQLiteLoopStore(
                database, runtime._appender, runtime._tenant_id, "r6", authority=runtime
            ),
            model,
            OwnedStaticPrompts(),
            tenant=runtime._tenant_id,
            principal="hermetic-principal",
            origin=origin,
            contour_head="hermetic-contour-v1",
            policy_head="hermetic-policy-v1",
            worker_session=runtime.current_worker(),
            planning=runtime,
            workspace=R13Workspace(runtime),
            session=runtime,
        )
