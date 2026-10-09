"""Authenticate a contiguous selected R14 publication suffix back to a cut."""

from __future__ import annotations

import sqlite3
from contextlib import closing

from chiplog.platform.authority_reads import capture_authority_snapshot_commitment
from chiplog.platform.publication_readback import inspect_publication
from chiplog.platform.workspace_snapshot import read_connection

_SUFFIX_OPERATIONS = frozenset(
    {
        "agent_loop.original-resolver-invocation.v1",
        "agent_loop.original-call-resolution.v1",
    }
)


def authenticate_selected_r14_suffix_held(
    runtime, actual: str, anchored: str, *, target_commitment: str
) -> bool:
    """Verify every later R14 write as one exact physical chain to target."""
    runtime._authority_gate().require_held()
    try:
        runtime._check_database_identity()
        if runtime._pending_owners() or runtime._pending_gate_publications():
            return False
        entries = []
        for _, _, raw in runtime._loop_decisions().entries():
            import json

            entry = json.loads(raw)
            if entry.get("kind") != "DECIDED":
                continue
            operation = entry.get("operation_kind")
            if operation not in _SUFFIX_OPERATIONS:
                continue
            command = runtime._publication(entry)
            if (
                command.operation_kind != operation
                or entry.get("operation_id") != command.idempotency_key
                or entry.get("expected_head") != command.expected_head
                or entry.get("fingerprint") != command.request_fingerprint
            ):
                return False
            if operation == "agent_loop.original-resolver-invocation.v1":
                from chiplog.composition import r14_original_resolver_invocation_publication as inv

                evidence = inv.RetainedOriginalResolverInvocationPublication.model_validate_json(
                    entry.get(inv.EVIDENCE_FIELD)
                )
                if inv.command(evidence) != command:
                    return False
                inv._validate_invocation_actor(runtime, evidence)
            else:
                from chiplog.composition import (
                    r14_original_call_resolution_publication as resolution,
                )

                resolution.validate_selected_command(runtime, entry, command)
            entries.append((entry, command))

        with read_connection(runtime._database) as source:
            if actual != capture_authority_snapshot_commitment(source, runtime._tenant_id):
                return False
            head_row = source.execute(
                "SELECT head FROM tenant_heads WHERE tenant_id=?", (runtime._tenant_id,)
            ).fetchone()
            if head_row is None:
                return False
            current_head = int(head_row[0])
            current_commitment = actual
            visited = {actual}
            with closing(sqlite3.connect(":memory:")) as historical:
                source.backup(historical)
                while current_commitment != target_commitment:
                    matches = [
                        (entry, command)
                        for entry, command in entries
                        if entry.get("resulting") == current_commitment
                        and command.expected_head + 1 == current_head
                    ]
                    if len(matches) != 1:
                        return False
                    entry, command = matches[0]
                    if inspect_publication(source, command, current_head) != "COMPLETE":
                        return False
                    predecessor = entry.get("predecessor")
                    if not isinstance(predecessor, str):
                        return False
                    historical.execute("BEGIN")
                    for record in command.records:
                        if historical.execute(
                            "DELETE FROM records WHERE tenant_id=? AND record_id=?",
                            (runtime._tenant_id, record.record_id),
                        ).rowcount != 1:
                            return False
                    if historical.execute(
                        "DELETE FROM publications WHERE tenant_id=? AND operation_kind=? "
                        "AND idempotency_key=?",
                        (runtime._tenant_id, command.operation_kind, command.idempotency_key),
                    ).rowcount != 1:
                        return False
                    historical.execute(
                        "UPDATE tenant_heads SET head=? WHERE tenant_id=?",
                        (command.expected_head, runtime._tenant_id),
                    )
                    if (
                        capture_authority_snapshot_commitment(historical, runtime._tenant_id)
                        != predecessor
                    ):
                        return False
                    current_commitment = predecessor
                    current_head = command.expected_head
                    visited.add(predecessor)
                    historical.commit()
        runtime._check_database_identity()
        return current_commitment == target_commitment and anchored in visited
    except (ValueError, TypeError, RuntimeError, OSError, sqlite3.Error):
        return False
