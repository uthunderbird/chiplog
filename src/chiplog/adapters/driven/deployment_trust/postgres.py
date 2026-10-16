"""Trust ports on the caller's locked PostgreSQL user transaction.

The trust aggregate retains its record formats and authorization rules. The PG
transaction replaces the former independently recoverable sidecar materializer.
"""

from __future__ import annotations

import hashlib
from typing import Literal

from chiplog.platform.postgres import Connection, PostgresIntegrityError


class PostgresTrustStore:
    def __init__(self, connection: Connection, tenant: str) -> None:
        self.connection = connection
        self.tenant = tenant

    def entries(self) -> tuple[tuple[str, str | None, bytes], ...]:
        result = []
        predecessor = None
        for identity, prior, payload in self.connection.execute(
            "SELECT decision_id, predecessor, canonical_bytes FROM trust_journal ORDER BY sequence"
        ):
            body = bytes(payload)
            expected = hashlib.sha256(
                (predecessor or "GENESIS").encode() + b"\x00" + body
            ).hexdigest()
            if identity != expected or prior != predecessor:
                raise PostgresIntegrityError(
                    f"operation=trust.entries tenant={self.tenant} record_id={identity}"
                ) from ValueError("trust journal predecessor mismatch")
            result.append((str(identity), prior, body))
            predecessor = identity
        return tuple(result)

    def append(self, decision: bytes, predecessor: str | None) -> str:
        entries = self.entries()
        if (entries[-1][0] if entries else None) != predecessor:
            raise PostgresIntegrityError("stale trust journal predecessor")
        identity = hashlib.sha256(
            (predecessor or "GENESIS").encode() + b"\x00" + decision
        ).hexdigest()
        self.connection.execute(
            "INSERT INTO trust_journal(decision_id, predecessor, canonical_bytes) "
            "VALUES (%s,%s,%s)",
            (identity, predecessor, decision),
        )
        return identity

    def observation(self, decision_id: str) -> Literal["DECIDED", "NO_DECISION", "AMBIGUOUS"]:
        return "DECIDED" if any(row[0] == decision_id for row in self.entries()) else "NO_DECISION"

    def materialize(self, decision_id: str, records: tuple[bytes, ...]) -> str:
        previous = self.connection.execute(
            "SELECT records FROM trust_materializations WHERE decision_id=%s", (decision_id,)
        ).fetchone()
        if previous is not None:
            if tuple(bytes(value) for value in previous[0]) != records:
                raise PostgresIntegrityError("trust materialization replay conflict")
            return "REPLAY"
        self.connection.execute(
            "INSERT INTO trust_materializations VALUES (%s,%s)", (decision_id, list(records))
        )
        return "MATERIALIZED"

    def materialized(self, decision_id: str) -> bool:
        return (
            self.connection.execute(
                "SELECT 1 FROM trust_materializations WHERE decision_id=%s", (decision_id,)
            ).fetchone()
            is not None
        )

    def records(self) -> tuple[bytes, ...]:
        return tuple(
            bytes(record)
            for (records,) in self.connection.execute(
                "SELECT m.records FROM trust_journal j JOIN trust_materializations m "
                "USING(decision_id) ORDER BY j.sequence"
            )
            for record in records
        )

    def record(self, decision_id: str, ordinal: int) -> bytes | None:
        row = self.connection.execute(
            "SELECT records FROM trust_materializations WHERE decision_id=%s", (decision_id,)
        ).fetchone()
        if row is None or not 0 <= ordinal < len(row[0]):
            return None
        return bytes(row[0][ordinal])
