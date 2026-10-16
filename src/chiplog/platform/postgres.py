"""PostgreSQL persistence: one database and one serialized head per user.

Server credentials are operator configuration. Application identities only enter
the fixed SHA-256 database-name mapping, never a connection string or SQL text.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import psycopg
from psycopg import sql

type Connection = psycopg.Connection[tuple[Any, ...]]

SCHEMA = """
CREATE TABLE IF NOT EXISTS user_state (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    user_identity text NOT NULL, head bigint NOT NULL CHECK (head >= 0), worker text
);
CREATE TABLE IF NOT EXISTS publications (
    operation text NOT NULL, idempotency_key text NOT NULL,
    fingerprint text NOT NULL, sequence bigint NOT NULL UNIQUE,
    record_ids text[] NOT NULL, PRIMARY KEY (operation, idempotency_key)
);
CREATE TABLE IF NOT EXISTS records (
    record_id text PRIMARY KEY, owner text NOT NULL, schema_id text NOT NULL,
    canonical_bytes bytea NOT NULL, sequence bigint NOT NULL
        REFERENCES publications(sequence) DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE IF NOT EXISTS trust_journal (
    sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    decision_id text NOT NULL UNIQUE, predecessor text, canonical_bytes bytea NOT NULL
);
CREATE TABLE IF NOT EXISTS trust_materializations (
    decision_id text PRIMARY KEY REFERENCES trust_journal(decision_id),
    records bytea[] NOT NULL
);
CREATE TABLE IF NOT EXISTS gate_current (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    canonical_bytes bytea NOT NULL, proof bytea NOT NULL
);
CREATE TABLE IF NOT EXISTS gate_handoffs (
    operation text PRIMARY KEY, request bytea NOT NULL, payload bytea NOT NULL,
    entitlement text NOT NULL, mode text NOT NULL
);
CREATE TABLE IF NOT EXISTS migrations (
    source_kind text PRIMARY KEY, source_fingerprint text NOT NULL
);
"""


class PostgresIntegrityError(RuntimeError):
    """Authoritative PostgreSQL state is not a complete valid publication."""


class PostgresTransientReadError(RuntimeError):
    """PostgreSQL definitively aborted a read transaction; retry may be admitted."""


@contextmanager
def transient_read_boundary(tenant: str) -> Iterator[None]:
    try:
        yield
    except (
        psycopg.errors.LockNotAvailable,
        psycopg.errors.DeadlockDetected,
        psycopg.errors.SerializationFailure,
    ) as error:
        raise PostgresTransientReadError(
            f"operation=history.read tenant={tenant} record_id=<snapshot>"
        ) from error


@dataclass(frozen=True)
class Record:
    record_id: str
    owner: str
    schema_id: str
    canonical_bytes: bytes


@dataclass(frozen=True)
class Batch:
    operation: str
    idempotency_key: str
    fingerprint: str
    sequence: int
    records: tuple[Record, ...]


@dataclass(frozen=True)
class Snapshot:
    head: int
    batches: tuple[Batch, ...]


@dataclass(frozen=True)
class PublicationResult:
    disposition: str
    sequence: int | None = None


@dataclass(frozen=True)
class PostgresServer:
    # Do not include operator credentials in repr, exceptions or status output.
    conninfo: str = field(repr=False)

    @classmethod
    def from_environment(cls) -> PostgresServer:
        value = os.environ.get("CHIPLOG_POSTGRES_DSN")
        if not value:
            raise RuntimeError("CHIPLOG_POSTGRES_DSN operator configuration is required")
        return cls(value)

    def user_database(self, identity: str) -> UserDatabase:
        if not identity:
            raise ValueError("empty user identity")
        # PostgreSQL identifiers are at most 63 bytes. This trusted mapping uses
        # 220 hash bits and an ASCII prefix, independent of arbitrary input text.
        name = "chiplog_" + hashlib.sha256(identity.encode()).hexdigest()[:55]
        return UserDatabase(self, identity, name)


@dataclass(frozen=True)
class UserDatabase:
    server: PostgresServer
    identity: str
    name: str

    def provision(self) -> None:
        with psycopg.connect(self.server.conninfo, autocommit=True) as admin:
            # CREATE DATABASE cannot run in a transaction. Serialize the catalog
            # check/create across independent provisioners on this same instance.
            key = int.from_bytes(hashlib.sha256(self.name.encode()).digest()[:8], signed=True)
            admin.execute("SELECT pg_advisory_lock(%s)", (key,))
            if (
                admin.execute(
                    "SELECT 1 FROM pg_database WHERE datname = %s", (self.name,)
                ).fetchone()
                is None
            ):
                admin.execute(
                    sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(
                        sql.Identifier(self.name)
                    )
                )
            with self.connect() as connection:
                connection.execute(SCHEMA)
                connection.execute(
                    "INSERT INTO user_state(singleton, user_identity, head) VALUES (true, %s, 0) "
                    "ON CONFLICT (singleton) DO NOTHING",
                    (self.identity,),
                )
                self._check_identity(connection)

    def connect(self) -> psycopg.Connection[tuple[Any, ...]]:
        return psycopg.connect(self.server.conninfo, dbname=self.name)

    def _check_identity(self, connection: psycopg.Connection[tuple[Any, ...]]) -> None:
        row = connection.execute("SELECT user_identity FROM user_state").fetchone()
        if row != (self.identity,):
            raise PostgresIntegrityError("database user identity mismatch")

    @contextmanager
    def read(self) -> Iterator[psycopg.Connection[tuple[Any, ...]]]:
        with self.connect() as connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            self._check_identity(connection)
            yield connection

    def snapshot(self) -> Snapshot:
        with self.read() as connection:
            return self._snapshot(connection)

    def _snapshot(self, connection: psycopg.Connection[tuple[Any, ...]]) -> Snapshot:
        head_row = connection.execute("SELECT head FROM user_state").fetchone()
        if head_row is None:
            raise PostgresIntegrityError("missing serialized user head")
        head = int(head_row[0])
        records = connection.execute(
            "SELECT record_id, owner, schema_id, canonical_bytes, sequence "
            "FROM records ORDER BY sequence, record_id"
        ).fetchall()
        physical = {
            str(row[0]): (Record(str(row[0]), str(row[1]), str(row[2]), bytes(row[3])), int(row[4]))
            for row in records
        }
        batches = []
        seen: set[str] = set()
        for operation, key, fingerprint, sequence, ids in connection.execute(
            "SELECT operation, idempotency_key, fingerprint, sequence, record_ids "
            "FROM publications ORDER BY sequence"
        ):
            members = tuple(str(value) for value in ids)
            if (
                not members
                or len(set(members)) != len(members)
                or any(
                    value in seen or value not in physical or physical[value][1] != sequence
                    for value in members
                )
            ):
                raise PostgresIntegrityError("incomplete publication membership")
            seen.update(members)
            batches.append(
                Batch(
                    str(operation),
                    str(key),
                    str(fingerprint),
                    int(sequence),
                    tuple(physical[value][0] for value in members),
                )
            )
        if seen != set(physical) or tuple(batch.sequence for batch in batches) != tuple(
            range(1, head + 1)
        ):
            raise PostgresIntegrityError("publication sequence or orphan record mismatch")
        return Snapshot(head, tuple(batches))

    def publish(
        self,
        operation: str,
        key: str,
        fingerprint: str,
        records: tuple[Record, ...],
        expected_head: int,
        guard: Callable[[Snapshot], None] | None = None,
    ) -> PublicationResult:
        if not records or len({record.record_id for record in records}) != len(records):
            raise ValueError("batch records must be nonempty and unique")
        with self.connect() as connection:
            self._check_identity(connection)
            row = connection.execute("SELECT head FROM user_state FOR UPDATE").fetchone()
            if row is None:
                raise PostgresIntegrityError("missing serialized user head")
            previous = connection.execute(
                "SELECT fingerprint, sequence, record_ids FROM publications "
                "WHERE operation = %s AND idempotency_key = %s",
                (operation, key),
            ).fetchone()
            if previous is not None:
                current = self._snapshot(connection)
                batch = next(batch for batch in current.batches if batch.sequence == previous[1])
                same = batch.fingerprint == fingerprint and batch.records == records
                return PublicationResult("REPLAY" if same else "CONFLICT", batch.sequence)
            if row[0] != expected_head:
                return PublicationResult("STALE")
            if guard is not None:
                guard(self._snapshot(connection))
            return self.append(connection, operation, key, fingerprint, records, expected_head)

    def append(
        self,
        connection: psycopg.Connection[tuple[Any, ...]],
        operation: str,
        key: str,
        fingerprint: str,
        records: tuple[Record, ...],
        expected_head: int,
    ) -> PublicationResult:
        """Append on the caller's locked, validated user transaction."""
        sequence = expected_head + 1
        connection.execute(
            "INSERT INTO publications VALUES (%s, %s, %s, %s, %s)",
            (operation, key, fingerprint, sequence, [record.record_id for record in records]),
        )
        for record in records:
            connection.execute(
                "INSERT INTO records VALUES (%s, %s, %s, %s, %s)",
                (
                    record.record_id,
                    record.owner,
                    record.schema_id,
                    record.canonical_bytes,
                    sequence,
                ),
            )
        connection.execute("UPDATE user_state SET head = %s", (sequence,))
        return PublicationResult("COMMITTED", sequence)
