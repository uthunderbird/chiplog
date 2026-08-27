from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path


class SQLiteTrustMaterializer:
    """Idempotent materializer; it cannot decide authority."""

    def __init__(self, database: Path) -> None:
        self._connection = sqlite3.connect(database)
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
        return (
            self._connection.execute(
                "SELECT 1 FROM trust_decisions WHERE decision_id = ?", (decision_id,)
            ).fetchone()
            is not None
        )

    def records(self) -> tuple[bytes, ...]:
        return tuple(
            bytes(row[0])
            for row in self._connection.execute(
                "SELECT canonical_bytes FROM trust_records ORDER BY rowid"
            )
        )

    def close(self) -> None:
        self._connection.close()
