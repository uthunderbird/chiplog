"""Broker-owned durable ordering of entitlement invalidation and exact handoff.

The pinned authenticator is supplied by the independent entitlement authority.
Without it, no imported view is accepted. This module never issues entitlement.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager, nullcontext
from pathlib import Path
from typing import Literal, Protocol

from .authority_gate import AuthorityGate, AuthorityGateError, FileIdentity, checked_file_identity
from .deployment_gate import (
    CurrentEntitlement,
    DeploymentGateRequest,
    DeploymentGateResult,
)


def canonical_request(request: DeploymentGateRequest) -> bytes:
    return json.dumps(
        request.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    ).encode()


def canonical_entitlement(value: CurrentEntitlement) -> bytes:
    return json.dumps(value.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()


def _digest(request: DeploymentGateRequest) -> str:
    return hashlib.sha256(canonical_request(request)).hexdigest()


class GateIntegrityError(RuntimeError):
    pass


class GateDecisionIndeterminate(RuntimeError):
    """An irreversible decision may exist; no definite denial or blind retry."""


class GateDecisionJournal(Protocol):
    def entries(self) -> tuple[tuple[str, str | None, bytes], ...]: ...

    def append(self, decision: bytes, predecessor: str | None) -> str: ...


class BrokerDeploymentGate:
    """Independent journal CAS orders current heads, cap use and handoff decisions.

    Authenticated updates require exact predecessor bytes. A durable handoff is the
    last reversible decision; later invalidation cannot claim to recall it. The
    owning adapter must execute only the exact durable payload of that decision.
    """

    def __init__(
        self,
        database: Path,
        *,
        tenant_id: str,
        surfaces: tuple[tuple[str, str], ...],
        authenticate: Callable[[bytes, bytes], bool] | None = None,
        journal: GateDecisionJournal | None = None,
        clock: Callable[[], int],
        authority_gate: AuthorityGate | None = None,
    ) -> None:
        if len({surface for surface, _ in surfaces}) != len(surfaces):
            raise ValueError("duplicate deployment surface")
        if getattr(journal, "authority_gate", None) != authority_gate:
            raise GateIntegrityError("deployment gate and journal authority binding mismatch")
        if authority_gate is not None and journal is None:
            raise GateIntegrityError("bound deployment gate requires an independent journal")
        if authority_gate is not None and database.is_symlink():
            raise GateIntegrityError("bound deployment database cannot be a symbolic link")
        self._authority_gate = authority_gate
        self._identity: FileIdentity | None = None
        self._database = database.resolve(strict=False) if authority_gate is not None else database
        self._tenant_id = tenant_id
        self._surfaces = dict(surfaces)
        self._authenticate = authenticate
        self._journal = journal
        self._journal_head: str | None = None
        self._initialized = False
        self._clock = clock
        with self._authority_scope():
            with self._transaction() as connection:
                connection.executescript(
                    "CREATE TABLE IF NOT EXISTS gate_current ("
                    "tenant TEXT PRIMARY KEY, payload BLOB NOT NULL, proof BLOB NOT NULL);"
                    "CREATE TABLE IF NOT EXISTS gate_handoffs ("
                    "tenant TEXT NOT NULL, operation TEXT NOT NULL, request BLOB NOT NULL,"
                    "payload BLOB NOT NULL, entitlement TEXT NOT NULL, mode TEXT NOT NULL,"
                    "execution BLOB NOT NULL, disposition TEXT NOT NULL,"
                    "sequence INTEGER PRIMARY KEY AUTOINCREMENT, UNIQUE(tenant,operation));"
                )
            self._initialized = True
            with self._transaction():
                pass

    @property
    def authority_gate(self) -> AuthorityGate | None:
        return self._authority_gate

    def _check_identity(self) -> None:
        if self._authority_gate is not None:
            self._identity = checked_file_identity(self._database, self._identity)

    @contextmanager
    def _authority_scope(self) -> Iterator[None]:
        try:
            with self._authority_gate.hold() if self._authority_gate is not None else nullcontext():
                if self._identity is not None or self._database.exists():
                    self._check_identity()
                yield
                self._check_identity()
        except AuthorityGateError as error:
            raise GateIntegrityError(
                "deployment authority source unavailable or replaced"
            ) from error

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with (
            self._authority_scope(),
            closing(sqlite3.connect(self._database, isolation_level=None)) as connection,
            connection,
        ):
            self._check_identity()
            connection.execute("BEGIN IMMEDIATE")
            if self._initialized:
                self._synchronize(connection)
            yield connection
            self._check_identity()

    def _synchronize(self, connection: sqlite3.Connection) -> None:
        if self._journal is None:
            if connection.execute("SELECT COUNT(*) FROM gate_current").fetchone()[0]:
                raise GateIntegrityError("gate authority journal is missing")
            return
        try:
            entries = self._journal.entries()
        except (OSError, RuntimeError, ValueError) as error:
            raise GateIntegrityError("gate decision history is unverifiable") from error
        self._journal_head = entries[-1][0] if entries else None
        if not entries and (
            connection.execute("SELECT COUNT(*) FROM gate_current").fetchone()[0]
            or connection.execute("SELECT COUNT(*) FROM gate_handoffs").fetchone()[0]
        ):
            raise GateIntegrityError("populated gate cache has no independent decision history")
        # SQLite is a disposable materialization of the separately protected
        # authenticated journal, including current heads and consumed capacity.
        connection.execute("DELETE FROM gate_current")
        connection.execute("DELETE FROM gate_handoffs")
        for _, _, payload in entries:
            event = json.loads(payload)
            if event["tenant"] != self._tenant_id:
                raise GateIntegrityError("foreign tenant in gate decision journal")
            if event["kind"] == "CURRENT":
                connection.execute(
                    "INSERT INTO gate_current VALUES (?,?,?) ON CONFLICT(tenant) DO UPDATE "
                    "SET payload=excluded.payload,proof=excluded.proof",
                    (
                        self._tenant_id,
                        bytes.fromhex(event["payload"]),
                        bytes.fromhex(event["proof"]),
                    ),
                )
            elif event["kind"] == "HANDOFF":
                request = DeploymentGateRequest.model_validate_json(bytes.fromhex(event["request"]))
                connection.execute(
                    "INSERT INTO gate_handoffs"
                    "(tenant,operation,request,payload,entitlement,mode,execution,disposition) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (
                        self._tenant_id,
                        request.operation_id,
                        bytes.fromhex(event["request"]),
                        bytes.fromhex(event["payload"]),
                        request.entitlement_head,
                        request.mode,
                        bytes.fromhex(event["execution"]),
                        "PENDING",
                    ),
                )
            elif event["kind"] == "MATERIALIZED":
                changed = connection.execute(
                    "UPDATE gate_handoffs SET disposition='MATERIALIZED' "
                    "WHERE tenant=? AND operation=? AND disposition='PENDING'",
                    (self._tenant_id, event["operation"]),
                )
                if changed.rowcount != 1:
                    raise GateIntegrityError("orphan or duplicate materialization decision")
            else:
                raise GateIntegrityError("unknown gate decision kind")

    def _decide(self, event: dict[str, str]) -> None:
        if self._journal is None:
            raise GateIntegrityError("independent gate decision journal is unavailable")
        payload = json.dumps(
            {**event, "tenant": self._tenant_id}, sort_keys=True, separators=(",", ":")
        ).encode()
        predecessor = self._journal_head
        try:
            self._journal_head = self._journal.append(payload, predecessor)
        except (OSError, RuntimeError, ValueError) as error:
            try:
                entries = self._journal.entries()
            except (OSError, RuntimeError, ValueError) as observation_error:
                raise GateDecisionIndeterminate(
                    "gate decision requires journal reconciliation"
                ) from observation_error
            exact = [
                head
                for head, prior, decided in entries
                if prior == predecessor and decided == payload
            ]
            if len(exact) == 1:
                self._journal_head = exact[0]
                return
            raise GateIntegrityError(
                "authenticated journal proves no exact gate decision"
            ) from error

    def observe(self) -> CurrentEntitlement | None:
        with self._transaction() as connection:
            return self._current(connection)

    def _current(self, connection: sqlite3.Connection) -> CurrentEntitlement | None:
        row = connection.execute(
            "SELECT payload,proof FROM gate_current WHERE tenant=?", (self._tenant_id,)
        ).fetchone()
        if row is None:
            return None
        if self._authenticate is None or not self._authenticate(bytes(row[0]), bytes(row[1])):
            raise GateIntegrityError("deployment gate current entitlement is unverifiable")
        value = CurrentEntitlement.model_validate_json(bytes(row[0]))
        if canonical_entitlement(value) != bytes(row[0]):
            raise GateIntegrityError("deployment gate current entitlement is noncanonical")
        return value

    def import_current(
        self, value: CurrentEntitlement, proof: bytes, *, expected: CurrentEntitlement | None
    ) -> bool:
        payload = canonical_entitlement(value)
        if (
            self._authenticate is None
            or self._journal is None
            or not self._authenticate(payload, proof)
            or value.generation.tenant_id != self._tenant_id
        ):
            return False
        with self._transaction() as connection:
            current = self._current(connection)
            if current != expected:
                return False
            if current is not None and (
                value.generation.epoch != current.generation.epoch
                or value.generation.sequence != current.generation.sequence + 1
            ):
                return False
            if current is None and value.generation.sequence != 0:
                return False
            self._decide({"kind": "CURRENT", "payload": payload.hex(), "proof": proof.hex()})
            connection.execute(
                "INSERT INTO gate_current VALUES (?,?,?) ON CONFLICT(tenant) DO UPDATE "
                "SET payload=excluded.payload,proof=excluded.proof",
                (self._tenant_id, payload, proof),
            )
            return True

    def _evaluate(
        self, connection: sqlite3.Connection, request: DeploymentGateRequest
    ) -> DeploymentGateResult:
        def hold(reason: str) -> DeploymentGateResult:
            return DeploymentGateResult(
                disposition="HOLD", request_digest=_digest(request), reason=reason
            )

        current = self._current(connection)
        if current is None:
            return hold("independent readiness and entitlement are absent")
        if self._surfaces.get(request.surface_id) != request.bounds.capability_id:
            return hold("surface is absent or has another capability")
        if (
            request.generation.tenant_id != self._tenant_id
            or request.generation != current.generation
        ):
            return hold("generation or tenant changed")
        if (
            request.mode != current.mode
            or request.readiness_head != current.readiness_head
            or request.entitlement_head != current.entitlement_head
            or request.evidence_cursors != current.evidence_cursors
            or request.freshness_leases != current.freshness_leases
            or request.bounds != current.bounds
        ):
            return hold("complete eligibility binding changed")
        now = self._clock()
        if type(now) is not int or now < 0:
            return hold("trusted clock is unavailable")
        bounds = current.bounds
        if not bounds.starts_ns <= now < bounds.expires_ns:
            return hold("entitlement is outside its duration")
        if current.readiness != "READY" or current.status != "ACTIVE" or current.open_causes:
            return hold("readiness or entitlement is held")
        if not current.live_predicates or not all(value for _, value in current.live_predicates):
            return hold("live eligibility predicates are missing or false")
        for rows in (current.live_predicates, current.evidence_cursors, current.freshness_leases):
            keys = [key for key, _ in rows]
            if not keys or keys != sorted(set(keys)):
                return hold("eligibility input keys are missing, duplicate or unordered")
        if any(expiry <= now for _, expiry in current.freshness_leases):
            return hold("freshness lease expired")
        for values in (bounds.instrumentation, bounds.stop_rules):
            if not values or list(values) != sorted(set(values)):
                return hold("instrumentation or stop rules are not closed")
        if any(
            not value
            for value in (
                request.operation_id,
                request.payload_digest,
                request.readiness_head,
                request.entitlement_head,
                bounds.capability_id,
                bounds.cohort_id,
                bounds.purpose,
            )
        ):
            return hold("required identity is missing")
        prior = connection.execute(
            "SELECT request FROM gate_handoffs WHERE tenant=? AND operation=?",
            (self._tenant_id, request.operation_id),
        ).fetchone()
        if prior is not None:
            return hold("operation already crossed its boundary; inspect recorded disposition")
        used = connection.execute(
            "SELECT COUNT(*) FROM gate_handoffs WHERE tenant=? AND entitlement=? AND mode=?",
            (self._tenant_id, request.entitlement_head, request.mode),
        ).fetchone()[0]
        if used >= bounds.cap:
            return hold("entitlement cap exhausted")
        disposition: Literal["PERMIT_EXACT_EVALUATION", "PERMIT_EXACT_PRODUCTION"] = (
            "PERMIT_EXACT_EVALUATION" if request.mode == "EVALUATION" else "PERMIT_EXACT_PRODUCTION"
        )
        return DeploymentGateResult(
            disposition=disposition,
            request_digest=_digest(request),
            reason="exact current entitlement",
        )

    def check(self, request: DeploymentGateRequest) -> DeploymentGateResult:
        try:
            with self._transaction() as connection:
                return self._evaluate(connection, request)
        except ValueError, OSError, sqlite3.Error, GateIntegrityError:
            return DeploymentGateResult(
                disposition="HOLD",
                request_digest=_digest(request),
                reason="eligibility authority unavailable or unverifiable",
            )

    def commit_handoff(
        self, request: DeploymentGateRequest, payload: bytes, *, execution: bytes = b""
    ) -> DeploymentGateResult:
        """Atomically revalidate and durably commit exact bytes, never call arbitrary code."""
        if hashlib.sha256(payload).hexdigest() != request.payload_digest:
            return DeploymentGateResult(
                disposition="HOLD",
                request_digest=_digest(request),
                reason="handoff payload substitution",
            )
        decision_may_exist = False
        try:
            with self._transaction() as connection:
                result = self._evaluate(connection, request)
                if result.disposition != "HOLD":
                    try:
                        self._decide(
                            {
                                "kind": "HANDOFF",
                                "request": canonical_request(request).hex(),
                                "payload": payload.hex(),
                                "execution": execution.hex(),
                            }
                        )
                    except GateDecisionIndeterminate:
                        # Preserve uncertainty before context cleanup can replace
                        # this exception with a source/lock identity failure.
                        decision_may_exist = True
                        raise
                    decision_may_exist = True
                    connection.execute(
                        "INSERT INTO gate_handoffs"
                        "(tenant,operation,request,payload,entitlement,mode,execution,disposition) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (
                            self._tenant_id,
                            request.operation_id,
                            canonical_request(request),
                            payload,
                            request.entitlement_head,
                            request.mode,
                            execution,
                            "PENDING",
                        ),
                    )
                return result
        except (ValueError, OSError, sqlite3.Error, GateIntegrityError) as error:
            if decision_may_exist:
                raise GateDecisionIndeterminate(
                    "handoff may be decided; acknowledgement unavailable"
                ) from error
            return DeploymentGateResult(
                disposition="HOLD", request_digest=_digest(request), reason="handoff not authorized"
            )

    def crossed(self, operation_id: str) -> tuple[bytes, bytes] | None:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT request,payload FROM gate_handoffs WHERE tenant=? AND operation=?",
                (self._tenant_id, operation_id),
            ).fetchone()
            return None if row is None else (bytes(row[0]), bytes(row[1]))

    def recorded_execution(self, operation_id: str) -> tuple[bytes, bool] | None:
        """Exact authenticated execution and whether its materialization is historical."""
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT execution,disposition FROM gate_handoffs WHERE tenant=? AND operation=?",
                (self._tenant_id, operation_id),
            ).fetchone()
            return None if row is None else (bytes(row[0]), row[1] == "MATERIALIZED")

    def pending(self) -> tuple[tuple[str, bytes], ...]:
        with self._transaction() as connection:
            return tuple(
                (str(row[0]), bytes(row[1]))
                for row in connection.execute(
                    "SELECT operation,execution FROM gate_handoffs "
                    "WHERE tenant=? AND disposition='PENDING' ORDER BY sequence",
                    (self._tenant_id,),
                )
            )

    def materialized(self, operation_id: str) -> None:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT disposition FROM gate_handoffs WHERE tenant=? AND operation=?",
                (self._tenant_id, operation_id),
            ).fetchone()
            if row is None:
                raise GateIntegrityError("materialization has no prior decision")
            if row[0] == "MATERIALIZED":
                return
            self._decide({"kind": "MATERIALIZED", "operation": operation_id})
            connection.execute(
                "UPDATE gate_handoffs SET disposition='MATERIALIZED' "
                "WHERE tenant=? AND operation=?",
                (self._tenant_id, operation_id),
            )


__all__ = [
    "BrokerDeploymentGate",
    "GateDecisionIndeterminate",
    "GateDecisionJournal",
    "GateIntegrityError",
    "canonical_entitlement",
    "canonical_request",
]
