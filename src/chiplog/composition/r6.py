"""R6 composition of the CLI, R4 trust, R5 planning, and R3 SQLite writer."""

from __future__ import annotations

import asyncio
import hashlib
import os
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from chiplog.adapters.driven.deployment_trust import (
    IndependentTenantDecisionJournal,
    SQLiteTrustMaterializer,
)
from chiplog.adapters.driven.planning_sqlite import (
    SQLitePlanningProjection,
    SQLitePlanningRepository,
)
from chiplog.capabilities.deployment_trust import (
    AuthenticationRequest,
    TrustAuthenticator,
    TrustReference,
    TrustReferenceRevalidation,
    TrustRevalidator,
)
from chiplog.capabilities.deployment_trust._model import DeploymentTrustService
from chiplog.capabilities.planning import (
    CreateIntentionLine,
    InvocationContext,
    PlanningCommands,
    PlanningOutcome,
    PlanningTrustDecision,
    PlanningTrustReference,
)
from chiplog.capabilities.planning._planning import _PlanningUseCase
from chiplog.capabilities.projections._planning import (
    _PlanningProjectionRebuilder,
    _PlanningReadStore,
)
from chiplog.domain_primitives import PermissionScope, PrincipalId, RecordId, TenantId
from chiplog.platform._sqlite import EventAppender, SQLiteMaterializer


class R4PlanningTrustBridge:
    """Translate only a complete R5 reference to the frozen R4 port."""

    def __init__(self, trust: TrustRevalidator, trace: Callable[[str], None]) -> None:
        self._trust = trust
        self._trace = trace

    def revalidate(
        self, reference: PlanningTrustReference, operation: str, subject_id: RecordId
    ) -> PlanningTrustDecision:
        self._trace("r4.revalidate")
        decision = self._trust.revalidate(
            TrustReferenceRevalidation(
                TrustReference(
                    reference.tenant_id,
                    reference.principal_id,
                    reference.contour,
                    reference.credential_head,
                    reference.session_head,
                    reference.source_head,
                    reference.trust_head,
                    reference.materialization_head,
                    reference.freshness_sequence,
                    reference.peer_credential,
                ),
                operation,
                subject_id,
            )
        )
        return PlanningTrustDecision(decision.disposition, decision.reason)


@dataclass(frozen=True)
class R6CreateRequest:
    authentication: AuthenticationRequest
    tenant_id: str
    principal_id: str
    command: CreateIntentionLine


class R6PlanningPort(Protocol):
    async def create(self, request: R6CreateRequest) -> PlanningOutcome: ...


class _R6PublicComponent:
    def __init__(
        self,
        authenticator: TrustAuthenticator,
        planning: PlanningCommands,
        peer_supplier: Callable[[], str],
    ) -> None:
        self._authenticator = authenticator
        self._planning = planning
        self._peer_supplier = peer_supplier

    async def create(self, request: R6CreateRequest) -> PlanningOutcome:
        if request.authentication.peer_credential != self._peer_supplier():
            return PlanningOutcome("DENIED", None, "peer credential does not match local peer")
        decision = self._authenticator.authenticate(request.authentication)
        if decision.disposition != "VALID" or decision.reference is None:
            disposition = (
                "INDETERMINATE" if decision.disposition == "VALID" else decision.disposition
            )
            return PlanningOutcome(disposition, None, decision.reason)
        reference = decision.reference
        if (
            reference.tenant_id.value != request.tenant_id
            or reference.principal_id.value != request.principal_id
        ):
            return PlanningOutcome(
                "DENIED", None, "caller identity does not match authenticated trust"
            )
        context = InvocationContext(
            reference.tenant_id,
            reference.principal_id,
            PermissionScope("planning.create_intention_line"),
            PlanningTrustReference(
                reference.tenant_id,
                reference.principal_id,
                reference.contour,
                reference.credential_head,
                reference.session_head,
                reference.source_head,
                reference.trust_head,
                reference.materialization_head,
                reference.freshness_sequence,
                reference.peer_credential,
            ),
        )
        return await asyncio.to_thread(self._planning.execute, context, request.command)


