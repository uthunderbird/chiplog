"""Journal persistence adapter; physical writes use the one existing EventAppender."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Literal

from chiplog.capabilities.evidence_journal.commands import Disposition, JournalRecord, Snapshot
from chiplog.platform._sqlite import EventAppender, PhysicalPublicationCommand, PhysicalRecord

OWNER = "evidence_journal"
SCHEMA = "chiplog.evidence_journal.record.v1"


class JournalIntegrityError(RuntimeError):
    """Authoritative journal data could not be decoded or bound to physical identity."""


class SQLiteJournal:
    def __init__(
        self,
        database: Path,
        appender: EventAppender,
        *,
        fence_generation: str,
        fence_frontier: int = 0,
    ) -> None:
        self._database = database
        self._appender = appender
        self._generation = fence_generation
        self._frontier = fence_frontier
        self.fault: Literal["none", "before_commit", "after_commit"] = "none"

    def snapshot(self, tenant: str) -> Snapshot:
        record_id = "snapshot"
        try:
            with closing(sqlite3.connect(self._database)) as connection:
                connection.execute("BEGIN")
                head = connection.execute(
                    "SELECT head FROM tenant_heads WHERE tenant_id = ?", (tenant,)
                ).fetchone()
                fence = connection.execute(
                    "SELECT generation, frontier FROM deletion_fences WHERE tenant_id = ?",
                    (tenant,),
                ).fetchone()
                if fence != (self._generation, self._frontier):
                    raise ValueError("stale or missing deletion fence")
                rows = connection.execute(
                    "SELECT record_id, schema_id, canonical_bytes FROM records "
                    "WHERE tenant_id = ? AND owner = ? ORDER BY commit_sequence, record_id",
                    (tenant, OWNER),
                ).fetchall()
                publications = connection.execute(
                    "SELECT idempotency_key, request_fingerprint, record_ids FROM publications "
                    "WHERE tenant_id = ? AND operation_kind = ?",
                    (tenant, "evidence_journal.command"),
                ).fetchall()
                bindings = {rid: (cid, fp) for cid, fp, rid in publications}
                if set(bindings) != {row[0] for row in rows} or len(bindings) != len(rows):
                    raise ValueError("incomplete journal publication membership")
                records = []
                for record_id, schema, payload in rows:
                    record = JournalRecord.model_validate_json(payload)
                    command_id, fingerprint = bindings[record_id]
                    if (
                        record.record_id != record_id
                        or record.tenant != tenant
                        or schema != SCHEMA
                        or command_id != record.command_id
                        or payload != record.canonical_bytes()
                        or hashlib.sha256(payload).hexdigest() != fingerprint
                    ):
                        raise ValueError("physical/logical identity or canonical digest mismatch")
                    records.append(record)
                return Snapshot(head=0 if head is None else int(head[0]), records=tuple(records))
        except (ValueError, TypeError, sqlite3.Error) as error:
            raise JournalIntegrityError(
                f"operation=snapshot tenant={tenant} record_id={record_id}"
            ) from error

    async def publish(
        self,
        record: JournalRecord,
        expected_head: int,
        guard: Callable[[], Literal["DENIED", "STALE", "INDETERMINATE"] | None],
    ) -> Disposition:
        payload = record.canonical_bytes()
        result = await self._appender.submit(
            PhysicalPublicationCommand(
                tenant_id=record.tenant,
                operation_kind="evidence_journal.command",
                idempotency_key=record.command_id,
                request_fingerprint=hashlib.sha256(payload).hexdigest(),
                expected_head=expected_head,
                fence_generation=self._generation,
                expected_fence_frontier=self._frontier,
                minimum_fence_frontier=self._frontier,
                records=(
                    PhysicalRecord(
                        record.record_id,
                        OWNER,
                        SCHEMA,
                        payload,
                        hashlib.sha256(payload).hexdigest(),
                    ),
                ),
                fault=self.fault,
                admission_guard=guard,
            )
        )
        return result.disposition
