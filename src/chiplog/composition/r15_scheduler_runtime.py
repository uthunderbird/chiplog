"""Offline R15 assembly with actual scheduler configuration and shared Run history."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import ClassVar, cast

from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.adapters.driven.loop_prompts import OwnedStaticPrompts
from chiplog.adapters.driven.loop_sqlite import SQLiteLoopStore
from chiplog.architecture.r7_runtime import R14_PRODUCTION_MANIFEST
from chiplog.capabilities.agent_loop.application import AgentLoop
from chiplog.capabilities.agent_loop.contracts import LoopRejected, LoopSnapshot
from chiplog.composition.r7_planning import _open_runtime
from chiplog.composition.r13_workspace import R13Workspace
from chiplog.composition.r14_runtime import R14PlanningRuntime
from chiplog.composition.r15_scheduler_history import read_loop_snapshot
from chiplog.composition.r15_scheduler_publication import (
    preview_configuration,
    preview_genesis,
    publish_configuration,
    publish_genesis,
)
from chiplog.composition.r15_scheduler_registry import (
    BrokerPublicationResult,
    ConfigurationGenesisCommand,
    ConfigurationTransitionCommand,
    PublicationRejected,
    SchedulerConfigurationAdoption,
    SchedulerConfigurationDraft,
    SchedulerGenesisAdoption,
    SchedulerGenesisDraft,
    _command_id,
    _origin,
    configuration_command_id,
)
from chiplog.platform.owner_publications import OwnerPublicationPending


class R15SchedulerRuntime(R14PlanningRuntime):
    _record_schema_variants: ClassVar[tuple[tuple[str, str], ...]] = (
        ("agent_loop", "chiplog.scheduler.schedule-definition.v1"),
        ("agent_loop", "chiplog.scheduler.missed-policy.v1"),
        ("agent_loop", "chiplog.scheduler.interval-bound.v1"),
    )

    def _loop_snapshot(self) -> LoopSnapshot:
        return read_loop_snapshot(self)

    async def _prepare_startup(self) -> None:
        await super()._prepare_startup()
        # Inherited recovery replays independently selected bytes before any live
        # owner starts. Complete readback rejects marker-only or partial history.
        self._loop_snapshot()

    async def preview_scheduler_genesis(
        self, peer: str, draft: SchedulerGenesisDraft
    ) -> ConfigurationGenesisCommand:
        try:
            return await preview_genesis(self, peer, draft)
        except LoopRejected:
            raise
        except (ValueError, TypeError) as error:
            raise LoopRejected("invalid scheduler configuration preview") from error

    async def adopt_scheduler_genesis(
        self, peer: str, adoption: SchedulerGenesisAdoption
    ) -> BrokerPublicationResult:
        try:
            return await publish_genesis(self, peer, adoption)
        except OwnerPublicationPending as error:
            return PublicationRejected(
                kind="HOLD",
                tenant_id=self._tenant_id,
                command_id=_command_id(adoption.adoption_act_id),
                reason=str(error),
            )
        except LoopRejected:
            raise
        except (ValueError, TypeError) as error:
            raise LoopRejected("invalid scheduler configuration adoption") from error

    async def preview_scheduler_configuration(
        self, peer: str, draft: SchedulerConfigurationDraft
    ) -> ConfigurationTransitionCommand:
        try:
            return await preview_configuration(self, peer, draft)
        except LoopRejected:
            raise
        except (ValueError, TypeError) as error:
            raise LoopRejected("invalid scheduler transition preview") from error

    async def adopt_scheduler_configuration(
        self, peer: str, adoption: SchedulerConfigurationAdoption
    ) -> BrokerPublicationResult:
        try:
            return await publish_configuration(self, peer, adoption)
        except OwnerPublicationPending as error:
            return PublicationRejected(
                kind="HOLD",
                tenant_id=self._tenant_id,
                command_id=configuration_command_id(adoption.adoption_act_id),
                reason=str(error),
            )
        except LoopRejected:
            raise
        except (ValueError, TypeError) as error:
            raise LoopRejected("invalid scheduler transition adoption") from error


class _SchedulerLoopStore(SQLiteLoopStore):
    def __init__(self, runtime: R15SchedulerRuntime) -> None:
        super().__init__(
            runtime._database,
            runtime._appender,
            runtime._tenant_id,
            "r6",
            authority=runtime,
        )
        self._runtime = runtime

    def snapshot(self) -> LoopSnapshot:
        return self._runtime._loop_snapshot()


@asynccontextmanager
async def open_r15_runtime(
    database: Path, *, model: HermeticModel | None = None
) -> AsyncIterator[R15SchedulerRuntime]:
    async with _open_runtime(
        database,
        tenant_id="hermetic-tenant",
        operator_secret=b"r13-hermetic-only",
        runtime_type=R15SchedulerRuntime,
        manifest=R14_PRODUCTION_MANIFEST,
        extra_leaves={"model": model if model is not None else HermeticModel()},
    ) as opened:
        runtime = cast(R15SchedulerRuntime, opened)
        if runtime._trust.verify() is None:
            await runtime.bootstrap(
                database_instance_id="hermetic-database",
                principal_id="hermetic-principal",
                credential_id="hermetic-credential",
                session_id="hermetic-session",
                token="hermetic-bootstrap",
            )
        runtime._loop_snapshot()
        yield runtime


@asynccontextmanager
async def open_r15_loop(
    database: Path, *, responses: tuple[bytes, ...], matches: tuple[str, ...] | None = None
) -> AsyncIterator[AgentLoop]:
    model = HermeticModel(responses, matches)
    async with open_r15_runtime(database, model=model) as runtime:
        model.session = runtime
        yield AgentLoop(
            _SchedulerLoopStore(runtime),
            model,
            OwnedStaticPrompts(),
            tenant="hermetic-tenant",
            principal="hermetic-principal",
            origin=_origin(),
            contour_head="hermetic-contour-v1",
            policy_head="hermetic-policy-v1",
            worker_session="hermetic-session",
            planning=runtime,
            workspace=R13Workspace(runtime),
            session=runtime,
        )


__all__ = ["R15SchedulerRuntime", "open_r15_loop", "open_r15_runtime"]
