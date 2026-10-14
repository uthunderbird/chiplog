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
        "agent_loop.accepted-open-accounting.v1",
        "agent_loop.accepted-open-recovery.v1",
        "agent_loop.native-one-call-cancelled-accounting.v1",
    }
)
_NATIVE_SUFFIX_OWNER_OPERATIONS = frozenset(
    {
        "effects.authorize",
        "effects.commit_send",
        "effects.record_evidence",
        "effects.reconcile",
        "dispatch.consume_effect_send",
    }
)

NATIVE_OPEN_PENDING_LIFECYCLE_OPERATIONS = frozenset(
    {
        "agent_loop.execution-suspension.v1",
        "agent_loop.execution-successor.v1",
    }
)


def validate_native_open_physical_source_prefix_held(
    runtime, connection: sqlite3.Connection, *, expected_head: int, expected_commitment: str
) -> None:
    """Prove an OPEN source stamp on the bounded append-only native path.

    The caller authenticates complete current membership and selected provenance.
    This check additionally proves the source commitment against actual prefix
    bytes. Other authority surfaces remain intact: an unsupported mutation holds.
    Only the private backup is changed, never the current physical cut.
    """
    runtime._authority_gate().require_held()
    runtime._check_database_identity()
    tenant = runtime._tenant_id
    before = capture_authority_snapshot_commitment(connection, tenant)
    head = connection.execute(
        "SELECT head FROM tenant_heads WHERE tenant_id=?", (tenant,)
    ).fetchone()
    if head is None or not 0 < expected_head <= head[0]:
        raise ValueError("OPEN source frontier is outside the current physical history")
    with closing(sqlite3.connect(":memory:")) as prefix:
        connection.backup(prefix)
        prefix.execute("BEGIN")
        prefix.execute(
            "DELETE FROM records WHERE tenant_id=? AND commit_sequence>?", (tenant, expected_head)
        )
        prefix.execute(
            "DELETE FROM publications WHERE tenant_id=? AND commit_sequence>?",
            (tenant, expected_head),
        )
        prefix.execute("UPDATE tenant_heads SET head=? WHERE tenant_id=?", (expected_head, tenant))
        if capture_authority_snapshot_commitment(prefix, tenant) != expected_commitment:
            raise ValueError("OPEN source commitment differs from the physical prefix")
    runtime._check_database_identity()
    if capture_authority_snapshot_commitment(connection, tenant) != before:
        raise ValueError("OPEN source physical acquisition changed during prefix validation")


def validate_native_open_selected_source_stamp_held(
    runtime, connection: sqlite3.Connection, *, command, entry, cut
) -> None:
    """Bind retained OPEN source metadata to its selected and physical prefix."""
    if (
        cut.tenant_id != runtime._tenant_id
        or command.tenant_id != runtime._tenant_id
        or cut.tenant_commit_sequence != command.expected_head
        or cut.materialization_commitment != entry.get("predecessor")
    ):
        raise ValueError("OPEN source stamp differs from selected publication header")
    validate_native_open_physical_source_prefix_held(
        runtime,
        connection,
        expected_head=command.expected_head,
        expected_commitment=cut.materialization_commitment,
    )


