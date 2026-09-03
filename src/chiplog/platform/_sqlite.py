from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

STORE_VERSION = 1
SCHEMA_SQL = """
CREATE TABLE store_metadata (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1), store_version INTEGER NOT NULL
);
CREATE TABLE tenant_heads (tenant_id TEXT PRIMARY KEY, head INTEGER NOT NULL);
CREATE TABLE publications (
    tenant_id TEXT NOT NULL, operation_kind TEXT NOT NULL, idempotency_key TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL, commit_sequence INTEGER NOT NULL, record_ids TEXT NOT NULL,
    PRIMARY KEY (tenant_id, operation_kind, idempotency_key)
);
CREATE TABLE records (
    tenant_id TEXT NOT NULL, record_id TEXT NOT NULL, owner TEXT NOT NULL,
    schema_id TEXT NOT NULL, canonical_bytes BLOB NOT NULL, commit_sequence INTEGER NOT NULL,
    PRIMARY KEY (tenant_id, record_id)
);
CREATE TABLE evidence_inbox (
    tenant_id TEXT NOT NULL, source_id TEXT NOT NULL, evidence_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL, canonical_bytes BLOB NOT NULL, followup_kind TEXT NOT NULL,
    state TEXT NOT NULL, attempt_id TEXT, transport_version TEXT, cursor TEXT,
    PRIMARY KEY (tenant_id, source_id, evidence_id)
);
CREATE TABLE deletion_fences (
    tenant_id TEXT PRIMARY KEY, generation TEXT NOT NULL, frontier INTEGER NOT NULL
);
CREATE TABLE derivatives (
    tenant_id TEXT NOT NULL, sink TEXT NOT NULL, derivative_id TEXT NOT NULL,
    source_record_ids TEXT NOT NULL, source_epoch INTEGER NOT NULL,
    provenance_fingerprint TEXT NOT NULL, PRIMARY KEY (tenant_id, sink, derivative_id)
);
"""
STORE_COLUMNS = {
    "store_metadata": ("singleton", "store_version"),
    "tenant_heads": ("tenant_id", "head"),
    "publications": (
        "tenant_id",
        "operation_kind",
        "idempotency_key",
        "request_fingerprint",
        "commit_sequence",
        "record_ids",
    ),
    "records": (
        "tenant_id",
        "record_id",
        "owner",
        "schema_id",
        "canonical_bytes",
        "commit_sequence",
    ),
    "evidence_inbox": (
        "tenant_id",
        "source_id",
        "evidence_id",
        "fingerprint",
        "canonical_bytes",
        "followup_kind",
        "state",
        "attempt_id",
        "transport_version",
        "cursor",
    ),
    "deletion_fences": ("tenant_id", "generation", "frontier"),
    "derivatives": (
        "tenant_id",
        "sink",
        "derivative_id",
        "source_record_ids",
        "source_epoch",
        "provenance_fingerprint",
    ),
}


class StoreAdmissionError(RuntimeError):
    pass


class LostCommitAcknowledgement(RuntimeError):
    """The transaction committed, but its caller did not observe the result."""


class EvidencePossibleLoss(RuntimeError):
    """A non-redeliverable source could not be durably admitted."""


@dataclass(frozen=True)
class PhysicalRecord:
    record_id: str
    owner: str
    schema_id: str
    canonical_bytes: bytes
    fingerprint: str


@dataclass(frozen=True)
class PhysicalPublicationCommand:
    tenant_id: str
    operation_kind: str
    idempotency_key: str
    request_fingerprint: str
    expected_head: int
    fence_generation: str
    expected_fence_frontier: int
    minimum_fence_frontier: int
    records: tuple[PhysicalRecord, ...]
    fault: Literal["none", "before_commit", "after_commit"] = "none"
    admission_guard: Callable[[], Literal["DENIED", "STALE", "INDETERMINATE"] | None] | None = None


@dataclass(frozen=True)
class PublicationResult:
    disposition: Literal["COMMITTED", "REPLAY", "CONFLICT", "STALE", "DENIED", "INDETERMINATE"]
    commit_sequence: int | None
    record_ids: tuple[str, ...]


EvidenceFollowupKind = Literal["PUSH", "POLL", "RECONCILIATION"]


