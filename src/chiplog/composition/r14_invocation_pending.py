"""Exact one-publication prefix for a selected R14 resolver invocation."""

from __future__ import annotations

import sqlite3
from contextlib import closing

from chiplog.platform.authority_reads import capture_authority_snapshot_commitment
from chiplog.platform.publication_readback import inspect_publication
from chiplog.platform.workspace_snapshot import read_connection


def authenticate_selected_invocation_prefix_held(runtime, actual: str, anchored: str) -> bool:
    """Authenticate the exact selected invocation as absent or fully materialized."""
    runtime._authority_gate().require_held()
    try:
        from chiplog.composition import r14_original_resolver_invocation_publication as inv

        runtime._check_database_identity()
        pending = runtime._pending()
        if len(pending) != 1 or runtime._pending_owners() or runtime._pending_gate_publications():
            return False
        entry = pending[0]
        if entry.get("operation_kind") != inv.OPERATION:
            return False
        evidence = inv.RetainedOriginalResolverInvocationPublication.model_validate_json(
            entry.get(inv.EVIDENCE_FIELD)
        )
        batch = inv.command(evidence)
        predecessor, resulting = entry.get("predecessor"), entry.get("resulting")
        if (
            runtime._publication(entry) != batch
            or batch.tenant_id != runtime._tenant_id
            or evidence.database_id != runtime._database_identity[0]
            or entry.get("operation_id") != batch.idempotency_key
            or predecessor != evidence.predecessor
            or batch.expected_head != evidence.expected_head
            or anchored != runtime._commitment_journal.load(runtime._tenant_id)
        ):
            return False
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
                        if (
                            historical.execute(
                                "DELETE FROM records WHERE tenant_id=? AND record_id=?",
                                (runtime._tenant_id, row.record_id),
                            ).rowcount
                            != 1
                        ):
                            return False
                    if (
                        historical.execute(
                            "DELETE FROM publications WHERE tenant_id=? AND operation_kind=? "
                            "AND idempotency_key=?",
                            (runtime._tenant_id, batch.operation_kind, batch.idempotency_key),
                        ).rowcount
                        != 1
                    ):
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
    except ValueError, TypeError, RuntimeError, OSError, sqlite3.Error:
        return False