def validate_native_open_causal_source_prefix(
    runtime, cut, *, snapshot, fanouts, acceptances, effects_cut, verified_effects
) -> None:
    """Join an OPEN cut to already authenticated loop/effects source prefixes.

    The outer held reader owns physical/provenance verification. This function
    neither reacquires history nor substitutes a semantic prefix for the full
    current physical cut. Later evidence may not supply an earlier OPEN cause.
    """
    import hashlib

    from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
    from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
    from chiplog.capabilities.agent_loop.recovery_contracts import Present
    from chiplog.capabilities.effects.dispatch_outcome_contracts import DispatchOutcomeRecordV2
    from chiplog.composition.r14_execution_fanout_records import reference
    from chiplog.composition.r14_native_selected_accounting_source import (
        _open_outcome_join_is_valid,
    )

    if (
        cut.tenant_id != runtime._tenant_id
        or cut.database_id != runtime._database_identity[0]
        or effects_cut.tenant_id != cut.tenant_id
        or snapshot.tenant_head != cut.tenant_commit_sequence
    ):
        raise ValueError("OPEN causal source tenant, database or frontier differs")
    runs = tuple(
        record for record in snapshot.records if record.run_id == cut.current_run.subject_id
    )
    if not runs or not isinstance(runs[-1], ExecutionRunRecord):
        raise ValueError("OPEN causal source Run is absent")
    run = runs[-1]
    if (
        run.tenant != cut.tenant_id
        or run.state != "ACTIVE"
        or cut.current_run
        != CallSubjectHead(
            subject_id=run.run_id, revision=Present(head=run.head, fingerprint=run.digest())
        )
        or cut.current_run_bytes != run.canonical_bytes()
    ):
        raise ValueError("OPEN causal source Run differs from its prefix")
    selected = tuple(
        value
        for value in acceptances
        if value.loop_request.binding.original_call_id == cut.original_call_id
    )
    members = tuple(
        (value.proposal.fan_out.response_seal, record)
        for value in fanouts
        for record in value.proposal.fan_out.initialized_records
        if record.original_call_id == cut.original_call_id
    )
    if len(selected) != 1 or len(members) != 1:
        raise ValueError("OPEN causal source acceptance or seal is ambiguous")
    acceptance = selected[0]
    seal, initialized = members[0]
    binding, proposal = acceptance.loop_request.binding, acceptance.loop_proposal
    initialized_ref = reference(initialized.original_call_id, initialized)
    manifest = tuple(item.revision for item in proposal.complete_acceptance_manifest)
    if (
        seal.response_seal_id != cut.response_id
        or seal.original_run_id != run.run_id
        or seal.original_turn_id != initialized.call.original.original_turn_id
        or initialized.call.original.tenant_id != cut.tenant_id
        or initialized.call.original.original_run_id != run.run_id
        or acceptance.loop_request.initialized_record != initialized
        or binding.initialized != initialized_ref
        or cut.initialized != initialized_ref
        or cut.accepted != reference(proposal.accepted.accepted_id, proposal.accepted)
        or cut.accepted_record_bytes != proposal.accepted.canonical_bytes()
        or cut.execution_intent
        != reference(proposal.execution_intent.execution_intent_id, proposal.execution_intent)
        or cut.execution_intent_record_bytes != proposal.execution_intent.canonical_bytes()
        or cut.external_effect_intent != proposal.execution_intent.external_intent
        or manifest
        != (
            cut.accepted.revision,
            cut.execution_intent.revision,
            cut.external_effect_intent.revision,
        )
    ):
        raise ValueError("OPEN causal source call, seal or acceptance differs")

    rows = {row.record.record_id: row for row in effects_cut.rows}
    records = {record.record.head: record for record in verified_effects.v2_records}
    row = rows.get(cut.selected_outcome_record_id)
    outcome = records.get(cut.selected_outcome_record_id)
    if (
        row is None
        or not isinstance(outcome, DispatchOutcomeRecordV2)
        or row.commit_sequence > cut.tenant_commit_sequence
    ):
        raise ValueError("OPEN causal source outcome is absent from its prefix")
    matching_members = tuple(
        member
        for member in verified_effects.members
        if member.record_bytes == outcome.canonical_bytes()
    )
    if len(matching_members) != 1:
        raise ValueError("OPEN causal source outcome selected member is ambiguous")
    member = matching_members[0]
    decision = member.selected_decision
    if (
        outcome.snapshot.intent != acceptance.effects_proposal.snapshot.intent
        or cut.selected_outcome_bytes != outcome.canonical_bytes()
        or row.record.canonical_bytes != cut.selected_outcome_bytes
        or cut.selected_outcome_fingerprint
        != hashlib.sha256(cut.selected_outcome_bytes).hexdigest()
        or cut.outcome_commit_sequence != row.commit_sequence
        or cut.publication_ordinal != row.publication_ordinal
        or cut.batch_record_ids != row.batch_record_ids
        or cut.selected_decision
        != CallSubjectHead(
            subject_id=decision.subject_id,
            revision=Present(head=decision.head, fingerprint=decision.fingerprint),
        )
    ):
        raise ValueError("OPEN causal source outcome locator or effect differs")
    intent_id = outcome.snapshot.intent.intent_id
    prefix = tuple(
        record
        for record in verified_effects.v2_records
        if record.snapshot.intent.intent_id == intent_id
        and record.record.head in rows
        and rows[record.record.head].commit_sequence <= cut.tenant_commit_sequence
    )
    if not prefix:
        raise ValueError("OPEN causal source intent history is absent")
    latest = prefix[-1]
    latest_members = tuple(
        item for item in verified_effects.members if item.record_bytes == latest.canonical_bytes()
    )
    if (
        len(latest_members) != 1
        or _open_outcome_join_is_valid(
            intent_id,
            (outcome, member, row),
            (latest, latest_members[0], rows[latest.record.head]),
        )
        is None
    ):
        raise ValueError("OPEN causal source has no admissible OPEN outcome at its prefix")


