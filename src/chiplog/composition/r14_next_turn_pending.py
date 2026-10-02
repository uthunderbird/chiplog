"""Exact one-publication V27 pending prefix for historical source reads only."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import TYPE_CHECKING

from chiplog.platform.authority_reads import capture_authority_snapshot_commitment
from chiplog.platform.publication_readback import inspect_publication
from chiplog.platform.workspace_snapshot import read_connection

from .r14_next_turn_records import NEXT_TURN_PUBLICATION, next_turn_command, retained_next_turn

if TYPE_CHECKING:
    from .common_cli_execution_runtime import CommonCliExecutionRuntime


def authenticate_selected_next_turn_prefix_held(
    runtime: CommonCliExecutionRuntime, actual: str, anchored: str
) -> bool:
    """Permit original-source reads across one selected V27 write, not Turn authority."""
    runtime._authority_gate().require_held()
    try:
        runtime._check_database_identity()
        pending = runtime._pending()
        if len(pending) != 1 or runtime._pending_owners() or runtime._pending_gate_publications():
            return False
        entry = pending[0]
        if entry.get("operation_kind") != NEXT_TURN_PUBLICATION:
            return False
        evidence = retained_next_turn(entry)
        batch = next_turn_command(evidence)
        if (
            runtime._publication(entry) != batch
            or batch.tenant_id != runtime._tenant_id
            or evidence.request.cut.database_id != runtime._database_identity[0]
            or entry.get("predecessor") != evidence.request.cut.materialization_commitment
            or batch.expected_head != evidence.request.cut.tenant_commit_sequence
            or anchored != runtime._commitment_journal.load(runtime._tenant_id)
        ):
            return False
        predecessor, resulting = entry.get("predecessor"), entry.get("resulting")
        with read_connection(runtime._database) as connection:
            if actual != capture_authority_snapshot_commitment(connection, runtime._tenant_id):
                return False
            state = inspect_publication(connection, batch, batch.expected_head + 1)
            if state == "ABSENT":
                if actual != predecessor or anchored != predecessor:
                    return False
            elif state == "COMPLETE":
                if actual != resulting or anchored not in (predecessor, resulting):
                    return False
            else:
                return False
            with closing(sqlite3.connect(":memory:")) as historical:
                connection.backup(historical)
                historical.execute("BEGIN")
                if state == "COMPLETE":
                    for row in batch.records:
                        if historical.execute(
                            "DELETE FROM records WHERE tenant_id=? AND record_id=?",
                            (runtime._tenant_id, row.record_id),
                        ).rowcount != 1:
                            return False
                    if historical.execute(
                        "DELETE FROM publications WHERE tenant_id=? AND operation_kind=? "
                        "AND idempotency_key=?",
                        (runtime._tenant_id, batch.operation_kind, batch.idempotency_key),
                    ).rowcount != 1:
                        return False
                    historical.execute(
                        "UPDATE tenant_heads SET head=? WHERE tenant_id=?",
                        (batch.expected_head, runtime._tenant_id),
                    )
                if (
                    capture_authority_snapshot_commitment(historical, runtime._tenant_id)
                    != predecessor
                ):
                    return False
                sequence = batch.expected_head + 1
                for row in batch.records:
                    historical.execute(
                        "INSERT INTO records VALUES(?,?,?,?,?,?)",
                        (
                            runtime._tenant_id,
                            row.record_id,
                            row.owner,
                            row.schema_id,
                            row.canonical_bytes,
                            sequence,
                        ),
                    )
                historical.execute(
                    "INSERT INTO publications VALUES(?,?,?,?,?,?)",
                    (
                        runtime._tenant_id,
                        batch.operation_kind,
                        batch.idempotency_key,
                        batch.request_fingerprint,
                        sequence,
                        "\n".join(row.record_id for row in batch.records),
                    ),
                )
                historical.execute(
                    "UPDATE tenant_heads SET head=? WHERE tenant_id=?",
                    (sequence, runtime._tenant_id),
                )
                if (
                    capture_authority_snapshot_commitment(historical, runtime._tenant_id)
                    != resulting
                ):
                    return False
        runtime._check_database_identity()
        return True
    except (ValueError, TypeError, RuntimeError, OSError, sqlite3.Error):
        return False
