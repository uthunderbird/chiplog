"""Authenticated planning on PostgreSQL, with default-HOLD deployment eligibility."""

from __future__ import annotations

import hashlib
import os
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import fields
from typing import cast

from chiplog.adapters.driven.deployment_trust.postgres import PostgresTrustStore
from chiplog.adapters.driven.planning_postgres import PostgresPlanningRepository
from chiplog.capabilities.deployment_trust import (
    AuthenticationRequest,
    TrustReference,
    TrustReferenceRevalidation,
)
from chiplog.capabilities.deployment_trust._model import DeploymentTrustService
from chiplog.capabilities.planning import (
    CreateIntentionLine,
    InvocationContext,
    PlanningOutcome,
    PlanningTrustDecision,
    PlanningTrustReference,
)
from chiplog.capabilities.planning._planning import _PlanningUseCase
from chiplog.capabilities.projections._planning import (
    _PlanningProjectionRebuilder,
    _PlanningReadStore,
)
from chiplog.composition.r8 import command_bytes
from chiplog.domain_primitives import PermissionScope, PrincipalId, RecordId, TenantId
from chiplog.platform.deployment_gate import CurrentEntitlement, DeploymentGateRequest
from chiplog.platform.postgres import Connection, PostgresServer, UserDatabase
from chiplog.platform.postgres_gate import PostgresDeploymentGate, canonical


class _TrustBridge:
    def __init__(self, trust: DeploymentTrustService) -> None:
        self.trust = trust

    def revalidate(
        self,
        reference: PlanningTrustReference,
        operation: str,
        subject_id: RecordId,
    ) -> PlanningTrustDecision:
        decision = self.trust.revalidate(
            TrustReferenceRevalidation(
                TrustReference(
                    **{field.name: getattr(reference, field.name) for field in fields(reference)}
                ),
                operation,
                subject_id,
            )
        )
        return PlanningTrustDecision(decision.disposition, decision.reason)