def native_open_physical_execution_history_held(
    runtime,
    connection: sqlite3.Connection,
    *,
    snapshot,
    selected,
    expected_ids,
    expected_publications,
):
    """Separate authenticated selected recovery state from exact physical Runs.

    Only an independently pending, wholly absent batch may be omitted from the
    physical cut. The caller retains its original selected snapshot for replay.
    """
    from chiplog.adapters.driven.loop_sqlite import OWNER, SCHEMA
    from chiplog.capabilities.agent_loop.contracts import RunRecord
    from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
    from chiplog.capabilities.agent_loop.execution_history_contracts import ExecutionRunRecordV3
    from chiplog.capabilities.agent_loop.execution_run_record_contracts import (
        EXECUTION_RUN_V2_SCHEMA,
        EXECUTION_RUN_V3_SCHEMA,
    )
    from chiplog.composition.r14_execution_transition_records import ExecutionHistorySnapshot

    runtime._authority_gate().require_held()
    physical_head = connection.execute(
        "SELECT head FROM tenant_heads WHERE tenant_id=?", (runtime._tenant_id,)
    ).fetchone()
    if physical_head is None or snapshot.tenant_head != physical_head[0]:
        raise ValueError("OPEN physical history frontier differs from the current cut")
    pending = {
        command.idempotency_key: command
        for entry in runtime._pending()
        for command in (runtime._publication(entry),)
    }
    expected_ids, expected_publications = set(expected_ids), set(expected_publications)
    for command, entry in selected:
        state = inspect_publication(connection, command, command.expected_head + 1)
        if state == "COMPLETE":
            continue
        if state != "ABSENT" or pending.get(command.idempotency_key) != command:
            raise ValueError("OPEN physical history has an unverified absent or conflicting batch")
        actual = capture_authority_snapshot_commitment(connection, runtime._tenant_id)
        if (
            command.expected_head != physical_head[0]
            or entry.get("predecessor") != actual
            or runtime._commitment_journal.load(runtime._tenant_id) != actual
        ):
            raise ValueError(
                "OPEN physical pending source differs from the current anchored frontier"
            )
        expected_ids.difference_update(
            row.record_id for row in command.records if row.owner == OWNER
        )
        expected_publications.discard((command.operation_kind, command.idempotency_key))
    tenant = runtime._tenant_id
    physical_ids = {
        row[0]
        for row in connection.execute(
            "SELECT record_id FROM records WHERE tenant_id=? AND owner=?", (tenant, OWNER)
        )
    }
    physical_publications = set(
        connection.execute(
            "SELECT operation_kind, idempotency_key FROM publications WHERE tenant_id=? "
            "AND (substr(operation_kind, 1, 10)='agent_loop' "
            "OR operation_kind='effects.accept_call')",
            (tenant,),
        )
    )
    if physical_ids != expected_ids or physical_publications != expected_publications:
        raise ValueError("OPEN physical history has orphaned, missing or unknown loop members")
    schemas = {
        RunRecord: SCHEMA,
        ExecutionRunRecord: EXECUTION_RUN_V2_SCHEMA,
        ExecutionRunRecordV3: EXECUTION_RUN_V3_SCHEMA,
    }
    physical_runs = {
        record_id: (schema, raw)
        for record_id, schema, raw in connection.execute(
            "SELECT record_id, schema_id, canonical_bytes FROM records WHERE tenant_id=? "
            "AND owner=? AND schema_id IN (?, ?, ?)",
            (tenant, OWNER, SCHEMA, EXECUTION_RUN_V2_SCHEMA, EXECUTION_RUN_V3_SCHEMA),
        )
    }
    records = tuple(record for record in snapshot.records if record.head in physical_runs)
    if physical_runs != {
        record.head: (schemas[type(record)], record.canonical_bytes()) for record in records
    }:
        raise ValueError("OPEN physical Run history differs from authenticated selected bytes")
    return ExecutionHistorySnapshot(tenant_head=snapshot.tenant_head, records=records)