@dataclass(frozen=True)
class EvidenceIngressCommand:
    tenant_id: str
    source_id: str
    evidence_id: str
    fingerprint: str
    canonical_bytes: bytes
    followup_kind: EvidenceFollowupKind
    redelivery_supported: bool
    fault: Literal["none", "before_commit", "after_commit"] = "none"


@dataclass(frozen=True)
class EvidenceFollowupCommand:
    tenant_id: str
    source_id: str
    evidence_id: str
    expected_state: str
    next_state: str
    attempt_id: str
    transport_version: str
    cursor: str | None = None


@dataclass(frozen=True)
class EvidenceResult:
    disposition: Literal["COMMITTED", "REPLAY", "CONFLICT"]
    evidence_id: str
    state: str


@dataclass(frozen=True)
class DerivativeRegistration:
    sink: str
    derivative_id: str
    source_record_ids: tuple[str, ...]
    source_epoch: int
    provenance_fingerprint: str


@dataclass(frozen=True)
class FenceAdvanceCommand:
    tenant_id: str
    generation: str
    frontier: int
    allow_exact_replay: bool = False


@dataclass(frozen=True)
class DerivativeRegistrationCommand:
    tenant_id: str
    registration: DerivativeRegistration
    fence_generation: str


@dataclass(frozen=True)
class PlatformMutationResult:
    disposition: Literal["COMMITTED", "REPLAY"]
    subject: str


class _WriterToken:
    pass


def _schema_manifest(connection: sqlite3.Connection) -> tuple[tuple[object, ...], ...]:
    rows = connection.execute(
        """SELECT type, name, tbl_name, sql FROM sqlite_master
           WHERE type IN ('table', 'index') AND name NOT LIKE 'sqlite_%'
           ORDER BY type, name"""
    ).fetchall()
    return tuple(tuple(row) for row in rows)


def _expected_schema_manifest() -> tuple[tuple[object, ...], ...]:
    with contextlib.closing(sqlite3.connect(":memory:")) as reference:
        reference.executescript(SCHEMA_SQL)
        return _schema_manifest(reference)


