"""Authenticate a contiguous selected R14 publication suffix back to a cut."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing

from chiplog.platform.authority_reads import capture_authority_snapshot_commitment
from chiplog.platform.publication_readback import inspect_publication
from chiplog.platform.workspace_snapshot import read_connection

_SUFFIX_OPERATIONS = frozenset(
    {
        "agent_loop.original-resolver-authority-policy.v1",
        "agent_loop.original-resolver-invocation.v1",
        "agent_loop.original-call-resolution.v1",
        "agent_loop.loop-semantic-reduction.v1",
        "agent_loop.execution-accounting-readiness.v1",
        "agent_loop.execution-next-turn.v1",
        "agent_loop.execution-suspension.v1",
        "agent_loop.execution-resume.v1",
    }
)
_APPEND_EVIDENCE = "effects.record_evidence"
_NATIVE_SUFFIX_LOOP_OPERATIONS = frozenset(
    {
        "agent_loop.accepted-direct-terminal.v1",
        "agent_loop.native-sealed-accounting.v3",
    }
)
_NATIVE_SUFFIX_OWNER_OPERATIONS = frozenset(
    {
        "effects.authorize",
        "effects.commit_send",
        "effects.record_evidence",
        "effects.reconcile",
    }
)


def _validate_native_suffix_loop_command(runtime, entry, command) -> None:
    """Bind native suffix bytes to their retained record without replaying history."""
    operation = command.operation_kind
    if operation == "agent_loop.accepted-direct-terminal.v1":
        from chiplog.composition.r14_accepted_direct_terminal_records import (
            accepted_direct_terminal_command,
            retained_accepted_direct_terminal,
        )

        evidence = retained_accepted_direct_terminal(entry)
        expected = accepted_direct_terminal_command(evidence)
    elif operation == "agent_loop.native-sealed-accounting.v3":
        from chiplog.composition.r14_native_accounting_records import (
            NATIVE_SEALED_ACCOUNTING_V3_PUBLICATION_OPERATION,
            retained_selected_native_sealed_accounting_v3,
            sealed_accounting_v3_command,
            validate_retained_sealed_accounting_v3_publication,
        )

        if operation != NATIVE_SEALED_ACCOUNTING_V3_PUBLICATION_OPERATION:
            raise ValueError("native suffix V3 operation is unregistered")
        evidence = retained_selected_native_sealed_accounting_v3(entry)
        validate_retained_sealed_accounting_v3_publication(evidence)
        expected = sealed_accounting_v3_command(evidence)
    else:
        raise ValueError("native suffix contains an unregistered loop operation")
    if (
        command != expected
        or entry.get("operation_id") != command.idempotency_key
        or entry.get("operation_kind") != operation
        or entry.get("fingerprint") != command.request_fingerprint
        or entry.get("expected_head") != command.expected_head
    ):
        raise ValueError("native suffix selected loop command differs from retained evidence")


def authenticate_selected_r14_native_terminal_suffix_held(
    runtime, actual: str, anchored: str, *, original_call_id: str
) -> bool:
    """Authenticate a physical suffix after one selected native direct terminal.

    This proves exact selected envelopes and physical tenant history only. R16
    effect semantics are checked separately on the mounted effects cut.
    """
    runtime._authority_gate().require_held()
    try:
        runtime._check_database_identity()
        if runtime._pending_owners() or runtime._pending_gate_publications():
            return False
        from chiplog.composition.r14_accepted_direct_terminal_records import (
            retained_accepted_direct_terminal,
        )

        targets = []
        loop_entries = []
        for _, _, raw in runtime._loop_decisions().entries():
            entry = json.loads(raw)
            if entry.get("kind") != "DECIDED":
                continue
            operation = entry.get("operation_kind")
            command = runtime._publication(entry)
            if operation in _NATIVE_SUFFIX_LOOP_OPERATIONS:
                _validate_native_suffix_loop_command(runtime, entry, command)
                loop_entries.append((entry, command))
            if operation == "agent_loop.accepted-direct-terminal.v1":
                evidence = retained_accepted_direct_terminal(entry)
                if evidence.result.accounting.original_call_id == original_call_id:
                    targets.append((entry, command))
        if len(targets) != 1:
            return False
        target_entry, target_command = targets[0]
        target_sequence = target_command.expected_head + 1
        steps = {}
        for entry, command in loop_entries:
            sequence = command.expected_head + 1
            if sequence <= target_sequence:
                continue
            if sequence in steps:
                return False
            steps[sequence] = (
                command,
                str(entry.get("predecessor")),
                str(entry.get("resulting")),
            )

        owner_snapshot = runtime._owner_decisions().snapshot()
        for decision in owner_snapshot.decisions:
            sequence = decision.tenant_commit_sequence
            if sequence <= target_sequence:
                continue
            command = runtime._owner_command(decision)
            if command.operation_kind not in _NATIVE_SUFFIX_OWNER_OPERATIONS or sequence in steps:
                return False
            steps[sequence] = (
                command,
                decision.prepared.predecessor_commitment,
                decision.resulting_commitment,
            )

        with read_connection(runtime._database) as source:
            if capture_authority_snapshot_commitment(source, runtime._tenant_id) != actual:
                return False
            head_row = source.execute(
                "SELECT head FROM tenant_heads WHERE tenant_id=?", (runtime._tenant_id,)
            ).fetchone()
            if head_row is None:
                return False
            current_head = int(head_row[0])
            expected_sequences = tuple(range(target_sequence + 1, current_head + 1))
            if tuple(sorted(steps)) != expected_sequences:
                return False
            publications = tuple(
                source.execute(
                    "SELECT operation_kind,idempotency_key,request_fingerprint,"
                    "commit_sequence,record_ids FROM publications "
                    "WHERE tenant_id=? AND commit_sequence>? ORDER BY commit_sequence",
                    (runtime._tenant_id, target_sequence),
                ).fetchall()
            )
            if tuple(row[3] for row in publications) != expected_sequences:
                return False
            record_sequences = tuple(
                row[0]
                for row in source.execute(
                    "SELECT DISTINCT commit_sequence FROM records "
                    "WHERE tenant_id=? AND commit_sequence>? ORDER BY commit_sequence",
                    (runtime._tenant_id, target_sequence),
                ).fetchall()
            )
            if record_sequences != expected_sequences:
                return False

            # Authenticate the target publication too, then remove it from a
            # private snapshot so the resulting source commitment is checked.
            target_sequence_row = source.execute(
                "SELECT operation_kind,idempotency_key,request_fingerprint,commit_sequence,"
                "record_ids "
                "FROM publications WHERE tenant_id=? AND operation_kind=? AND idempotency_key=?",
                (runtime._tenant_id, target_command.operation_kind, target_command.idempotency_key),
            ).fetchone()
            if (
                target_sequence_row
                != (
                    target_command.operation_kind,
                    target_command.idempotency_key,
                    target_command.request_fingerprint,
                    target_sequence,
                    "\n".join(record.record_id for record in target_command.records),
                )
                or inspect_publication(source, target_command, target_sequence) != "COMPLETE"
            ):
                return False
            target_physical = tuple(
                source.execute(
                    "SELECT record_id,owner,schema_id,canonical_bytes FROM records "
                    "WHERE tenant_id=? AND commit_sequence=? ORDER BY rowid",
                    (runtime._tenant_id, target_sequence),
                ).fetchall()
            )
            if target_physical != tuple(
                (record.record_id, record.owner, record.schema_id, record.canonical_bytes)
                for record in target_command.records
            ):
                return False

            with closing(sqlite3.connect(":memory:")) as historical:
                source.backup(historical)
                current_commitment = actual
                latest_predecessor = None
                for sequence in reversed(expected_sequences):
                    command, predecessor, resulting = steps[sequence]
                    publication = publications[sequence - target_sequence - 1]
                    if (
                        resulting != current_commitment
                        or command.expected_head + 1 != sequence
                        or publication
                        != (
                            command.operation_kind,
                            command.idempotency_key,
                            command.request_fingerprint,
                            sequence,
                            "\n".join(record.record_id for record in command.records),
                        )
                        or inspect_publication(source, command, sequence) != "COMPLETE"
                    ):
                        return False
                    physical = tuple(
                        source.execute(
                            "SELECT record_id,owner,schema_id,canonical_bytes FROM records "
                            "WHERE tenant_id=? AND commit_sequence=? ORDER BY rowid",
                            (runtime._tenant_id, sequence),
                        ).fetchall()
                    )
                    if physical != tuple(
                        (record.record_id, record.owner, record.schema_id, record.canonical_bytes)
                        for record in command.records
                    ):
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
                    historical.commit()
                    current_commitment = predecessor
                    if sequence == expected_sequences[-1]:
                        latest_predecessor = predecessor
                if current_commitment != str(target_entry.get("resulting")):
                    return False
                historical.execute("BEGIN")
                for record in target_command.records:
                    if historical.execute(
                        "DELETE FROM records WHERE tenant_id=? AND record_id=?",
                        (runtime._tenant_id, record.record_id),
                    ).rowcount != 1:
                        return False
                if historical.execute(
                    "DELETE FROM publications WHERE tenant_id=? AND operation_kind=? "
                    "AND idempotency_key=?",
                    (
                        runtime._tenant_id,
                        target_command.operation_kind,
                        target_command.idempotency_key,
                    ),
                ).rowcount != 1:
                    return False
                historical.execute(
                    "UPDATE tenant_heads SET head=? WHERE tenant_id=?",
                    (target_command.expected_head, runtime._tenant_id),
                )
                target_predecessor = str(target_entry.get("predecessor"))
                if (
                    capture_authority_snapshot_commitment(historical, runtime._tenant_id)
                    != target_predecessor
                ):
                    return False
                historical.commit()
                current_commitment = target_predecessor

            runtime._check_database_identity()
            return (
                capture_authority_snapshot_commitment(source, runtime._tenant_id) == actual
                and runtime._commitment_journal.load(runtime._tenant_id) == anchored
                and anchored in (actual, latest_predecessor, target_predecessor)
            )
    except (ValueError, TypeError, RuntimeError, OSError, sqlite3.Error, KeyError):
        return False


def validate_native_terminal_suffix_semantics(
    runtime, journal, original_call_id: str, *, allowed_intent_ids: frozenset[str]
) -> None:
    """Validate R16 source/order semantics on an already mounted complete effects cut."""
    from chiplog.composition.r14_accepted_direct_terminal_records import (
        retained_accepted_direct_terminal,
    )
    from chiplog.composition.r16_dispatch_authority import validate_issuance
    from chiplog.composition.r16_dispatch_outcomes import validate_outcome_issuance

    targets = [
        runtime._publication(entry).expected_head + 1
        for _, _, raw in runtime._loop_decisions().entries()
        if (entry := json.loads(raw)).get("kind") == "DECIDED"
        and entry.get("operation_kind") == "agent_loop.accepted-direct-terminal.v1"
        and retained_accepted_direct_terminal(entry).result.accounting.original_call_id
        == original_call_id
    ]
    if len(targets) != 1:
        raise ValueError("native terminal suffix semantic anchor is ambiguous")
    terminal_sequence = targets[0]

    for decision in journal.decisions:
        if decision.tenant_commit_sequence <= terminal_sequence:
            continue
        batch = decision.prepared.request
        if batch.operation in ("effects.authorize", "effects.commit_send"):
            issuance = validate_issuance(batch, runtime)
            retained_intent = issuance.request.command.intent
            intent_id = getattr(retained_intent, "intent_id", None) or getattr(
                retained_intent, "subject_id", None
            )
            if intent_id not in allowed_intent_ids:
                raise ValueError("native terminal suffix dispatch belongs to another seal")
        elif batch.operation in ("effects.record_evidence", "effects.reconcile"):
            issuance = validate_outcome_issuance(batch, runtime)
            if issuance.request.original_send.snapshot.intent.intent_id not in allowed_intent_ids:
                raise ValueError("native terminal suffix outcome belongs to another seal")
        else:
            raise ValueError("native terminal suffix includes an unregistered R16 operation")


def _validate_loop_entry(runtime, entry, command) -> None:
    operation = command.operation_kind
    if operation == "agent_loop.original-resolver-authority-policy.v1":
        from chiplog.composition import r14_original_resolver_policy_publication as policy

        policy.validate_selected(runtime, entry, command)
    elif operation == "agent_loop.original-resolver-invocation.v1":
        from chiplog.composition import r14_original_resolver_invocation_publication as inv

        inv.validate_selected_for_historical_suffix(runtime, entry, command)
    elif operation == "agent_loop.original-call-resolution.v1":
        from chiplog.composition import r14_original_call_resolution_publication as resolution

        resolution.validate_selected_command(runtime, entry, command)
    elif operation == "agent_loop.loop-semantic-reduction.v1":
        from chiplog.composition import r14_loop_semantic_reduction_publication as reduction

        reduction.validate_selected_command(runtime, entry, command)
    elif operation == "agent_loop.execution-accounting-readiness.v1":
        from chiplog.composition.r14_recovery_publication import validate_selected

        validate_selected(runtime, entry, command, through_sequence=command.expected_head)
    elif operation == "agent_loop.execution-next-turn.v1":
        from chiplog.composition.r14_next_turn_records import validate_selected_next_turn

        validate_selected_next_turn(runtime, entry, command)
    elif operation == "agent_loop.execution-suspension.v1":
        from chiplog.composition import r14_execution_suspension as suspension

        suspension._validate_selected(runtime, entry, command, check_history=False)
    elif operation == "agent_loop.execution-resume.v1":
        from chiplog.composition import r14_execution_resume as resume

        evidence = resume._retained(entry)
        resume._validate_selected(runtime, entry, resume._command(evidence), check_history=False)
    else:
        raise ValueError("suffix contains an unregistered loop operation")


def authenticate_selected_r14_suffix_held(
    runtime, actual: str, anchored: str, *, target_commitment: str
) -> bool:
    """Verify an exact mixed owner/loop physical suffix after a selected R14 cut."""
    runtime._authority_gate().require_held()
    try:
        runtime._check_database_identity()
        if runtime._pending_owners() or runtime._pending_gate_publications():
            return False

        from chiplog.composition import r14_original_call_resolution_publication as resolution

        resolutions = resolution._selected_entries(runtime)
        if len(resolutions) > 1:
            return False
        resolution_sequence = None
        if resolutions:
            _, _, resolution_entry, resolution_command = resolutions[0]
            resolution.validate_selected_command(runtime, resolution_entry, resolution_command)
            resolution_sequence = resolution_command.expected_head + 1

        selected_entries = []
        for _, _, raw in runtime._loop_decisions().entries():
            entry = json.loads(raw)
            if (
                entry.get("kind") == "DECIDED"
                and entry.get("operation_kind") in _SUFFIX_OPERATIONS
                and entry.get("resulting") == target_commitment
            ):
                command = runtime._publication(entry)
                if (
                    entry.get("operation_id") != command.idempotency_key
                    or entry.get("operation_kind") != command.operation_kind
                    or entry.get("fingerprint") != command.request_fingerprint
                    or type(entry.get("expected_head")) is not int
                    or entry.get("expected_head") != command.expected_head
                ):
                    return False
                selected_entries.append((entry, command))
        if len(selected_entries) != 1:
            return False
        target_entry, target_command = selected_entries[0]
        _validate_loop_entry(runtime, target_entry, target_command)
        target_sequence = target_command.expected_head + 1

        reductions = runtime._loop_decisions().entries()
        has_reduction = any(
            json.loads(raw).get("kind") == "DECIDED"
            and json.loads(raw).get("operation_kind") == "agent_loop.loop-semantic-reduction.v1"
            for _, _, raw in reductions
        )
        if has_reduction:
            from chiplog.composition.r14_loop_semantic_reduction_publication import (
                validate_loop_semantic_reduction_startup,
            )

            validate_loop_semantic_reduction_startup(runtime)

        # Build one sequence-indexed view. The independent owner journal and loop
        # journal have no shared iteration order, so only persisted tenant sequence
        # determines the chain.
        steps = {}
        for _, _, raw in reductions:
            entry = json.loads(raw)
            if entry.get("kind") != "DECIDED":
                continue
            operation = entry.get("operation_kind")
            expected_head = entry.get("expected_head")
            if type(expected_head) is not int:
                return False
            sequence = expected_head + 1
            if sequence <= target_sequence:
                continue
            if operation not in _SUFFIX_OPERATIONS:
                return False
            command = runtime._publication(entry)
            if (
                entry.get("operation_id") != command.idempotency_key
                or entry.get("fingerprint") != command.request_fingerprint
                or command.operation_kind != operation
                or command.expected_head + 1 != sequence
            ):
                return False
            _validate_loop_entry(runtime, entry, command)
            if sequence in steps:
                return False
            steps[sequence] = (
                entry,
                command,
                str(entry.get("predecessor")),
                str(entry.get("resulting")),
            )

        owner_snapshot = runtime._owner_decisions().snapshot()
        for decision in owner_snapshot.decisions:
            sequence = decision.tenant_commit_sequence
            if sequence <= target_sequence:
                continue
            if not has_reduction or resolution_sequence is None or sequence <= resolution_sequence:
                return False
            command = runtime._owner_command(decision)
            if command.operation_kind != _APPEND_EVIDENCE or sequence in steps:
                return False
            steps[sequence] = (
                decision,
                command,
                decision.prepared.predecessor_commitment,
                decision.resulting_commitment,
            )

        with read_connection(runtime._database) as source:
            if capture_authority_snapshot_commitment(source, runtime._tenant_id) != actual:
                return False
            head_row = source.execute(
                "SELECT head FROM tenant_heads WHERE tenant_id=?", (runtime._tenant_id,)
            ).fetchone()
            if head_row is None:
                return False
            current_head = int(head_row[0])
            expected_sequences = tuple(range(target_sequence + 1, current_head + 1))
            if tuple(sorted(steps)) != expected_sequences:
                return False
            publications = tuple(
                source.execute(
                    "SELECT operation_kind,idempotency_key,request_fingerprint,"
                    "commit_sequence,record_ids FROM publications "
                    "WHERE tenant_id=? AND commit_sequence>? ORDER BY commit_sequence",
                    (runtime._tenant_id, target_sequence),
                ).fetchall()
            )
            if tuple(row[3] for row in publications) != expected_sequences:
                return False
            record_sequences = tuple(
                row[0]
                for row in source.execute(
                    "SELECT DISTINCT commit_sequence FROM records "
                    "WHERE tenant_id=? AND commit_sequence>? ORDER BY commit_sequence",
                    (runtime._tenant_id, target_sequence),
                ).fetchall()
            )
            if record_sequences != expected_sequences:
                return False

            with closing(sqlite3.connect(":memory:")) as historical:
                source.backup(historical)
                current_commitment = actual
                latest_predecessor = None
                for sequence in reversed(expected_sequences):
                    entry, command, predecessor, resulting = steps[sequence]
                    publication = publications[sequence - target_sequence - 1]
                    if (
                        resulting != current_commitment
                        or command.expected_head + 1 != sequence
                        or publication
                        != (
                            command.operation_kind,
                            command.idempotency_key,
                            command.request_fingerprint,
                            sequence,
                            "\n".join(record.record_id for record in command.records),
                        )
                        or inspect_publication(source, command, sequence) != "COMPLETE"
                    ):
                        return False
                    physical = tuple(
                        source.execute(
                            "SELECT record_id,owner,schema_id,canonical_bytes FROM records "
                            "WHERE tenant_id=? AND commit_sequence=? ORDER BY rowid",
                            (runtime._tenant_id, sequence),
                        ).fetchall()
                    )
                    expected_physical = tuple(
                        (record.record_id, record.owner, record.schema_id, record.canonical_bytes)
                        for record in command.records
                    )
                    if physical != expected_physical:
                        return False

                    historical.execute("BEGIN")
                    for record in command.records:
                        if (
                            historical.execute(
                                "DELETE FROM records WHERE tenant_id=? AND record_id=?",
                                (runtime._tenant_id, record.record_id),
                            ).rowcount
                            != 1
                        ):
                            return False
                    if (
                        historical.execute(
                            "DELETE FROM publications WHERE tenant_id=? AND operation_kind=? "
                            "AND idempotency_key=?",
                            (runtime._tenant_id, command.operation_kind, command.idempotency_key),
                        ).rowcount
                        != 1
                    ):
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
                    historical.commit()
                    current_commitment = predecessor
                    if sequence == expected_sequences[-1]:
                        latest_predecessor = predecessor

            runtime._check_database_identity()
            return (
                current_commitment == target_commitment
                and capture_authority_snapshot_commitment(source, runtime._tenant_id) == actual
                and runtime._commitment_journal.load(runtime._tenant_id) == anchored
                and anchored in (actual, latest_predecessor)
            )
    except ValueError, TypeError, RuntimeError, OSError, sqlite3.Error:
        return False


def authenticate_selected_r14_source_cut_held(
    runtime, actual: str, anchored: str, *, target_commitment: str
) -> bool:
    """Authenticate a pending resolution with its purpose-built prefix verifier first."""
    runtime._authority_gate().require_held()
    if actual == anchored:
        return False
    from chiplog.composition.r14_original_call_resolution_source import (
        authenticate_selected_resolution_prefix_held,
    )

    if authenticate_selected_resolution_prefix_held(runtime, actual, anchored):
        return True
    return authenticate_selected_r14_suffix_held(
        runtime, actual, anchored, target_commitment=target_commitment
    )