def authenticate_native_open_pending_lifecycle_held(
    runtime, connection, actual, anchored, *, pending_publication, selected, frontier
):
    """Prove the exact final lifecycle batch after its outer semantic decoding."""
    runtime._authority_gate().require_held()
    if (
        pending_publication.get("operation_kind") not in NATIVE_OPEN_PENDING_LIFECYCLE_OPERATIONS
        or runtime._pending() != (pending_publication,)
        or runtime._pending_owners()
        or runtime._pending_gate_publications()
    ):
        return False
    matches = tuple((command, entry) for command, entry in selected if entry == pending_publication)
    if len(matches) != 1:
        return False
    command, entry = matches[0]
    sequence = command.expected_head + 1
    if (
        sequence != frontier
        or entry.get("predecessor") != anchored
        or entry.get("resulting") != actual
        or capture_authority_snapshot_commitment(connection, runtime._tenant_id) != actual
        or runtime._commitment_journal.load(runtime._tenant_id) != anchored
        or inspect_publication(connection, command, sequence) != "COMPLETE"
    ):
        return False
    publications = connection.execute(
        "SELECT operation_kind,idempotency_key FROM publications "
        "WHERE tenant_id=? AND commit_sequence=?",
        (runtime._tenant_id, sequence),
    ).fetchall()
    rows = connection.execute(
        "SELECT record_id,owner,schema_id,canonical_bytes FROM records "
        "WHERE tenant_id=? AND commit_sequence=? ORDER BY rowid",
        (runtime._tenant_id, sequence),
    ).fetchall()
    if publications != [(command.operation_kind, command.idempotency_key)] or rows != [
        (row.record_id, row.owner, row.schema_id, row.canonical_bytes) for row in command.records
    ]:
        return False
    validate_native_open_physical_source_prefix_held(
        runtime,
        connection,
        expected_head=command.expected_head,
        expected_commitment=anchored,
    )
    return True


