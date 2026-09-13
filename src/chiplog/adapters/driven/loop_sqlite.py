"""Durable loop records use the existing tenant EventAppender exclusively."""

from __future__ import annotations

import base64
import hashlib
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from chiplog.capabilities.agent_loop.contracts import (
    DurableCompanion,
    LoopRejected,
    LoopSnapshot,
    RunRecord,
    TransitionAuthority,
)
from chiplog.capabilities.agent_loop.domain import validate_record
from chiplog.platform._sqlite import EventAppender, PhysicalPublicationCommand, PhysicalRecord
from chiplog.platform.workspace_snapshot import read_connection

OWNER = "agent_loop"
SCHEMA = "chiplog.agent-loop.record.v1"


class LoopIntegrityError(RuntimeError):
    pass


class SQLiteLoopStore:
    def __init__(
        self,
        database: Path,
        appender: EventAppender,
        tenant: str,
        fence_generation: str = "r13",
        fence_frontier: int = 0,
        authority: TransitionAuthority | None = None,
    ) -> None:
        self._database, self._appender, self._tenant = database, appender, tenant
        self._generation, self._frontier = fence_generation, fence_frontier
        self._authority = authority

    def snapshot(self) -> LoopSnapshot:
        record_id = "<enumeration>"
        try:
            with read_connection(self._database) as connection:
                head = connection.execute(
                    "SELECT head FROM tenant_heads WHERE tenant_id = ?",
                    (self._tenant,),
                ).fetchone()
                fence = connection.execute(
                    "SELECT generation, frontier FROM deletion_fences WHERE tenant_id = ?",
                    (self._tenant,),
                ).fetchone()
                if fence != (self._generation, self._frontier):
                    raise ValueError("missing/stale deletion fence")
                rows = connection.execute(
                    "SELECT record_id, schema_id, canonical_bytes, commit_sequence FROM records "
                    "WHERE tenant_id = ? AND owner = ? ORDER BY commit_sequence",
                    (self._tenant, OWNER),
                ).fetchall()
                publications = connection.execute(
                    "SELECT record_ids, request_fingerprint, commit_sequence, idempotency_key "
                    "FROM publications "
                    "WHERE tenant_id = ? AND operation_kind = ?",
                    (self._tenant, "agent_loop"),
                ).fetchall()
                bindings = {
                    rid.split("\n")[0]: (fp, seq, key) for rid, fp, seq, key in publications
                }
                if len(bindings) != len(rows) or set(bindings) != {row[0] for row in rows}:
                    raise ValueError("incomplete publication membership")
                records = []
                heads: dict[str, RunRecord] = {}
                for record_id, schema, payload, seq in rows:
                    record = RunRecord.model_validate_json(payload)
                    if (
                        record.tenant != self._tenant
                        or record.head != record_id
                        or schema != SCHEMA
                        or payload != record.canonical_bytes()
                        or bindings[record_id]
                        != (hashlib.sha256(payload).hexdigest(), seq, record_id)
                    ):
                        raise ValueError("record/publication/ancestry identity mismatch")
                    validate_record(heads.get(record.run_id), record)
                    publication = next(row for row in publications if row[3] == record.head)
                    expected_ids: tuple[str, ...] = (record.head,)
                    if self._authority is not None:
                        companions = self._authority.companions(record)
                        expected_ids += tuple(item.record_id for item in companions)
                        for companion in companions:
                            physical = connection.execute(
                                "SELECT owner, schema_id, canonical_bytes, commit_sequence "
                                "FROM records "
                                "WHERE tenant_id=? AND record_id=?",
                                (self._tenant, companion.record_id),
                            ).fetchone()
                            if physical != (
                                companion.owner,
                                companion.schema_id,
                                base64.b64decode(companion.payload_base64, validate=True),
                                seq,
                            ):
                                raise ValueError("missing or corrupt atomic acceptance companion")
                    elif record.event == "CompleteAcceptance":
                        expected_ids += (record.run_id + "/accepted",)
                    if tuple(publication[0].split("\n")) != expected_ids:
                        raise ValueError("incomplete or extra atomic publication members")
                    heads[record.run_id] = record
                    records.append(record)
                return LoopSnapshot(
                    tenant_head=0 if head is None else head[0], records=tuple(records)
                )
        except (ValueError, TypeError, sqlite3.Error) as error:
            raise LoopIntegrityError(
                f"operation=snapshot tenant={self._tenant} record_id={record_id}"
            ) from error

    async def publish(
        self,
        record: RunRecord,
        expected: LoopSnapshot,
        validate: Callable[[LoopSnapshot], None] | None = None,
    ) -> None:
        if record.tenant != self._tenant:
            raise LoopRejected("foreign tenant publication")
        payload = record.canonical_bytes()
        companions = self._authority.companions(record) if self._authority is not None else ()

        def guard() -> Literal["STALE"] | None:
            # Called by EventAppender after BEGIN IMMEDIATE, before any append.
            # The writer lock prevents another commit between this complete read
            # and publication; equality includes every prior response and outcome.
            current = self.snapshot()
            if current != expected:
                return "STALE"
            prior = [row for row in current.records if row.run_id == record.run_id]
            validate_record(prior[-1] if prior else None, record)
            if self._authority is not None:
                self._authority.validate(prior[-1] if prior else None, record)
            if validate is not None:
                validate(current)
            return None

        result = await self._appender.submit(
            PhysicalPublicationCommand(
                tenant_id=self._tenant,
                operation_kind="agent_loop",
                idempotency_key=record.head,
                request_fingerprint=hashlib.sha256(payload).hexdigest(),
                expected_head=expected.tenant_head,
                fence_generation=self._generation,
                expected_fence_frontier=self._frontier,
                minimum_fence_frontier=self._frontier,
                records=(
                    PhysicalRecord(
                        record.head, OWNER, SCHEMA, payload, hashlib.sha256(payload).hexdigest()
                    ),
                    *(
                        PhysicalRecord(
                            item.record_id,
                            item.owner,
                            item.schema_id,
                            base64.b64decode(item.payload_base64, validate=True),
                            hashlib.sha256(
                                base64.b64decode(item.payload_base64, validate=True)
                            ).hexdigest(),
                        )
                        for item in companions
                    ),
                ),
                admission_guard=guard,
                decision_guard=(
                    lambda commitment: self._decision(record, expected, commitment, companions)
                )
                if self._authority is not None
                else None,
            )
        )
        if result.disposition not in ("COMMITTED", "REPLAY"):
            raise LoopRejected("publication " + result.disposition)
        if self._authority is not None:
            self._authority.committed(record)

    def _decision(
        self,
        record: RunRecord,
        expected: LoopSnapshot,
        commitment: str,
        companions: tuple[DurableCompanion, ...],
    ) -> None:
        if self._authority is None:
            raise LoopRejected("missing publication authority")
        self._authority.decide(record, expected, commitment, companions)
