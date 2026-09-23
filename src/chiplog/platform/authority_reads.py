"""Broker-private verified snapshot and two-point authority-read execution."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sqlite3
import stat
import tempfile
from contextlib import ExitStack, closing, nullcontext
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from chiplog.architecture.r7_read_registry import (
    AUTHORITY_READ_INVALIDATOR_REGISTRY,
    invalidator_registry_bytes,
    verify_invalidator_registry,
)
from chiplog.architecture.r7_storage_surface import (
    AUTHORITY_STORAGE_MEMBERS,
    AUTHORITY_STORAGE_SURFACE_DIGEST,
    PLANNING_PUBLICATION_READ_EDGES,
    R13_PLANNING_PUBLICATION_READ_EDGES,
)
from chiplog.platform.authority_gate import AuthorityGate
from chiplog.platform.read_ledger import BrokerReadLedger, BrokerReadState, ReadOperation
from chiplog.verification.r7_read_surface import verify_surface_registry_equality

AMR_FINGERPRINT = hashlib.sha256(
    json.dumps(
        [
            {"columns": item.columns, "table": item.table}
            for item in AUTHORITY_STORAGE_MEMBERS
            if item.authority_bearing
        ],
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
).hexdigest()


class AuthorityCommitmentIndeterminateError(RuntimeError):
    """The anchor was replaced but durability or identity is unconfirmed."""


class AuthorityCommitmentJournal:
    """Independent authenticated anchor for the exact current authority materialization."""

    def __init__(
        self, database: Path, secret: bytes, *, authority_gate: AuthorityGate | None = None
    ) -> None:
        database = database.resolve(strict=False)
        if authority_gate is not None and database != authority_gate.database:
            raise ValueError("authority commitment journal database differs from authority gate")
        self._path = database.with_suffix(database.suffix + ".r7-authority-commitment.json")
        self._secret = secret
        self._authority_gate = authority_gate

    @property
    def authority_gate(self) -> AuthorityGate | None:
        return self._authority_gate

    def _check_descriptor(self, descriptor: int) -> os.stat_result:
        actual = os.fstat(descriptor)
        named = self._path.lstat()
        if (
            self._path.resolve(strict=True) != self._path
            or not stat.S_ISREG(actual.st_mode)
            or not stat.S_ISREG(named.st_mode)
            or actual.st_nlink != 1
            or named.st_nlink != 1
            or (actual.st_dev, actual.st_ino) != (named.st_dev, named.st_ino)
        ):
            raise RuntimeError("authority commitment journal replaced or aliased")
        return actual

    @staticmethod
    def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate journal key")
            result[key] = value
        return result

    def load(self, tenant_id: str) -> str | None:
        with self._authority_gate.hold() if self._authority_gate is not None else nullcontext():
            return self._load(tenant_id)

    def _load(self, tenant_id: str) -> str | None:
        try:
            metadata = self._path.lstat()
        except FileNotFoundError:
            if self._path.resolve(strict=False) != self._path:
                raise RuntimeError("authority commitment journal path redirected") from None
            return None
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise RuntimeError("authority commitment journal must be regular and unaliased")
        descriptor = os.open(self._path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            before = self._check_descriptor(stream.fileno())
            if (before.st_dev, before.st_ino) != (metadata.st_dev, metadata.st_ino):
                raise RuntimeError("authority commitment journal changed before read")
            raw = stream.read()
            after = self._check_descriptor(stream.fileno())
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ):
                raise RuntimeError("authority commitment journal changed during read")
        try:
            envelope = json.loads(raw, object_pairs_hook=self._unique_object)
            if not isinstance(envelope, dict) or set(envelope) != {"payload", "mac"}:
                raise ValueError("invalid envelope")
            value = envelope["payload"]
            if (
                not isinstance(value, dict)
                or set(value) != {"tenant_id", "commitment"}
                or not isinstance(value["tenant_id"], str)
                or not isinstance(value["commitment"], str)
                or not isinstance(envelope["mac"], str)
            ):
                raise ValueError("invalid payload")
            payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
            expected = hmac.new(self._secret, payload, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(envelope["mac"], expected):
                raise ValueError("authentication failed")
            if value["tenant_id"] != tenant_id:
                raise ValueError("tenant mismatch")
            return str(value["commitment"])
        except (ValueError, TypeError, UnicodeError) as error:
            raise RuntimeError("authority commitment journal invalid or unauthenticated") from error

    def commit(self, tenant_id: str, commitment: str) -> None:
        with self._authority_gate.hold() if self._authority_gate is not None else nullcontext():
            self._commit(tenant_id, commitment)

    def _commit(self, tenant_id: str, commitment: str) -> None:
        if not isinstance(tenant_id, str) or not isinstance(commitment, str):
            raise TypeError("authority commitment journal requires string payload fields")
        self._load(tenant_id)
        payload_value = {"commitment": commitment, "tenant_id": tenant_id}
        payload = json.dumps(payload_value, sort_keys=True, separators=(",", ":")).encode()
        envelope = json.dumps(
            {
                "mac": hmac.new(self._secret, payload, hashlib.sha256).hexdigest(),
                "payload": payload_value,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        descriptor, name = tempfile.mkstemp(
            prefix=self._path.name + ".", suffix=".tmp", dir=self._path.parent
        )
        temporary = Path(name)
        replaced = False
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(envelope)
                stream.flush()
                os.fsync(stream.fileno())
                actual, named = os.fstat(stream.fileno()), temporary.lstat()
                if (
                    not stat.S_ISREG(named.st_mode)
                    or actual.st_nlink != 1
                    or (actual.st_dev, actual.st_ino) != (named.st_dev, named.st_ino)
                ):
                    raise RuntimeError("authority commitment journal temporary replaced or aliased")
                self._load(tenant_id)
                os.replace(temporary, self._path)
                replaced = True
                self._check_descriptor(stream.fileno())
                with ExitStack() as resources:
                    directory = os.open(self._path.parent, os.O_RDONLY | os.O_DIRECTORY)
                    resources.callback(os.close, directory)
                    os.fsync(directory)
        except Exception as error:
            if replaced:
                raise AuthorityCommitmentIndeterminateError(
                    "authority commitment journal replacement occurred; durability or identity "
                    "indeterminate; reconcile the visible anchor"
                ) from error
            raise
        finally:
            if not replaced:
                temporary.unlink(missing_ok=True)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class VerifiedAuthorityReadSnapshot(_StrictModel):
    snapshot_id: str
    database_instance_id: str
    sqlite_snapshot_frontier: int
    trust_transition_head: str
    journal_head: str
    materialization_commitment: str
    authority_surface_digest: str
    authority_surface_version: Literal[1]
    read_graph_version: Literal[1]
    amr_fingerprint: str
    storage_mutation_generation: int
    file_wal_observation: str
    verifier_version: Literal[1]


class PreparedReadEdgeAttestation(_StrictModel):
    read_attempt_id: str
    query_variant: Literal["PLANNING_PUBLICATIONS", "PLANNING_PUBLICATIONS_R13"]
    prepared_query_fingerprint: str
    actual_edges: tuple[tuple[str, tuple[str, ...]], ...]
    authority_surface_digest: str
    read_graph_version: Literal[1]
    snapshot_id: str


class AuthorityReadResult(_StrictModel):
    disposition: Literal["RELEASED", "STALE_OR_INDETERMINATE_READ"]
    read_attempt_id: str
    result_digest: str | None
    canonical_result_bytes: bytes | None
    proof_fingerprint: str


class AuthorityReadFailure(RuntimeError):
    pass


class AuthoritySnapshotIntegrityError(AuthorityReadFailure):
    def __init__(self, tenant_id: str) -> None:
        super().__init__(
            "authority snapshot integrity: operation=capture_authority_snapshot_commitment "
            f"tenant={tenant_id} record=authority_storage"
        )


def _canonical_scalar(value: object) -> object:
    if isinstance(value, bytes):
        return {"base64": base64.b64encode(value).decode("ascii")}
    return value


def _file_wal_observation(path: Path) -> str:
    stat = path.stat()
    values = {
        "database_device": stat.st_dev,
        "database_inode": stat.st_ino,
        "database_path": str(path.resolve()),
        "wal_path": str(Path(f"{path}-wal").resolve()),
    }
    return hashlib.sha256(
        json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _schema_members(connection: sqlite3.Connection) -> tuple[tuple[str, tuple[str, ...]], ...]:
    rows = connection.execute(
        "SELECT name FROM main.sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name"
    ).fetchall()
    return tuple(
        (
            str(row[0]),
            tuple(
                str(column[1])
                for column in connection.execute(f'PRAGMA main.table_info("{row[0]}")').fetchall()
            ),
        )
        for row in rows
    )


def _authority_commitment(connection: sqlite3.Connection) -> str:
    materialized: list[dict[str, object]] = []
    for member in AUTHORITY_STORAGE_MEMBERS:
        if not member.authority_bearing:
            continue
        columns = ", ".join(f'"{column}"' for column in member.columns)
        order = ", ".join(str(index) for index in range(1, len(member.columns) + 1))
        rows = connection.execute(
            f'SELECT {columns} FROM main."{member.table}" ORDER BY {order}'
        ).fetchall()
        materialized.append(
            {
                "columns": member.columns,
                "rows": [[_canonical_scalar(value) for value in row] for row in rows],
                "table": member.table,
            }
        )
    return hashlib.sha256(
        json.dumps(materialized, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def capture_authority_snapshot_commitment(connection: sqlite3.Connection, tenant_id: str) -> str:
    """Hash physical main AMR in the caller's existing transaction.

    This neither authorizes a read nor verifies an independent journal anchor or
    database path identity. It leaves the caller's transaction and connection open.
    """
    try:
        if not connection.in_transaction:
            raise ValueError("authority snapshot requires an active transaction")
        expected = tuple((item.table, item.columns) for item in AUTHORITY_STORAGE_MEMBERS)
        if _schema_members(connection) != expected:
            raise ValueError("authority storage surface differs from physical schema")
        return _authority_commitment(connection)
    except (sqlite3.Error, ValueError, TypeError) as error:
        raise AuthoritySnapshotIntegrityError(tenant_id) from error


def capture_authority_storage_state(database: Path) -> tuple[str, str]:
    """Capture broker-private physical identity and full AMR in one read snapshot."""
    with closing(
        sqlite3.connect(f"file:{database}?mode=ro", uri=True, isolation_level=None)
    ) as connection:
        connection.execute("BEGIN")
        commitment = _authority_commitment(connection)
        observation = _file_wal_observation(database)
        connection.rollback()
    return commitment, observation


class BrokerAuthorityReader:
    def __init__(self, database: Path, database_instance_id: str, ledger: BrokerReadLedger) -> None:
        verify_invalidator_registry()
        verify_surface_registry_equality(AUTHORITY_READ_INVALIDATOR_REGISTRY)
        self._database = database
        self._database_instance_id = database_instance_id
        self._ledger = ledger
        self._registry_fingerprint = hashlib.sha256(invalidator_registry_bytes()).hexdigest()

    def execute(self, operation: ReadOperation) -> AuthorityReadResult:
        state = self._ledger.current_state(operation.tenant_id)
        self._validate_request_state(operation, state)
        self._ledger.begin(operation, state)
        with closing(
            sqlite3.connect(f"file:{self._database}?mode=ro", uri=True, isolation_level=None)
        ) as connection:
            connection.execute("BEGIN")
            observed_schema = _schema_members(connection)
            expected_schema = tuple(
                (item.table, item.columns) for item in AUTHORITY_STORAGE_MEMBERS
            )
            if observed_schema != expected_schema:
                raise AuthorityReadFailure("authority storage surface differs from physical schema")
            observation = _file_wal_observation(self._database)
            if observation != state.file_wal_observation:
                raise AuthorityReadFailure(
                    "file/WAL observation differs from authenticated state: "
                    f"expected={state.file_wal_observation} observed={observation}"
                )
            commitment = _authority_commitment(connection)
            if commitment != state.materialization_commitment:
                raise AuthorityReadFailure(
                    "full raw AMR commitment differs from authenticated state"
                )
            frontier_row = connection.execute(
                "SELECT head FROM tenant_heads WHERE tenant_id = ?", (operation.tenant_id,)
            ).fetchone()
            frontier = 0 if frontier_row is None else int(frontier_row[0])
            snapshot = VerifiedAuthorityReadSnapshot(
                snapshot_id=hashlib.sha256(
                    f"{operation.read_attempt_id}:{state.fingerprint()}:{commitment}".encode()
                ).hexdigest(),
                database_instance_id=self._database_instance_id,
                sqlite_snapshot_frontier=frontier,
                trust_transition_head=state.trust_transition_head,
                journal_head=state.journal_head,
                materialization_commitment=commitment,
                authority_surface_digest=AUTHORITY_STORAGE_SURFACE_DIGEST,
                authority_surface_version=1,
                read_graph_version=1,
                amr_fingerprint=AMR_FINGERPRINT,
                storage_mutation_generation=state.storage_mutation_generation,
                file_wal_observation=observation,
                verifier_version=1,
            )
            result_bytes, attestation = self._execute_planning_read(connection, operation, snapshot)
            if _file_wal_observation(self._database) != observation:
                raise AuthorityReadFailure("file/WAL observation changed before read release")
            proof_bytes = json.dumps(
                {
                    "attestation": attestation.model_dump(),
                    "registry_fingerprint": self._registry_fingerprint,
                    "snapshot": snapshot.model_dump(),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            proof_fingerprint = hashlib.sha256(proof_bytes).hexdigest()
            release = self._ledger.release(operation, state, proof_fingerprint, result_bytes)
            if release.state != "RELEASED":
                return AuthorityReadResult(
                    disposition="STALE_OR_INDETERMINATE_READ",
                    read_attempt_id=operation.read_attempt_id,
                    result_digest=None,
                    canonical_result_bytes=None,
                    proof_fingerprint=proof_fingerprint,
                )
            emitted = self._ledger.dequeue(operation)
            if emitted is None:
                raise AuthorityReadFailure("released response slot was not atomically enqueued")
            return AuthorityReadResult(
                disposition="RELEASED",
                read_attempt_id=operation.read_attempt_id,
                result_digest=hashlib.sha256(emitted).hexdigest(),
                canonical_result_bytes=emitted,
                proof_fingerprint=proof_fingerprint,
            )
            connection.rollback()

    @staticmethod
    def _validate_request_state(operation: ReadOperation, state: BrokerReadState) -> None:
        if (
            operation.broker_epoch != state.broker_epoch
            or operation.generation_id != state.owner_generation
            or operation.owner_id not in {"planning", "projections"}
            or state.owner_draining
            or state.authority_surface_digest != AUTHORITY_STORAGE_SURFACE_DIGEST
            or state.amr_fingerprint != AMR_FINGERPRINT
        ):
            raise AuthorityReadFailure("read request or current authority state is stale")

    @staticmethod
    def _execute_planning_read(
        connection: sqlite3.Connection,
        operation: ReadOperation,
        snapshot: VerifiedAuthorityReadSnapshot,
    ) -> tuple[bytes, PreparedReadEdgeAttestation]:
        scope_sql = (
            "AND operation_kind = 'planning.create_intention_line' "
            if operation.variant == "PLANNING_PUBLICATIONS_R13"
            else ""
        )
        publication_sql = (
            "SELECT tenant_id, operation_kind, idempotency_key, request_fingerprint, "
            "commit_sequence, record_ids FROM publications WHERE tenant_id = ? "
            + scope_sql
            + "AND (commit_sequence, operation_kind, idempotency_key) > (?, ?, ?) "
            "ORDER BY commit_sequence, operation_kind, idempotency_key LIMIT ?"
        )
        record_sql = (
            "SELECT tenant_id, record_id, owner, schema_id, canonical_bytes, commit_sequence "
            "FROM records WHERE tenant_id = ? AND commit_sequence IN ("
            "SELECT commit_sequence FROM publications WHERE tenant_id = ? "
            + scope_sql
            + "AND (commit_sequence, operation_kind, idempotency_key) > (?, ?, ?) "
            "ORDER BY commit_sequence, operation_kind, idempotency_key LIMIT ?"
            ") ORDER BY record_id LIMIT ?"
        )
        publication_parameters = (
            operation.tenant_id,
            operation.after_commit_sequence,
            operation.after_operation_kind,
            operation.after_idempotency_key,
            operation.max_rows,
        )
        record_parameters = (
            operation.tenant_id,
            operation.tenant_id,
            operation.after_commit_sequence,
            operation.after_operation_kind,
            operation.after_idempotency_key,
            operation.max_rows,
            operation.max_rows * 5 + 1,
        )
        actual_edges = (
            (
                "publications",
                (
                    "tenant_id",
                    "operation_kind",
                    "idempotency_key",
                    "request_fingerprint",
                    "commit_sequence",
                    "record_ids",
                ),
            ),
            (
                "records",
                (
                    "tenant_id",
                    "record_id",
                    "owner",
                    "schema_id",
                    "canonical_bytes",
                    "commit_sequence",
                ),
            ),
        )
        declared = (
            R13_PLANNING_PUBLICATION_READ_EDGES
            if operation.variant == "PLANNING_PUBLICATIONS_R13"
            else PLANNING_PUBLICATION_READ_EDGES
        )
        manifested_edges = tuple((item.table, item.columns) for item in declared)
        if actual_edges != manifested_edges:
            raise AuthorityReadFailure("prepared query edge differs from authority read graph")
        duplicate_sequence = connection.execute(
            "SELECT 1 FROM publications WHERE tenant_id = ? "
            "GROUP BY commit_sequence HAVING COUNT(*) > 1 LIMIT 1",
            (operation.tenant_id,),
        ).fetchone()
        if duplicate_sequence is not None:
            raise AuthorityReadFailure("publication commit sequence is not unique")
        publications = connection.execute(publication_sql, publication_parameters).fetchall()
        records = connection.execute(record_sql, record_parameters).fetchall()
        publication_record_ids = tuple(str(row[5]).split("\n") for row in publications)
        if any(
            len(record_ids) != 5 or len(set(record_ids)) != 5
            for record_ids in publication_record_ids
        ):
            raise AuthorityReadFailure("publication page is not an exact-five record manifest")
        expected_records = {
            (str(row[0]), record_id, int(row[4]))
            for row, record_ids in zip(publications, publication_record_ids, strict=True)
            for record_id in record_ids
        }
        actual_records = {(str(row[0]), str(row[1]), int(row[5])) for row in records}
        if actual_records != expected_records:
            raise AuthorityReadFailure("publication page is not referentially closed")
        prepared_fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "parameters": (publication_parameters, record_parameters),
                    "queries": (publication_sql, record_sql),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        attestation = PreparedReadEdgeAttestation(
            read_attempt_id=operation.read_attempt_id,
            query_variant=operation.variant,
            prepared_query_fingerprint=prepared_fingerprint,
            actual_edges=actual_edges,
            authority_surface_digest=AUTHORITY_STORAGE_SURFACE_DIGEST,
            read_graph_version=1,
            snapshot_id=snapshot.snapshot_id,
        )
        result = {
            "tenant_frontier": snapshot.sqlite_snapshot_frontier,
            "publications": [[_canonical_scalar(value) for value in row] for row in publications],
            "records": [[_canonical_scalar(value) for value in row] for row in records],
        }
        return json.dumps(result, sort_keys=True, separators=(",", ":")).encode(), attestation


__all__ = [
    "AMR_FINGERPRINT",
    "AuthorityCommitmentJournal",
    "AuthorityReadFailure",
    "AuthorityReadResult",
    "AuthoritySnapshotIntegrityError",
    "BrokerAuthorityReader",
    "PreparedReadEdgeAttestation",
    "VerifiedAuthorityReadSnapshot",
    "capture_authority_snapshot_commitment",
    "capture_authority_storage_state",
]