def validate_native_open_sources_held(
    runtime,
    connection: sqlite3.Connection,
    *,
    sources,
    snapshot,
    selected,
    expected_ids,
    expected_publications,
    acceptances,
    pending_publication=None,
    authenticated_source_prefix=None,
) -> None:
    """Validate captured OPEN causes against one complete current effects read."""
    from chiplog.composition.r14_accepted_direct_terminal_records import (
        retained_accepted_direct_terminal,
    )
    from chiplog.composition.r16_dispatch_history import verify_dispatch_history
    from chiplog.composition.r16_effects import _read_materialized_effects_with_history

    physical_history = native_open_physical_execution_history_held(
        runtime,
        connection,
        snapshot=snapshot,
        selected=selected,
        expected_ids=expected_ids,
        expected_publications=expected_publications,
    )
    physical_sequences = dict(
        connection.execute(
            "SELECT record_id, commit_sequence FROM records WHERE tenant_id=? "
            "AND owner='agent_loop'",
            (runtime._tenant_id,),
        )
    )
    for _command, _entry, cut, prefix, _fanouts, _acceptances in sources:
        if any(
            record not in physical_history.records
            or physical_sequences[record.head] > cut.tenant_commit_sequence
            for record in prefix.records
        ):
            raise ValueError("OPEN causal source contains an unmaterialized or future Run")

    native_anchor = None

    def authenticate_suffix(runtime, actual, anchored):
        nonlocal native_anchor
        if authenticated_source_prefix == (actual, anchored):
            return True
        if pending_publication is not None and authenticate_native_open_pending_lifecycle_held(
            runtime,
            connection,
            actual,
            anchored,
            pending_publication=pending_publication,
            selected=selected,
            frontier=snapshot.tenant_head,
        ):
            return True
        # A completed OPEN may be awaiting its independent anchor/finish marker.
        # Its source stamp was already proved against the actual physical prefix.
        for command, entry, _cut, _prefix, _fanouts, _acceptances in sources:
            sequence = command.expected_head + 1
            if (
                sequence != snapshot.tenant_head
                or entry.get("predecessor") != anchored
                or entry.get("resulting") != actual
            ):
                continue
            publications = connection.execute(
                "SELECT operation_kind, idempotency_key FROM publications "
                "WHERE tenant_id=? AND commit_sequence=?",
                (runtime._tenant_id, sequence),
            ).fetchall()
            rows = connection.execute(
                "SELECT record_id, owner, schema_id, canonical_bytes FROM records "
                "WHERE tenant_id=? AND commit_sequence=? ORDER BY rowid",
                (runtime._tenant_id, sequence),
            ).fetchall()
            if (
                publications == [(command.operation_kind, command.idempotency_key)]
                and rows
                == [
                    (row.record_id, row.owner, row.schema_id, row.canonical_bytes)
                    for row in command.records
                ]
                and inspect_publication(connection, command, sequence) == "COMPLETE"
            ):
                return True
        for command, entry in selected:
            if command.operation_kind != "agent_loop.accepted-direct-terminal.v1":
                continue
            evidence = retained_accepted_direct_terminal(entry)
            call_id = evidence.result.accounting.original_call_id
            if authenticate_selected_r14_native_terminal_suffix_held(
                runtime, actual, anchored, original_call_id=call_id
            ):
                native_anchor = (call_id, evidence.request.cut.current_run.subject_id)
                return True
        return False

    effects_cut, journal = _read_materialized_effects_with_history(
        runtime,
        runtime._owner_decisions(),
        authenticate_later_suffix=authenticate_suffix,
        authenticated_execution_history=physical_history,
    )
    if effects_cut.materialization_commitment != capture_authority_snapshot_commitment(
        connection, runtime._tenant_id
    ):
        raise ValueError("OPEN current effects and loop physical cuts differ")
    verified = verify_dispatch_history(effects_cut, journal)
    if native_anchor is not None:
        call_id, run_id = native_anchor
        allowed_intents = frozenset(
            value.effects_proposal.snapshot.intent.intent_id
            for value in acceptances
            if value.loop_request.binding.original.original_run_id == run_id
        )
        validate_native_terminal_suffix_semantics(
            runtime, journal, call_id, allowed_intent_ids=allowed_intents
        )
    for _command, _entry, cut, prefix, fanouts, source_acceptances in sources:
        validate_native_open_causal_source_prefix(
            runtime,
            cut,
            snapshot=prefix,
            fanouts=fanouts,
            acceptances=source_acceptances,
            effects_cut=effects_cut,
            verified_effects=verified,
        )


