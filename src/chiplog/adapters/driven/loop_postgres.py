"""The existing loop domain over PostgreSQL atomic publication batches."""

from __future__ import annotations

import hashlib
from collections.abc import Callable

from chiplog.capabilities.agent_loop.contracts import LoopRejected, LoopSnapshot, RunRecord
from chiplog.capabilities.agent_loop.domain import validate_record
from chiplog.platform.postgres import PostgresIntegrityError, Record, Snapshot, UserDatabase

OWNER = "agent_loop"
SCHEMA = "chiplog.agent-loop.record.v1"


class PostgresLoopStore:
    def __init__(self, database: UserDatabase, tenant: str, *, local_chat: bool = False) -> None:
        self.database, self.tenant, self.local_chat = database, tenant, local_chat

    def _decode(self, snapshot: Snapshot) -> LoopSnapshot:
        records = []
        heads: dict[str, RunRecord] = {}
        record_id = "<enumeration>"
        try:
            for batch in snapshot.batches:
                if batch.operation != "agent_loop":
                    continue
                if len(batch.records) != 1:
                    raise ValueError("loop batch must have exactly one Run record")
                physical = batch.records[0]
                record_id = physical.record_id
                record = RunRecord.model_validate_json(physical.canonical_bytes)
                if (
                    physical.owner != OWNER
                    or physical.schema_id != SCHEMA
                    or record.tenant != self.tenant
                    or record.head != record_id
                    or record.canonical_bytes() != physical.canonical_bytes
                    or batch.idempotency_key != record.head
                    or batch.fingerprint != hashlib.sha256(physical.canonical_bytes).hexdigest()
                ):
                    raise ValueError("Run record identity or publication mismatch")
                validate_record(heads.get(record.run_id), record)
                heads[record.run_id] = record
                records.append(record)
            return LoopSnapshot(tenant_head=snapshot.head, records=tuple(records))
        except (ValueError, TypeError, KeyError) as error:
            raise PostgresIntegrityError(
                f"operation=loop.snapshot tenant={self.tenant} record_id={record_id}"
            ) from error

    def snapshot(self) -> LoopSnapshot:
        return self._decode(self.database.snapshot())

    async def publish(
        self,
        record: RunRecord,
        expected: LoopSnapshot,
        validate: Callable[[LoopSnapshot], None] | None = None,
    ) -> None:
        if record.tenant != self.tenant:
            raise LoopRejected("foreign tenant publication")

        def guard(snapshot: Snapshot) -> None:
            current = self._decode(snapshot)
            if current != expected:
                raise LoopRejected("stale exact loop snapshot")
            prior = [row for row in current.records if row.run_id == record.run_id]
            validate_record(prior[-1] if prior else None, record)
            if self.local_chat and (
                record.policy.live_model is None
                or record.origin.provider != "local-cli"
                or record.planning_receipts
            ):
                raise LoopRejected("local chat requires live binding and no planning authority")
            if validate is not None:
                validate(current)

        payload = record.canonical_bytes()
        result = self.database.publish(
            "agent_loop",
            record.head,
            hashlib.sha256(payload).hexdigest(),
            (Record(record.head, OWNER, SCHEMA, payload),),
            expected.tenant_head,
            guard,
        )
        if result.disposition not in ("COMMITTED", "REPLAY"):
            raise LoopRejected("publication " + result.disposition)
