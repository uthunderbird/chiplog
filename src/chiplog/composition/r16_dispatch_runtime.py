"""Legacy SQLite dispatch compatibility assembly for historical callers.

New clients use chiplog.client.open_execution_client (native PostgreSQL).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import ClassVar

from chiplog.adapters.driven.deployment_trust._journal import IndependentTenantDecisionJournal
from chiplog.adapters.driven.effects_broker import EffectsIntegrityError
from chiplog.adapters.driven.effects_hermetic import IssuedEffectSendTicket
from chiplog.capabilities.agent_loop.contracts import (
    LoopSnapshot,
    ProposalDisplay,
)
from chiplog.composition.r14_acceptance_contracts import ACCEPTED_SCHEMA, EXECUTION_SCHEMA
from chiplog.composition.r14_call_acceptance_port import (
    AcceptedCallReceipt,
    CallAcceptanceAdoption,
    CallAcceptancePreview,
    CallAcceptanceTarget,
)
from chiplog.composition.r14_execution_runtime import R14ExecutionRuntime
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
        ("effects", "chiplog.effects.dispatch-outcome-record.v2"),
    )

    def _bind_appender(self) -> None:
        self._call_history_cache = None
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

    async def observe_dispatch_outcome(
        self, intent_id: str, raw_receipt: bytes | None = None
    ) -> BrokerPublicationResult:
        from chiplog.composition.r16_dispatch_outcomes import observe_outcome

        return await observe_outcome(self, intent_id, raw_receipt)

    async def resolve_dispatch_outcome(
        self, peer: str, intent_id: str, act_id: str
    ) -> BrokerPublicationResult:
        from chiplog.composition.r16_dispatch_outcomes import resolve_outcome

        return await resolve_outcome(self, peer, intent_id, act_id)

    async def emit_committed(self, peer: str, intent_id: str) -> bytes | None:
        from chiplog.composition.r16_dispatch_outbox import consume_and_emit

        return await consume_and_emit(self, peer, intent_id)


class ExecutionDispatchRuntime(R14ExecutionRuntime, R16DispatchRuntime):
    """Executable Run history with the original retained dispatch resource custody."""

    _record_contracts: ClassVar[dict[str, str]] = dict(R16DispatchRuntime._record_contracts)
    _record_schema_variants: ClassVar[tuple[tuple[str, str], ...]] = tuple(
        dict.fromkeys(
            (
                *R16DispatchRuntime._record_schema_variants,
                *R14ExecutionRuntime._record_schema_variants,
                ("agent_loop", ACCEPTED_SCHEMA),
                ("agent_loop", EXECUTION_SCHEMA),
            )
        )
    )

    def _bind_appender(self) -> None:
        super()._bind_appender()
        try:
            self._call_preview_journal = IndependentTenantDecisionJournal.for_authority_bundle(
                self._database.with_suffix(".call-previews"), authority_gate=self._authority_gate()
            )
        except (ValueError, RuntimeError, OSError) as error:
            raise EffectsIntegrityError(
                f"call-preview.open tenant={self._tenant_id} record=<journal>"
            ) from error
        self._original_call_preview_journal = self._call_preview_journal
        from chiplog.composition.r14_call_preview import validate_preview_custody_database

        validate_preview_custody_database(self)

    def _require_call_preview_journal(self) -> IndependentTenantDecisionJournal:
        journal = self._call_preview_journal
        if (
            journal is not self._original_call_preview_journal
            or type(journal) is not IndependentTenantDecisionJournal
            or journal.authority_gate != self._authority_gate()
            or journal._path != self._database.with_suffix(".call-previews").resolve()
            or any(name in vars(journal) for name in ("append", "entries", "physical_sources"))
        ):
            raise EffectsIntegrityError(
                f"call-preview.journal tenant={self._tenant_id} "
                "record=<journal>: substituted custody"
            )
        try:
            journal.physical_sources()
        except (ValueError, RuntimeError, OSError) as error:
            raise EffectsIntegrityError(
                f"call-preview.journal tenant={self._tenant_id} record=<journal>"
            ) from error
        return journal

    async def _prepare_startup(self) -> None:
        from chiplog.composition.r14_call_preview import read_call_previews

        await super()._prepare_startup()
        with self._authority_gate().hold():
            read_call_previews(self)

    def _loop_snapshot(self) -> LoopSnapshot:
        from chiplog.composition.r14_loop_history import read_loop_snapshot

        return read_loop_snapshot(self)

    async def accept_call(self, peer: str, adoption: CallAcceptanceAdoption) -> AcceptedCallReceipt:
        from chiplog.composition.r14_call_publication import accept_call

        return await accept_call(self, peer, adoption)

    async def preview_call_acceptance(
        self, peer: str, target: CallAcceptanceTarget
    ) -> CallAcceptancePreview:
        from chiplog.composition.r14_call_preview import preview_call_acceptance

        return await preview_call_acceptance(self, peer, target)