class PostgresPlanningRuntime:
    def __init__(
        self,
        database: UserDatabase,
        tenant: str,
        operator_secret: bytes,
        authenticate_entitlement: Callable[[bytes, bytes], bool] | None = None,
    ) -> None:
        if not operator_secret:
            raise ValueError("operator secret is required")
        self.database, self.tenant = database, TenantId(tenant)
        self._secret = operator_secret
        self._authenticate_entitlement = authenticate_entitlement

    @contextmanager
    def _transaction(self) -> Iterator[tuple[Connection, DeploymentTrustService]]:
        with self.database.connect() as connection:
            self.database._check_identity(connection)
            connection.execute("SELECT head FROM user_state FOR UPDATE").fetchone()
            ports = PostgresTrustStore(connection, self.tenant.value)
            trust = DeploymentTrustService(
                ports,
                ports,
                operator_key_id="postgres-operator.v1",
                operator_secret=self._secret,
                broker_secret=hashlib.sha256(b"postgres-broker\x00" + self._secret).digest(),
                journal_identity="postgres-user:" + self.database.name,
            )
            trust.recover()
            if trust.state is not None and trust.state.tenant_id != self.tenant.value:
                raise PermissionError("trust tenant mismatch")
            yield connection, trust

    async def bootstrap(
        self,
        *,
        database_instance_id: str,
        principal_id: str,
        credential_id: str,
        session_id: str,
        token: str,
    ) -> None:
        if not all((database_instance_id, principal_id, credential_id, session_id, token)):
            raise ValueError("bootstrap identities and token are required")
        with self._transaction() as (_connection, trust):
            if trust.state is None:
                trust.initialize(self.tenant.value, database_instance_id)
            peer = f"uid:{os.getuid()}"
            trust.bootstrap(
                token_fingerprint=hashlib.sha256(token.encode()).hexdigest(),
                peer=peer,
                expected_peer=peer,
                principal_id=PrincipalId(principal_id),
                credential_id=credential_id,
                session_id=session_id,
                recovery_verifier=hashlib.sha256(
                    b"r6-recovery-not-a-runtime-credential"
                ).hexdigest(),
            )

    def _context(
        self,
        trust: DeploymentTrustService,
        principal: str,
        credential: str,
        session: str,
    ) -> InvocationContext:
        decision = trust.authenticate(
            AuthenticationRequest(
                "CLI",
                credential,
                session,
                "",
                None,
                f"uid:{os.getuid()}",
            )
        )
        reference = decision.reference
        if (
            decision.disposition != "VALID"
            or reference is None
            or reference.tenant_id != self.tenant
            or reference.principal_id.value != principal
        ):
            raise PermissionError("caller does not match current authenticated trust")
        return InvocationContext(
            self.tenant,
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

    async def create(
        self,
        *,
        principal_id: str,
        credential_id: str,
        session_id: str,
        command: CreateIntentionLine,
        gate_request: DeploymentGateRequest | None = None,
    ) -> PlanningOutcome:
        with self._transaction() as (connection, trust):
            return self.publish_plan_in_transaction(
                connection,
                trust,
                principal_id=principal_id,
                credential_id=credential_id,
                session_id=session_id,
                command=command,
                gate_request=gate_request,
            )

    def publish_plan_in_transaction(
        self,
        connection: Connection,
        trust: DeploymentTrustService,
        *,
        principal_id: str,
        credential_id: str,
        session_id: str,
        command: CreateIntentionLine,
        gate_request: DeploymentGateRequest | None = None,
    ) -> PlanningOutcome:
        """Publish on the caller's locked transaction; never commit independently.

        Companion owners must raise on denial or companion failure so the outer
        transaction rolls back both publications and their gate handoffs.
        """
        context = self._context(trust, principal_id, credential_id, session_id)
        if any(
            item.tenant_id != self.tenant
            for item in (command.command_id, command.intention_line_id, command.revision_id)
        ):
            return PlanningOutcome("DENIED", None, "foreign tenant identifier")
        if gate_request is None or gate_request.operation_id != command.command_id.value:
            return PlanningOutcome("DENIED", None, "HOLD: exact deployment entitlement required")
        gate = PostgresDeploymentGate(connection, self.tenant.value, self._authenticate_entitlement)
        payload = command_bytes(command)
        try:
            fresh = gate.require(gate_request, "planning.create", payload)
        except PermissionError as error:
            return PlanningOutcome("DENIED", None, str(error))
        repository = PostgresPlanningRepository(self.database, self.tenant, connection)
        outcome = _PlanningUseCase(repository, _TrustBridge(trust)).execute(context, command)
        if outcome.disposition == "COMMITTED":
            if not fresh:
                raise RuntimeError("recorded deployment handoff has no committed planning batch")
            gate.crossed(gate_request, payload)
        return outcome

    async def render(
        self,
        *,
        principal_id: str,
        credential_id: str,
        session_id: str,
        gate_request: DeploymentGateRequest | None = None,
    ) -> tuple[str, ...]:
        with self._transaction() as (connection, trust):
            self._context(trust, principal_id, credential_id, session_id)
            if gate_request is None:
                raise PermissionError("HOLD: exact deployment entitlement required for disclosure")
            repository = PostgresPlanningRepository(self.database, self.tenant, connection)
            publications = repository.committed_publications(self.tenant)
            snapshot = self.database._snapshot(connection)
            projection = _PlanningProjectionRebuilder(cast(_PlanningReadStore, repository)).rebuild(
                self.tenant, verified_tenant_frontier=snapshot.head
            )
            count = sum(len(item.records) for item in publications)
            lines = [f"tenant={self.tenant.value} records={count}"]
            for publication in publications:
                view = projection.get(self.tenant, publication.result.intention_line_id)
                if view is None:
                    raise ValueError("committed planning projection is missing")
                lines.extend((view.intention_line_id.value, "purpose=" + view.purpose))
            result = tuple(lines)
            gate = PostgresDeploymentGate(
                connection, self.tenant.value, self._authenticate_entitlement
            )
            payload = canonical(result)
            if gate.require(gate_request, "cli.render", payload):
                gate.crossed(gate_request, payload)
            return result

    def import_entitlement(
        self,
        current: CurrentEntitlement,
        proof: bytes,
        *,
        previous: bytes | None = None,
    ) -> None:
        with self._transaction() as (connection, _trust):
            PostgresDeploymentGate(
                connection, self.tenant.value, self._authenticate_entitlement
            ).import_current(current, proof, previous)

    def revoke(self, expected_credential_head: str) -> None:
        with self._transaction() as (_connection, trust):
            trust.revoke(expected_credential_head)


@asynccontextmanager
async def open_postgres_planning_runtime(
    *,
    tenant_id: str,
    operator_secret: bytes,
    authenticate_entitlement: Callable[[bytes, bytes], bool] | None = None,
) -> AsyncIterator[PostgresPlanningRuntime]:
    database = PostgresServer.from_environment().user_database(tenant_id)
    database.provision()
    yield PostgresPlanningRuntime(database, tenant_id, operator_secret, authenticate_entitlement)
