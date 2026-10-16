"""Explicit lossless import of the old local-chat record stream; source is read-only.

Planning/R14 authority bundles are deliberately rejected: their journal and
custody sidecars require a separately specified migration. No partial import or
automatic reinterpretation of those records occurs here.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from chiplog.capabilities.agent_loop.contracts import RunRecord
from chiplog.capabilities.agent_loop.domain import validate_record
from chiplog.platform.postgres import Batch, PostgresIntegrityError, Record, UserDatabase

SOURCE_KIND = "sqlite.local-chat.v1"


def import_sqlite_chat(source: Path, database: UserDatabase, tenant: str) -> str:
    # mode=ro retains SQLite's committed WAL snapshot semantics. immutable=1
    # would be incorrect for a source whose committed rows remain in its WAL.
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        connection.execute("BEGIN")
        if connection.execute("SELECT singleton, store_version FROM store_metadata").fetchall() != [
            (1, 1)
        ]:
            raise ValueError("unsupported SQLite store version")
        if connection.execute(
            "SELECT tenant_id, generation, frontier FROM deletion_fences"
        ).fetchall() != [(tenant, "r6", 0)]:
            raise ValueError("import requires the original single-user local-chat fence")
        for table in ("evidence_inbox", "derivatives"):
            if connection.execute(f"SELECT count(*) FROM {table}").fetchone() != (0,):
                raise ValueError("non-chat durable state requires a separate migration")
        heads = connection.execute("SELECT tenant_id, head FROM tenant_heads").fetchall()
        if heads and (len(heads) != 1 or heads[0][0] != tenant):
            raise ValueError("source contains another tenant")
        source_head = 0 if not heads else heads[0][1]
        rows = connection.execute(
            "SELECT tenant_id, record_id, owner, schema_id, canonical_bytes, "
            "commit_sequence FROM records"
        ).fetchall()
        if any(row[0] != tenant for row in rows):
            raise ValueError("source contains another tenant")
        physical = {row[1]: (Record(row[1], row[2], row[3], bytes(row[4])), row[5]) for row in rows}
        batches = []
        seen: set[str] = set()
        runs: dict[str, RunRecord] = {}
        for owner, operation, key, fingerprint, sequence, ids in connection.execute(
            "SELECT tenant_id, operation_kind, idempotency_key, request_fingerprint, "
            "commit_sequence, "
            "record_ids FROM publications ORDER BY commit_sequence"
        ):
            members = ids.split("\n")
            if owner != tenant or operation != "agent_loop" or len(members) != 1:
                raise ValueError(
                    "source is not local-chat-only; planning/R14 bundles require separate migration"
                )
            identity = members[0]
            if identity in seen or identity not in physical or physical[identity][1] != sequence:
                raise PostgresIntegrityError("SQLite import publication membership mismatch")
            record = physical[identity][0]
            decoded = RunRecord.model_validate_json(record.canonical_bytes)
            if (
                record.owner != "agent_loop"
                or record.schema_id != "chiplog.agent-loop.record.v1"
                or decoded.tenant != tenant
                or decoded.head != identity
                or key != identity
                or decoded.canonical_bytes() != record.canonical_bytes
                or fingerprint != hashlib.sha256(record.canonical_bytes).hexdigest()
                or decoded.origin.provider != "local-cli"
                or decoded.policy.live_model is None
                or decoded.planning_receipts
            ):
                raise PostgresIntegrityError("SQLite import canonical chat record mismatch")
            validate_record(runs.get(decoded.run_id), decoded)
            runs[decoded.run_id] = decoded
            batches.append(Batch(operation, key, fingerprint, sequence, (record,)))
            seen.add(identity)
        if seen != set(physical) or tuple(batch.sequence for batch in batches) != tuple(
            range(1, source_head + 1)
        ):
            raise PostgresIntegrityError("SQLite import sequence or orphan record mismatch")
    encoded = json.dumps(
        {
            "tenant": tenant,
            "head": source_head,
            "batches": [
                [
                    batch.operation,
                    batch.idempotency_key,
                    batch.fingerprint,
                    batch.sequence,
                    [
                        [
                            record.record_id,
                            record.owner,
                            record.schema_id,
                            hashlib.sha256(record.canonical_bytes).hexdigest(),
                        ]
                        for record in batch.records
                    ],
                ]
                for batch in batches
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    source_fingerprint = hashlib.sha256(encoded).hexdigest()
    database.provision()
    with database.connect() as connection:
        database._check_identity(connection)
        connection.execute("SELECT head FROM user_state FOR UPDATE").fetchone()
        current = database._snapshot(connection)
        prior = connection.execute(
            "SELECT source_fingerprint FROM migrations WHERE source_kind=%s", (SOURCE_KIND,)
        ).fetchone()
        if prior is not None:
            if prior != (source_fingerprint,) or current.batches[: len(batches)] != tuple(batches):
                raise ValueError("SQLite import replay differs from original source")
            return "REPLAY"
        if current.head:
            raise ValueError("SQLite chat import requires an empty PostgreSQL target")
        for batch in batches:
            database.append(
                connection,
                batch.operation,
                batch.idempotency_key,
                batch.fingerprint,
                batch.records,
                batch.sequence - 1,
            )
        connection.execute(
            "INSERT INTO migrations VALUES (%s,%s)", (SOURCE_KIND, source_fingerprint)
        )
        if database._snapshot(connection).batches != tuple(batches):
            raise PostgresIntegrityError("SQLite import final batch readback differs")
    return "IMPORTED"