class SQLiteMaterializer:
    """Internal physical transaction boundary; not an application-facing port."""

    def __init__(
        self,
        path: Path,
        *,
        record_contracts: dict[str, str],
        managed_record_owners: tuple[str, ...] | None = None,
        derivative_contracts: tuple[str, ...] = (),
        managed_derivative_sinks: tuple[str, ...] = (),
        store_version: int = STORE_VERSION,
    ) -> None:
        self._path = path
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._writer_token: _WriterToken | None = None
        self._record_contracts = dict(record_contracts)
        if managed_record_owners is None:
            managed_record_owners = tuple(record_contracts)
        if len(managed_record_owners) != len(set(managed_record_owners)) or set(
            managed_record_owners
        ) != set(record_contracts):
            self._connection.close()
            raise StoreAdmissionError("record owner registry exact-set mismatch")
        if len(derivative_contracts) != len(set(derivative_contracts)) or set(
            derivative_contracts
        ) != set(managed_derivative_sinks):
            self._connection.close()
            raise StoreAdmissionError("derivative registry exact-set mismatch")
        self._derivative_contracts = frozenset(derivative_contracts)
        try:
            self._admit(store_version)
        except BaseException:
            self._connection.close()
            raise
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")

    def __enter__(self) -> SQLiteMaterializer:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _admit(self, store_version: int) -> None:
        tables = {
            row[0]
            for row in self._connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if not tables:
            with self._connection:
                self._connection.executescript(SCHEMA_SQL)
                self._connection.execute(
                    "INSERT INTO store_metadata(singleton, store_version) VALUES (1, ?)",
                    (store_version,),
                )
            return
        if "store_metadata" not in tables:
            raise StoreAdmissionError("non-empty database has no store version")
        if tables != set(STORE_COLUMNS):
            raise StoreAdmissionError("physical store table set mismatch")
        for table, expected_columns in STORE_COLUMNS.items():
            observed_columns = tuple(
                str(column[1]) for column in self._connection.execute(f"PRAGMA table_info({table})")
            )
            if observed_columns != expected_columns:
                raise StoreAdmissionError("physical store column set mismatch")
        if _schema_manifest(self._connection) != _expected_schema_manifest():
            raise StoreAdmissionError("physical store canonical schema mismatch")
        row = self._connection.execute(
            "SELECT store_version FROM store_metadata WHERE singleton = 1"
        ).fetchone()
        if row is None or row[0] != store_version:
            raise StoreAdmissionError("store version mismatch")
        for owner, schema_id in self._connection.execute(
            "SELECT DISTINCT owner, schema_id FROM records"
        ):
            if self._record_contracts.get(str(owner)) != str(schema_id):
                raise StoreAdmissionError("persisted record owner/schema has no decoder")

    def _publish(
        self, token: _WriterToken, command: PhysicalPublicationCommand
    ) -> PublicationResult:
        self._require_writer(token)
        record_ids = tuple(record.record_id for record in command.records)
        if not record_ids or len(record_ids) != len(set(record_ids)):
            raise ValueError("publication records must be a non-empty unique set")
        for record in command.records:
            expected_schema = self._record_contracts.get(record.owner)
            if expected_schema is None or expected_schema != record.schema_id:
                raise ValueError("owner/schema contract mismatch")
            observed_fingerprint = hashlib.sha256(record.canonical_bytes).hexdigest()
            if record.fingerprint != observed_fingerprint:
                raise ValueError("canonical record fingerprint mismatch")
        self.require_fence(
            command.tenant_id,
            command.fence_generation,
            command.minimum_fence_frontier,
            exact_frontier=command.expected_fence_frontier,
        )

        try:
            self._connection.execute("BEGIN IMMEDIATE")
            existing = self._connection.execute(
                """SELECT request_fingerprint, commit_sequence, record_ids
                   FROM publications
                   WHERE tenant_id = ? AND operation_kind = ? AND idempotency_key = ?""",
                (command.tenant_id, command.operation_kind, command.idempotency_key),
            ).fetchone()
            if existing is not None:
                self._connection.rollback()
                if existing[0] != command.request_fingerprint:
                    return PublicationResult("CONFLICT", existing[1], ())
                return PublicationResult("REPLAY", existing[1], tuple(existing[2].split("\n")))
            if command.admission_guard is not None:
                disposition = command.admission_guard()
                if disposition is not None:
                    self._connection.rollback()
                    return PublicationResult(disposition, None, ())
            row = self._connection.execute(
                "SELECT head FROM tenant_heads WHERE tenant_id = ?", (command.tenant_id,)
            ).fetchone()
            current_head = 0 if row is None else int(row[0])
            if current_head != command.expected_head:
                self._connection.rollback()
                return PublicationResult("STALE", None, ())
            commit_sequence = current_head + 1
            for record in command.records:
                self._connection.execute(
                    """INSERT INTO records(
                           tenant_id, record_id, owner, schema_id, canonical_bytes, commit_sequence
                       ) VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        command.tenant_id,
                        record.record_id,
                        record.owner,
                        record.schema_id,
                        record.canonical_bytes,
                        commit_sequence,
                    ),
                )
            self._connection.execute(
                """INSERT INTO publications(
                       tenant_id, operation_kind, idempotency_key, request_fingerprint,
                       commit_sequence, record_ids
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    command.tenant_id,
                    command.operation_kind,
                    command.idempotency_key,
                    command.request_fingerprint,
                    commit_sequence,
                    "\n".join(record_ids),
                ),
            )
            self._connection.execute(
                """INSERT INTO tenant_heads(tenant_id, head) VALUES (?, ?)
                   ON CONFLICT(tenant_id) DO UPDATE SET head = excluded.head""",
                (command.tenant_id, commit_sequence),
            )
            if command.fault == "before_commit":
                raise RuntimeError("injected fault before commit")
            self._connection.commit()
        except BaseException:
            if self._connection.in_transaction:
                self._connection.rollback()
            raise
        if command.fault == "after_commit":
            raise LostCommitAcknowledgement("injected lost commit acknowledgement")
        return PublicationResult("COMMITTED", commit_sequence, record_ids)

    def durable_records(self) -> tuple[tuple[object, ...], ...]:
        rows = self._connection.execute(
            """SELECT tenant_id, record_id, owner, schema_id, canonical_bytes, commit_sequence
               FROM records ORDER BY tenant_id, commit_sequence, record_id"""
        ).fetchall()
        return tuple(tuple(row) for row in rows)

    def _attach_writer(self) -> _WriterToken:
        if self._writer_token is not None:
            raise RuntimeError("SQLite materializer already has an EventAppender owner")
        self._writer_token = _WriterToken()
        return self._writer_token

    def _require_writer(self, token: _WriterToken) -> None:
        if token is not self._writer_token:
            raise RuntimeError("SQLite mutation requires EventAppender ownership")

    def _install_fence(
        self,
        token: _WriterToken,
        tenant_id: str,
        generation: str,
        frontier: int,
        *,
        allow_exact_replay: bool = False,
    ) -> PlatformMutationResult:
        self._require_writer(token)
        if frontier < 0 or not generation:
            raise ValueError("invalid deletion fence")
        with self._connection:
            current = self._connection.execute(
                "SELECT generation, frontier FROM deletion_fences WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchone()
            if current == (generation, frontier) and allow_exact_replay:
                return PlatformMutationResult("REPLAY", f"fence:{tenant_id}:{generation}")
            if current is not None and (frontier < current[1] or generation == current[0]):
                raise ValueError("deletion fence must advance generation and not regress")
            self._connection.execute(
                """INSERT INTO deletion_fences(tenant_id, generation, frontier) VALUES (?, ?, ?)
                   ON CONFLICT(tenant_id) DO UPDATE SET
                       generation = excluded.generation, frontier = excluded.frontier""",
                (tenant_id, generation, frontier),
            )
        return PlatformMutationResult("COMMITTED", f"fence:{tenant_id}:{generation}")

    def require_fence(
        self,
        tenant_id: str,
        generation: str,
        minimum_frontier: int,
        *,
        exact_frontier: int | None = None,
    ) -> None:
        row = self._connection.execute(
            "SELECT generation, frontier FROM deletion_fences WHERE tenant_id = ?", (tenant_id,)
        ).fetchone()
        if row is None:
            raise ValueError("deletion fence absent")
        if row[0] != generation:
            raise ValueError("deletion fence generation mismatch")
        if int(row[1]) < minimum_frontier:
            raise ValueError("deletion fence lags required frontier")
        if exact_frontier is not None and int(row[1]) != exact_frontier:
            raise ValueError("deletion fence frontier mismatch")

    def guarded_records(
        self, tenant_id: str, generation: str, minimum_frontier: int
    ) -> tuple[tuple[object, ...], ...]:
        with contextlib.closing(sqlite3.connect(self._path)) as reader:
            reader.execute("BEGIN")
            row = reader.execute(
                "SELECT generation, frontier FROM deletion_fences WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchone()
            if row is None:
                raise ValueError("deletion fence absent")
            if row[0] != generation:
                raise ValueError("deletion fence generation mismatch")
            if int(row[1]) < minimum_frontier:
                raise ValueError("deletion fence lags required frontier")
            rows = reader.execute(
                """SELECT tenant_id, record_id, owner, schema_id, canonical_bytes,
                          commit_sequence
                   FROM records WHERE tenant_id = ? ORDER BY commit_sequence, record_id""",
                (tenant_id,),
            ).fetchall()
            reader.commit()
            return tuple(tuple(item) for item in rows)

    def _register_derivative(
        self,
        token: _WriterToken,
        tenant_id: str,
        registration: DerivativeRegistration,
        *,
        fence_generation: str,
    ) -> PlatformMutationResult:
        self._require_writer(token)
        if registration.sink not in self._derivative_contracts:
            raise ValueError("unknown derivative sink")
        if not registration.source_record_ids or len(registration.source_record_ids) != len(
            set(registration.source_record_ids)
        ):
            raise ValueError("derivative provenance must be a non-empty unique source set")
        self.require_fence(
            tenant_id,
            fence_generation,
            registration.source_epoch,
            exact_frontier=registration.source_epoch,
        )
        placeholders = ",".join("?" for _ in registration.source_record_ids)
        rows = self._connection.execute(
            f"""SELECT record_id FROM records
                WHERE tenant_id = ? AND record_id IN ({placeholders})""",
            (tenant_id, *registration.source_record_ids),
        ).fetchall()
        if {str(row[0]) for row in rows} != set(registration.source_record_ids):
            raise ValueError("derivative provenance source set is missing or foreign")
        canonical = json.dumps(
            {
                "tenant_id": tenant_id,
                "sink": registration.sink,
                "derivative_id": registration.derivative_id,
                "source_record_ids": registration.source_record_ids,
                "source_epoch": registration.source_epoch,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        if hashlib.sha256(canonical).hexdigest() != registration.provenance_fingerprint:
            raise ValueError("derivative provenance fingerprint mismatch")
        with self._connection:
            self._connection.execute(
                """INSERT INTO derivatives(
                       tenant_id, sink, derivative_id, source_record_ids, source_epoch,
                       provenance_fingerprint
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    tenant_id,
                    registration.sink,
                    registration.derivative_id,
                    "\n".join(registration.source_record_ids),
                    registration.source_epoch,
                    registration.provenance_fingerprint,
                ),
            )
        return PlatformMutationResult("COMMITTED", registration.derivative_id)

    def _admit_evidence(
        self, token: _WriterToken, command: EvidenceIngressCommand
    ) -> EvidenceResult:
        self._require_writer(token)
        existing = self._connection.execute(
            """SELECT fingerprint, state FROM evidence_inbox
               WHERE tenant_id = ? AND source_id = ? AND evidence_id = ?""",
            (command.tenant_id, command.source_id, command.evidence_id),
        ).fetchone()
        if existing is not None:
            if existing[0] != command.fingerprint:
                return EvidenceResult("CONFLICT", command.evidence_id, str(existing[1]))
            return EvidenceResult("REPLAY", command.evidence_id, str(existing[1]))
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            self._connection.execute(
                """INSERT INTO evidence_inbox(
                       tenant_id, source_id, evidence_id, fingerprint, canonical_bytes,
                       followup_kind, state
                   ) VALUES (?, ?, ?, ?, ?, ?, 'LOCAL_ACK_AUTHORIZED')""",
                (
                    command.tenant_id,
                    command.source_id,
                    command.evidence_id,
                    command.fingerprint,
                    command.canonical_bytes,
                    command.followup_kind,
                ),
            )
            if command.fault == "before_commit":
                raise RuntimeError("injected evidence fault before commit")
            self._connection.commit()
        except BaseException as error:
            if self._connection.in_transaction:
                self._connection.rollback()
            if not command.redelivery_supported:
                raise EvidencePossibleLoss("non-redeliverable evidence may be lost") from error
            raise
        if command.fault == "after_commit":
            raise LostCommitAcknowledgement("injected lost evidence commit acknowledgement")
        return EvidenceResult("COMMITTED", command.evidence_id, "LOCAL_ACK_AUTHORIZED")

    def _advance_evidence(
        self, token: _WriterToken, command: EvidenceFollowupCommand
    ) -> EvidenceResult:
        self._require_writer(token)
        row = self._connection.execute(
            """SELECT source_id, followup_kind, state, attempt_id, transport_version, cursor
               FROM evidence_inbox
               WHERE tenant_id = ? AND source_id = ? AND evidence_id = ?""",
            (command.tenant_id, command.source_id, command.evidence_id),
        ).fetchone()
        if row is None:
            raise ValueError("unknown evidence inbox subject")
        source_id, followup_kind, state, attempt_id, transport_version, cursor = row
        if state == command.next_state:
            if (attempt_id, transport_version, cursor) == (
                command.attempt_id,
                command.transport_version,
                command.cursor,
            ):
                return EvidenceResult("REPLAY", command.evidence_id, command.next_state)
            return EvidenceResult("CONFLICT", command.evidence_id, str(state))
        allowed = {
            "PUSH": {
                ("LOCAL_ACK_AUTHORIZED", "PUSH_RESPONSE_ATTEMPT_ISSUED"),
                ("PUSH_RESPONSE_ATTEMPT_ISSUED", "PUSH_RESPONSE_LOCAL_COMPLETION_OBSERVED"),
                ("PUSH_RESPONSE_LOCAL_COMPLETION_OBSERVED", "PROVIDER_RECEIPT_OBSERVED"),
            },
            "POLL": {
                ("LOCAL_ACK_AUTHORIZED", "POLL_CURSOR_ADVANCE_AUTHORIZED"),
                ("POLL_CURSOR_ADVANCE_AUTHORIZED", "POLL_CURSOR_APPLIED"),
            },
            "RECONCILIATION": {
                ("LOCAL_ACK_AUTHORIZED", "RECONCILIATION_RELEASE_AUTHORIZED"),
                ("RECONCILIATION_RELEASE_AUTHORIZED", "RECONCILIATION_OBLIGATION_RELEASED"),
            },
        }
        transition = (command.expected_state, command.next_state)
        if state != command.expected_state or transition not in allowed[str(followup_kind)]:
            raise ValueError("illegal evidence follow-up transition")
        if followup_kind == "POLL" and command.cursor is None:
            raise ValueError("polling transition requires a durable cursor")
        with self._connection:
            changed = self._connection.execute(
                """UPDATE evidence_inbox
                   SET state = ?, attempt_id = ?, transport_version = ?, cursor = ?
                   WHERE tenant_id = ? AND source_id = ? AND evidence_id = ? AND state = ?""",
                (
                    command.next_state,
                    command.attempt_id,
                    command.transport_version,
                    command.cursor,
                    command.tenant_id,
                    source_id,
                    command.evidence_id,
                    command.expected_state,
                ),
            ).rowcount
            if changed != 1:
                raise RuntimeError("evidence state CAS lost")
        return EvidenceResult("COMMITTED", command.evidence_id, command.next_state)

    def evidence_state(self, tenant_id: str, source_id: str, evidence_id: str) -> str | None:
        row = self._connection.execute(
            """SELECT state FROM evidence_inbox
               WHERE tenant_id = ? AND source_id = ? AND evidence_id = ?""",
            (tenant_id, source_id, evidence_id),
        ).fetchone()
        return None if row is None else str(row[0])

    def close(self) -> None:
        self._connection.close()


@dataclass
class _AcceptedCommand:
    command: (
        PhysicalPublicationCommand
        | EvidenceIngressCommand
        | EvidenceFollowupCommand
        | FenceAdvanceCommand
        | DerivativeRegistrationCommand
    )
    result: asyncio.Future[PublicationResult | EvidenceResult | PlatformMutationResult]


class EventAppender:
    """Broker-owned bounded single writer with close-before-drain semantics."""

    def __init__(
        self, materializer: SQLiteMaterializer, *, capacity: int, evidence_capacity: int = 1
    ) -> None:
        if capacity < 1 or evidence_capacity < 1:
            raise ValueError("capacities must be positive")
        self._materializer = materializer
        self._writer_token = materializer._attach_writer()
        self._ordinary: asyncio.Queue[_AcceptedCommand] = asyncio.Queue(capacity)
        self._evidence: asyncio.Queue[_AcceptedCommand] = asyncio.Queue(evidence_capacity)
        self._ordinary_capacity = capacity
        self._evidence_capacity = evidence_capacity
        self._ordinary_admitted = 0
        self._evidence_admitted = 0
        self._available = asyncio.Semaphore(0)
        self._admission_lock = asyncio.Lock()
        self._accepting = True
        self._worker = asyncio.create_task(self._run())
        self._close_task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> EventAppender:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def submit(self, command: PhysicalPublicationCommand) -> PublicationResult:
        loop = asyncio.get_running_loop()
        result: asyncio.Future[PublicationResult | EvidenceResult | PlatformMutationResult] = (
            loop.create_future()
        )
        async with self._admission_lock:
            if not self._accepting:
                raise RuntimeError("event appender admission is closed")
            if self._ordinary_admitted >= self._ordinary_capacity:
                raise asyncio.QueueFull("ordinary writer lane is at capacity")
            self._ordinary_admitted += 1
            self._ordinary.put_nowait(_AcceptedCommand(command, result))
            self._available.release()
        value = await asyncio.shield(result)
        assert isinstance(value, PublicationResult)
        return value

    async def submit_evidence(
        self, command: EvidenceIngressCommand | EvidenceFollowupCommand
    ) -> EvidenceResult:
        loop = asyncio.get_running_loop()
        result: asyncio.Future[PublicationResult | EvidenceResult | PlatformMutationResult] = (
            loop.create_future()
        )
        async with self._admission_lock:
            if not self._accepting:
                raise RuntimeError("event appender admission is closed")
            if self._evidence_admitted >= self._evidence_capacity:
                raise asyncio.QueueFull("evidence writer lane is at capacity")
            self._evidence_admitted += 1
            self._evidence.put_nowait(_AcceptedCommand(command, result))
            self._available.release()
        value = await asyncio.shield(result)
        assert isinstance(value, EvidenceResult)
        return value

    async def advance_fence(self, command: FenceAdvanceCommand) -> PlatformMutationResult:
        return await self._submit_platform_mutation(command)

    async def register_derivative(
        self, command: DerivativeRegistrationCommand
    ) -> PlatformMutationResult:
        return await self._submit_platform_mutation(command)

    async def _submit_platform_mutation(
        self, command: FenceAdvanceCommand | DerivativeRegistrationCommand
    ) -> PlatformMutationResult:
        loop = asyncio.get_running_loop()
        result: asyncio.Future[PublicationResult | EvidenceResult | PlatformMutationResult] = (
            loop.create_future()
        )
        async with self._admission_lock:
            if not self._accepting:
                raise RuntimeError("event appender admission is closed")
            if self._ordinary_admitted >= self._ordinary_capacity:
                raise asyncio.QueueFull("ordinary writer lane is at capacity")
            self._ordinary_admitted += 1
            self._ordinary.put_nowait(_AcceptedCommand(command, result))
            self._available.release()
        value = await asyncio.shield(result)
        assert isinstance(value, PlatformMutationResult)
        return value

    async def _run(self) -> None:
        while True:
            await self._available.acquire()
            queue: asyncio.Queue[_AcceptedCommand]
            try:
                accepted = self._evidence.get_nowait()
                queue = self._evidence
            except asyncio.QueueEmpty:
                accepted = self._ordinary.get_nowait()
                queue = self._ordinary
            try:
                try:
                    value: PublicationResult | EvidenceResult | PlatformMutationResult
                    if isinstance(accepted.command, PhysicalPublicationCommand):
                        value = await asyncio.to_thread(
                            self._materializer._publish,
                            self._writer_token,
                            accepted.command,
                        )
                    elif isinstance(accepted.command, EvidenceIngressCommand):
                        value = await asyncio.to_thread(
                            self._materializer._admit_evidence,
                            self._writer_token,
                            accepted.command,
                        )
                    elif isinstance(accepted.command, EvidenceFollowupCommand):
                        value = await asyncio.to_thread(
                            self._materializer._advance_evidence,
                            self._writer_token,
                            accepted.command,
                        )
                    elif isinstance(accepted.command, FenceAdvanceCommand):
                        value = await asyncio.to_thread(
                            self._materializer._install_fence,
                            self._writer_token,
                            accepted.command.tenant_id,
                            accepted.command.generation,
                            accepted.command.frontier,
                            allow_exact_replay=accepted.command.allow_exact_replay,
                        )
                    else:
                        value = await asyncio.to_thread(
                            self._materializer._register_derivative,
                            self._writer_token,
                            accepted.command.tenant_id,
                            accepted.command.registration,
                            fence_generation=accepted.command.fence_generation,
                        )
                except BaseException as error:
                    if not accepted.result.done():
                        accepted.result.set_exception(error)
                else:
                    if not accepted.result.done():
                        accepted.result.set_result(value)
            finally:
                if queue is self._evidence:
                    self._evidence_admitted -= 1
                else:
                    self._ordinary_admitted -= 1
                queue.task_done()

    async def close(self) -> None:
        async with self._admission_lock:
            if self._close_task is None:
                self._accepting = False
                self._close_task = asyncio.create_task(self._drain_and_close())
            close_task = self._close_task
        await asyncio.shield(close_task)

    async def _drain_and_close(self) -> None:
        await self._evidence.join()
        await self._ordinary.join()
        self._worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._worker
        await asyncio.to_thread(self._materializer.close)