def build_r6_component(
    authenticator: TrustAuthenticator,
    planning: PlanningCommands,
    peer_supplier: Callable[[], str],
) -> R6PlanningPort:
    """Public R6 inbound composition seam used by V4 components and the CLI runtime."""
    return _R6PublicComponent(authenticator, planning, peer_supplier)


class R6Runtime:
    def __init__(
        self,
        trust: DeploymentTrustService,
        repository: SQLitePlanningRepository,
    ) -> None:
        self._trust = trust
        self._repository = repository
        self._call_trace: list[str] = []
        bridge = R4PlanningTrustBridge(trust, self._call_trace.append)
        self._planning = _PlanningUseCase(repository, bridge)
        self._component = build_r6_component(trust, self._planning, lambda: f"uid:{os.getuid()}")
        repository.set_trace(self._call_trace.append)
        repository.set_commit_revalidator(bridge.revalidate)

    @property
    def call_trace(self) -> tuple[str, ...]:
        return tuple(self._call_trace)

    async def bootstrap(
        self,
        *,
        tenant_id: str,
        database_instance_id: str,
        principal_id: str,
        credential_id: str,
        session_id: str,
        token: str,
    ) -> None:
        if self._trust.state is None:
            self._trust.initialize(tenant_id, database_instance_id)
        peer = f"uid:{os.getuid()}"
        self._trust.bootstrap(
            token_fingerprint=hashlib.sha256(token.encode()).hexdigest(),
            peer=peer,
            expected_peer=peer,
            principal_id=PrincipalId(principal_id),
            credential_id=credential_id,
            session_id=session_id,
            recovery_verifier=hashlib.sha256(b"r6-recovery-not-a-runtime-credential").hexdigest(),
        )
        await asyncio.to_thread(self._repository.ensure_fence, TenantId(tenant_id))

    async def create(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        credential_id: str,
        session_id: str,
        command: CreateIntentionLine,
    ) -> PlanningOutcome:
        self._call_trace.append("r4.authenticate")
        self._call_trace.append("planning.inbound")
        return await self._component.create(
            R6CreateRequest(
                AuthenticationRequest(
                    "CLI", credential_id, session_id, "", None, f"uid:{os.getuid()}"
                ),
                tenant_id,
                principal_id,
                command,
            )
        )

    def render(
        self, *, tenant_id: str, principal_id: str, credential_id: str, session_id: str
    ) -> tuple[str, ...]:
        self._call_trace.append("r4.authenticate")
        decision = self._trust.authenticate(
            AuthenticationRequest("CLI", credential_id, session_id, "", None, f"uid:{os.getuid()}")
        )
        if (
            decision.disposition != "VALID"
            or decision.reference is None
            or decision.reference.tenant_id.value != tenant_id
            or decision.reference.principal_id.value != principal_id
        ):
            raise PermissionError("DENIED: caller identity does not match authenticated trust")
        self._call_trace.append("projections.rebuild")
        return SQLitePlanningProjection(
            self._repository,
            lambda tenant: _PlanningProjectionRebuilder(
                cast(_PlanningReadStore, self._repository)
            ).rebuild(tenant),
        ).render(tenant_id)


@asynccontextmanager
async def open_r6_runtime(database: Path, *, operator_secret: bytes) -> AsyncIterator[R6Runtime]:
    journal = IndependentTenantDecisionJournal(
        database.with_suffix(database.suffix + ".trust-journal")
    )
    trust_store = SQLiteTrustMaterializer(database.with_suffix(database.suffix + ".trust.sqlite3"))
    trust = DeploymentTrustService(
        journal,
        trust_store,
        operator_key_id="r6-local-operator",
        operator_secret=operator_secret,
        broker_secret=hashlib.sha256(operator_secret + b"/broker").digest(),
    )
    if journal.entries():
        trust.recover()
    with SQLiteMaterializer(
        database, record_contracts={"planning": "chiplog.planning.record.v1"}
    ) as store:
        async with EventAppender(store, capacity=4) as appender:
            runtime = R6Runtime(
                trust,
                SQLitePlanningRepository(database, appender, asyncio.get_running_loop()),
            )
            yield runtime
    trust_store.close()
