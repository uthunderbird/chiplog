from __future__ import annotations

import hashlib
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from pathlib import Path

from chiplog.platform.authority_gate import AuthorityGate, FileIdentity, checked_file_identity


class SQLiteTrustMaterializer:
    """Idempotent materializer; it cannot decide authority."""

    def __init__(self, database: Path) -> None:
        self._initialize_bound(database, None)

    @classmethod
    def for_authority_bundle(
        cls, database: Path, *, authority_gate: AuthorityGate
    ) -> SQLiteTrustMaterializer:
        if not isinstance(authority_gate, AuthorityGate):
            raise TypeError("bound trust adapter requires an AuthorityGate")
        instance = cls.__new__(cls)
        instance._initialize_bound(database, authority_gate)
        return instance

    def _initialize_bound(self, database: Path, authority_gate: AuthorityGate | None) -> None:
        self._authority_gate = authority_gate
        self._lock = threading.RLock()
        self._identity: FileIdentity | None = None
        if authority_gate is not None and database.is_symlink():
            raise RuntimeError("canonical trust sidecar cannot be a symbolic link")
        self._path = database.resolve(strict=False)
        with self._scope():
            prior = checked_file_identity(self._path) if self._path.exists() else None
            self._initialize(self._path)
            try:
                self._identity = checked_file_identity(self._path, prior)
            except BaseException:
                self._connection.close()
                raise

    @property
    def authority_gate(self) -> AuthorityGate | None:
        return self._authority_gate

    @contextmanager
    def _scope(self) -> Iterator[None]:
        gate = nullcontext() if self._authority_gate is None else self._authority_gate.hold()
        with gate, self._lock:
            if self._identity is not None:
                checked_file_identity(self._path, self._identity)
            yield

    def physical_identity(self) -> FileIdentity:
        with self._scope():
            assert self._identity is not None
            return checked_file_identity(self._path, self._identity)

    def _initialize(self, database: Path) -> None:
        self._connection = sqlite3.connect(database, check_same_thread=False)
        self._connection.execute("PRAGMA foreign_keys = ON")
        with self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS trust_decisions(
                    decision_id TEXT PRIMARY KEY, records_digest TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS trust_records(
                    decision_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    canonical_bytes BLOB NOT NULL,
                    PRIMARY KEY(decision_id, ordinal),
                    FOREIGN KEY(decision_id) REFERENCES trust_decisions(decision_id)
                );
                """
            )

    def materialize(self, decision_id: str, records: tuple[bytes, ...]) -> str:
        with self._scope():
            return self._materialize(decision_id, records)

    def _materialize(self, decision_id: str, records: tuple[bytes, ...]) -> str:
        digest = hashlib.sha256(b"\x00".join(records)).hexdigest()
        existing = self._connection.execute(
            "SELECT records_digest FROM trust_decisions WHERE decision_id = ?", (decision_id,)
        ).fetchone()
        if existing is not None:
            if existing[0] != digest:
                raise RuntimeError("materialization replay conflict")
            return "REPLAY"
        with self._connection:
            self._connection.execute(
                "INSERT INTO trust_decisions(decision_id, records_digest) VALUES (?, ?)",
                (decision_id, digest),
            )
            self._connection.executemany(
                "INSERT INTO trust_records(decision_id, ordinal, canonical_bytes) VALUES (?, ?, ?)",
                ((decision_id, index, record) for index, record in enumerate(records)),
            )
        return "MATERIALIZED"

    def materialized(self, decision_id: str) -> bool:
        with self._scope():
            return self._materialized(decision_id)

    def _materialized(self, decision_id: str) -> bool:
        return (
            self._connection.execute(
                "SELECT 1 FROM trust_decisions WHERE decision_id = ?", (decision_id,)
            ).fetchone()
            is not None
        )

    def records(self) -> tuple[bytes, ...]:
        with self._scope():
            return self._records()

    def _records(self) -> tuple[bytes, ...]:
        return tuple(
            bytes(row[0])
            for row in self._connection.execute(
                "SELECT canonical_bytes FROM trust_records ORDER BY rowid"
            )
        )

    def close(self) -> None:
        with self._lock:
            self._connection.close()
