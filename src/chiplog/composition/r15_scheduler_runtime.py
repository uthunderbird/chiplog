"""Offline R15 assembly with actual scheduler configuration and shared Run history."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, cast

from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.adapters.driven.loop_prompts import OwnedStaticPrompts
from chiplog.adapters.driven.loop_sqlite import SQLiteLoopStore
from chiplog.architecture.r7_runtime import R15_MANDATE_PRODUCTION_MANIFEST
from chiplog.capabilities.agent_loop.application import AgentLoop
from chiplog.capabilities.agent_loop.contracts import LoopRejected, LoopSnapshot, RunRecord
from chiplog.composition.r7_planning import _open_runtime
from chiplog.composition.r13_workspace import R13Workspace
from chiplog.composition.r14_runtime import R14PlanningRuntime
from chiplog.composition.r15_interval_service import publish_service_interval
from chiplog.composition.r15_lease_policy import LeasePolicyAdoptionRequest
from chiplog.composition.r15_lease_publication import (
    LeaseExecutionSession,
    acquire,
    adopt_lease_policy,
    open_execution_session,
    validate_selected_lease_decision,
)
from chiplog.composition.r15_lease_service_auth import LeaseExecutionAuthority
from chiplog.composition.r15_mandate_contracts import MandateClockPreview, MandateIssuanceAdoption
from chiplog.composition.r15_mandate_publication import preview_mandate_clock, publish_mandate
from chiplog.composition.r15_resolution_contracts import (
    ResolutionPolicyAdoption,
    ResolutionPolicyDraft,
    ResolutionPolicyPreview,
)
from chiplog.composition.r15_resolution_evidence import (
    resolution_id,
    validate_selected_resolution,
)
from chiplog.composition.r15_resolution_runtime import preview_resolution, publish_resolution
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
from chiplog.composition.r15_tick_clock_v1 import SchedulerClock, registered_clock_source
from chiplog.composition.r15_tick_contracts import (
    TickClockPort,
    TickPolicyAdoption,
    TickPolicyDraft,
    TickPolicyPreview,
)
from chiplog.composition.r15_tick_evidence import tick_id, validate_selected_tick
from chiplog.composition.r15_tick_runtime import preview_tick, publish_tick
from chiplog.platform.owner_publications import OwnerPublicationPending

if TYPE_CHECKING:
    from chiplog.composition.r15_interval_policy import IntervalPolicyAdoptionRequest
    from chiplog.composition.r15_interval_service_auth import (
        IntervalExecutionAuthority,
        IntervalServiceSession,
    )


class R15SchedulerRuntime(R14PlanningRuntime):
    _tick_clock: TickClockPort
    _mandate_clock_issuances: dict[str, object]
    _lease_execution_sessions: list[LeaseExecutionSession]
    _interval_service_sessions: list[object]
    _record_contracts: ClassVar[dict[str, str]] = {
        **R14PlanningRuntime._record_contracts,
        "broker_scheduler": "chiplog.r15.lease-policy-adoption.v1",
    }
    _record_schema_variants: ClassVar[tuple[tuple[str, str], ...]] = (
        *R14PlanningRuntime._record_schema_variants,
        ("agent_loop", "chiplog.scheduler.lease-transition.v1"),
        ("broker_scheduler", "chiplog.r15.lease-execution-authority.v2"),
        ("broker_scheduler", "chiplog.r15.interval-service-adoption.v3"),
        ("broker_scheduler", "chiplog.r15.interval-execution-authority.v3"),
        ("agent_loop", "chiplog.scheduler.schedule-definition.v1"),
        ("agent_loop", "chiplog.scheduler.missed-policy.v1"),
        ("agent_loop", "chiplog.scheduler.interval-bound.v1"),
        ("agent_loop", "chiplog.scheduler.system-mandate.v2"),
        ("agent_loop", "chiplog.scheduler.mandate-budget.v1"),
        *(
            ("agent_loop", "chiplog.scheduler." + kind + ".v1")
            for kind in (
                "interval-parent",
                "interval-result",
                "overflow-hold",
                "overflow-resolution",
                "batch-primitive",
                "stable-lineage",
                "physical-root",
                "physical-selector",
                "root-lease-genesis",
                "run-initialization",
                "root-run-reciprocal",
                "occurrence-disposition",
                "coalesced-aggregate",
                "epoch-companion",
            )
        ),
    )

    def _loop_snapshot(self) -> LoopSnapshot:
        return read_loop_snapshot(self)

    def validate(self, previous: RunRecord | None, proposed: RunRecord) -> None:
        if proposed.root_binding != "NOT_APPLICABLE":
            raise LoopRejected(
                "scheduler worker publication requires registered live lease fencing"
            )
        super().validate(previous, proposed)

    async def _prepare_startup(self) -> None:
        from chiplog.composition.r15_interval_policy_publication import (
            validate_selected_interval_policy_decision,
            validate_selected_interval_service_decision,
        )

        with self._authority_gate().hold():
            decisions = self._owner_decisions().snapshot().decisions
            for decision in decisions:
                validate_selected_tick(decision)
                validate_selected_lease_decision(decision, decisions)
                validate_selected_interval_policy_decision(decision, decisions)
                validate_selected_interval_service_decision(decision, decisions)
                validate_selected_resolution(decision)
        await super()._prepare_startup()
        # Inherited recovery replays independently selected bytes before any live
        # owner starts. Complete readback rejects marker-only or partial history.
        self._loop_snapshot()

    async def adopt_scheduler_lease_policy(
        self,
        peer: str,
        request: LeasePolicyAdoptionRequest,
        execution_authority: LeaseExecutionAuthority,
    ) -> BrokerPublicationResult:
        return await adopt_lease_policy(self, peer, request, execution_authority)

    async def open_scheduler_execution_session(self, peer: str) -> LeaseExecutionSession:
        return await open_execution_session(self, peer)

    async def acquire_scheduler_lease(
        self,
        session: LeaseExecutionSession,
        root_id: str,
        command_id: str,
        duration_ns: int = 60_000_000_000,
        *,
        signature: bytes = b"",
    ) -> BrokerPublicationResult:
        return await acquire(self, session, root_id, command_id, duration_ns, signature)

    async def preview_scheduler_tick_policy(
        self, peer: str, draft: TickPolicyDraft
    ) -> TickPolicyPreview:
        return await preview_tick(self, peer, draft)

    async def preview_scheduler_mandate_clock(
        self, peer: str, adoption_act_id: str, scope_without_proof_bytes: bytes
    ) -> MandateClockPreview:
        return await preview_mandate_clock(self, peer, adoption_act_id, scope_without_proof_bytes)

    async def adopt_scheduler_mandate(
        self, peer: str, adoption: MandateIssuanceAdoption
    ) -> BrokerPublicationResult:
        return await publish_mandate(self, peer, adoption)

    async def adopt_scheduler_tick(
        self, peer: str, adoption: TickPolicyAdoption
    ) -> BrokerPublicationResult:
        try:
            return await publish_tick(self, peer, adoption)
        except OwnerPublicationPending as error:
            return PublicationRejected(
                kind="HOLD",
                tenant_id=self._tenant_id,
                command_id=tick_id(adoption.adoption_act_id),
                reason=str(error),
            )
        except (ValueError, TypeError) as error:
            raise LoopRejected("invalid scheduler tick: " + str(error)) from error

    async def preview_scheduler_resolution_policy(
        self, peer: str, draft: ResolutionPolicyDraft
    ) -> ResolutionPolicyPreview:
        return await preview_resolution(self, peer, draft)

    async def adopt_scheduler_resolution(
        self, peer: str, adoption: ResolutionPolicyAdoption
    ) -> BrokerPublicationResult:
        try:
            return await publish_resolution(self, peer, adoption)
        except OwnerPublicationPending as error:
            return PublicationRejected(
                kind="HOLD",
                tenant_id=self._tenant_id,
                command_id=resolution_id(adoption.adoption_act_id),
                reason=str(error),
            )
        except (ValueError, TypeError) as error:
            raise LoopRejected("invalid scheduler resolution: " + str(error)) from error

    async def open_scheduler_interval_service_session(self, peer: str) -> IntervalServiceSession:
        from chiplog.composition.r15_interval_service_auth import open_interval_service_session

        return await open_interval_service_session(self, peer)

    async def adopt_scheduler_interval_service_policy(
        self,
        peer: str,
        request: IntervalPolicyAdoptionRequest,
        execution_authority: IntervalExecutionAuthority,
    ) -> BrokerPublicationResult:
        from chiplog.composition.r15_interval_policy_publication import adopt_interval_policy

        return await adopt_interval_policy(self, peer, request, execution_authority)

    async def decide_scheduler_interval(
        self,
        session: IntervalServiceSession,
        schedule_id: str,
        act_id: str,
        signature: bytes,
    ) -> BrokerPublicationResult:
        try:
            return await publish_service_interval(self, session, schedule_id, act_id, signature)
        except OwnerPublicationPending as error:
            from chiplog.composition.r15_interval_service import service_interval_id

            return PublicationRejected(
                kind="HOLD",
                tenant_id=self._tenant_id,
                command_id=service_interval_id(act_id, session.authority.principal_id),
                reason=str(error),
            )

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
    database: Path, *, model: HermeticModel | None = None, clock: TickClockPort | None = None
) -> AsyncIterator[R15SchedulerRuntime]:
    async with _open_runtime(
        database,
        tenant_id="hermetic-tenant",
        operator_secret=b"r13-hermetic-only",
        runtime_type=R15SchedulerRuntime,
        manifest=R15_MANDATE_PRODUCTION_MANIFEST,
        extra_leaves={"model": model if model is not None else HermeticModel()},
    ) as opened:
        runtime = cast(R15SchedulerRuntime, opened)
        runtime._tick_clock = clock if clock is not None else SchedulerClock()
        runtime._mandate_clock_issuances = {}
        runtime._lease_execution_sessions = []
        runtime._interval_service_sessions = []
        registered_clock_source(runtime._tick_clock)
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