def _validate_native_suffix_loop_command(runtime, entry, command) -> None:
    """Bind native suffix bytes to their retained record without replaying history."""
    operation = command.operation_kind
    source_commitment = None
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
    elif operation == "agent_loop.accepted-open-accounting.v1":
        from chiplog.composition.r14_accepted_open_accounting_records import (
            accepted_open_accounting_command,
            retained_accepted_open_accounting,
        )

        evidence = retained_accepted_open_accounting(entry)
        expected = accepted_open_accounting_command(evidence)
        source_commitment = evidence.request.cut.materialization_commitment
    elif operation == "agent_loop.accepted-open-recovery.v1":
        from chiplog.composition.r14_accepted_open_accounting_records import (
            accepted_open_recovery_command,
            retained_accepted_open_recovery,
        )

        evidence = retained_accepted_open_recovery(entry)
        expected = accepted_open_recovery_command(evidence)
        source_commitment = evidence.request.cut.materialization_commitment
    elif operation == "agent_loop.native-one-call-cancelled-accounting.v1":
        from pydantic import TypeAdapter

        from chiplog.composition.r14_native_accounting_history import (
            retained_selected_native_accounting,
        )
        from chiplog.composition.r14_native_accounting_records import (
            _native_one_call_accounting_artifacts,
        )
        from chiplog.composition.r14_native_execution_record_source import (
            R14NativeExecutionRecordSourceManifest,
        )

        evidence = retained_selected_native_accounting(entry)
        source = TypeAdapter(R14NativeExecutionRecordSourceManifest).validate_json(
            evidence.physical_source_bytes
        )
        expected, envelope = _native_one_call_accounting_artifacts(evidence, source)
        if entry.get("native_one_call_accounting_envelope") != envelope.canonical_bytes().decode():
            raise ValueError("native suffix cancellation accounting envelope differs")
        source_commitment = source.source_stamp.authority_materialization_commitment
    else:
        raise ValueError("native suffix contains an unregistered loop operation")
    if (
        command != expected
        or entry.get("operation_id") != command.idempotency_key
        or entry.get("operation_kind") != operation
        or entry.get("fingerprint") != command.request_fingerprint
        or entry.get("expected_head") != command.expected_head
        or (source_commitment is not None and entry.get("predecessor") != source_commitment)
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
        if any(
            decision.tenant_commit_sequence > target_sequence
            and decision.prepared.request.operation == "dispatch.consume_effect_send"
            for decision in owner_snapshot.decisions
        ):
            from chiplog.composition.r16_dispatch_outbox import validate_consumptions

            validate_consumptions(runtime, owner_snapshot)
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
                if current_commitment != str(target_entry.get("resulting")):
                    return False
                historical.execute("BEGIN")
                for record in target_command.records:
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
                        (
                            runtime._tenant_id,
                            target_command.operation_kind,
                            target_command.idempotency_key,
                        ),
                    ).rowcount
                    != 1
                ):
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
    except ValueError, TypeError, RuntimeError, OSError, sqlite3.Error, KeyError:
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

    if any(
        decision.tenant_commit_sequence > terminal_sequence
        and decision.prepared.request.operation == "dispatch.consume_effect_send"
        for decision in journal.decisions
    ):
        from chiplog.composition.r16_dispatch_outbox import validate_consumptions

        validate_consumptions(runtime, journal)
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
        elif batch.operation == "dispatch.consume_effect_send":
            from chiplog.composition.r16_dispatch_outbox import ConsumeRequest

            consumption = ConsumeRequest.model_validate_json(batch.command.canonical_bytes)
            if consumption.intent.subject_id not in allowed_intent_ids:
                raise ValueError("native terminal suffix consumption belongs to another seal")
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
