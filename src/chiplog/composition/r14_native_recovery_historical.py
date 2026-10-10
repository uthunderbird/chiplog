"""Reconstruct the native C cut and exact atomic C→C' bridge from storage.

The temporary SQLite copy is private, read-only with respect to the live database;
its hash proves the historical prefix and predicted exact publication delta.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.capabilities.agent_loop.native_recovery_profile_contracts import (
    SCHEMA,
    SCHEMA_V2,
    SCHEMA_V3,
    decode_native_one_call_recovery_profile,
    decode_native_one_call_recovery_profile_v2,
    decode_native_one_call_recovery_profile_v3,
)
from chiplog.composition.r14_execution_cancellation_contracts import (
    decode_execution_cancellation_preparation,
)
from chiplog.composition.r14_execution_fanout_contracts import RetainedExecutionFanOutPreparation
from chiplog.composition.r14_native_accounting_history import decode_selected_native_accounting
from chiplog.composition.r14_native_accounting_records import NATIVE_ACCOUNTING_OPERATION
from chiplog.composition.r14_native_recovery_source_subset import R14NativeRecoverySourceSubset
from chiplog.composition.r14_native_selected_accounting_source import R14NativeOneCallCancelledFacts
from chiplog.platform._owner_publication_contracts import OwnerRecordBytes
from chiplog.platform._sqlite import PhysicalRecord
from chiplog.platform.authority_reads import capture_authority_snapshot_commitment
from chiplog.platform.workspace_snapshot import read_connection

if TYPE_CHECKING:
    from .common_cli_execution_runtime import CommonCliExecutionRuntime
    from .r14_native_recovery_cut import NativeRecoveryCutSource
    from .r14_recovery_publication import RetainedRecoveryPublication


def _authenticate_v3_accepted_cut(
    runtime: CommonCliExecutionRuntime,
    evidence: RetainedRecoveryPublication,
    cut,
    rows,
    members,
) -> bool:
    """Revalidate the selected accepted cut's exact source carriers and owner join."""
    from chiplog.capabilities.agent_loop.execution_recovery_contracts import (
        PreparedExecutionAccounting,
        PreparedExecutionContinuation,
    )
    from chiplog.capabilities.agent_loop.execution_recovery_preparation import (
        prepare_execution_accounting,
        prepare_execution_continuation,
    )
    from chiplog.capabilities.agent_loop.native_recovery_cut_validation import (
        validate_native_recovery_cut,
    )
    from chiplog.composition.r14_native_scope_binding import bind_native_prepare_scope
    from chiplog.composition.r17_native_one_call_scope import NativeOneCallScopeService

    try:
        if not cut.original_closures or not cut.current_reductions:
            return False
        validate_native_recovery_cut(cut)
        rows_by_id = {row[0]: row for row in rows}
        if len(rows_by_id) != len(rows):
            return False
        carriers = {}
        for item in cut.complete_sources:
            record_id = item.physical_record.subject_id
            row = rows_by_id.get(record_id)
            if row is None:
                return False
            selected_record, selected_decision = members[record_id]
            if (
                item.owner != row[1]
                or item.physical_record.revision.fingerprint
                != hashlib.sha256(row[3]).hexdigest()
                or item.selected_decision != selected_decision
                or item.subject.revision.fingerprint
                != hashlib.sha256(item.canonical_record_bytes).hexdigest()
                or selected_record.owner != row[1]
                or (
                    selected_record.owner,
                    selected_record.schema_id,
                    selected_record.canonical_bytes,
                )
                != (row[1], row[2], row[3])
            ):
                return False
            carriers.setdefault(record_id, []).append(item)
        if set(carriers) != set(rows_by_id):
            return False
        if any(
            not any(
                item.schema_id == row[2]
                and item.canonical_record_bytes == row[3]
                and item.subject.revision.fingerprint == hashlib.sha256(row[3]).hexdigest()
                for item in carriers[record_id]
            )
            for record_id, row in rows_by_id.items()
        ):
            return False

        scope_sources = [
            item
            for item in cut.complete_sources
            if item.subject.revision == cut.current_bindings.authority_scope
        ]
        if len(scope_sources) != 1:
            return False
        scope_source = scope_sources[0]
        service = NativeOneCallScopeService(runtime)
        lineage = service._lineage()
        selected = [
            item
            for item in lineage
            if item.scope.canonical_bytes() == scope_source.canonical_record_bytes
        ]
        if len(selected) != 1:
            return False
        authenticated = service.read_historical(selected[0].anchor)
        if authenticated is None:
            return False
        if bind_native_prepare_scope(authenticated) != bind_native_prepare_scope(selected[0]):
            return False

        accounting = prepare_execution_accounting(evidence.accounting_request)
        continuation = prepare_execution_continuation(evidence.continuation_request)
        return (
            isinstance(accounting, PreparedExecutionAccounting)
            and isinstance(continuation, PreparedExecutionContinuation)
            and accounting == evidence.accounting_result
            and continuation == evidence.continuation_result
        )
    except (OSError, RuntimeError, TypeError, ValueError, KeyError):
        return False


def authenticate_native_recovery_selected(
    source: NativeRecoveryCutSource,
    runtime: CommonCliExecutionRuntime,
    evidence: RetainedRecoveryPublication,
    entry: dict[str, object],
    *,
    through_sequence: int | None = None,
) -> bool:
    """Authenticate retained original C without reopening recursive current history."""
    from chiplog.capabilities.agent_loop.execution_recovery_observations import RecoverySourceRecord
    from chiplog.composition.r14_native_scope_binding import bind_native_prepare_scope
    from chiplog.composition.r17_native_one_call_scope import NativeOneCallScopeService

    from .r14_native_recovery_cut import _ref, classify_native_occurrences
    from .r14_recovery_publication import command

    runtime._authority_gate().require_held()
    try:
        runtime._check_database_identity()
        actual_entries = [
            item
            for _, _, raw in runtime._loop_decisions().entries()
            if (item := json.loads(raw)).get("kind") == "DECIDED"
            and item.get("operation_id") == entry.get("operation_id")
        ]
        if len(actual_entries) != 1 or actual_entries[0] != entry:
            return False
        cut = evidence.accounting_request.cut
        batch = command(evidence)
        if (
            cut != evidence.continuation_request.cut
            or cut.tenant_id != runtime._tenant_id
            or cut.database_id != runtime._database_identity[0]
            or entry.get("predecessor") != cut.materialization_commitment
            or runtime._publication(entry) != batch
        ):
            return False
        selections = []
        with read_connection(runtime._database) as connection:
            for decision_id, _, raw in runtime._loop_decisions().entries():
                item = json.loads(raw)
                if item.get("kind") != "DECIDED":
                    continue
                selected = runtime._publication(item)
                if selected.expected_head < cut.tenant_commit_sequence:
                    selections.append((decision_id, raw, item, selected))
            rows = tuple(
                connection.execute(
                    "SELECT record_id,owner,schema_id,canonical_bytes,commit_sequence FROM records "
                    "WHERE tenant_id=? AND commit_sequence<=? ORDER BY commit_sequence,record_id",
                    (cut.tenant_id, cut.tenant_commit_sequence),
                )
            )
            members: dict[str, tuple[PhysicalRecord | OwnerRecordBytes, CallSubjectHead]] = {}
            for decision_id, raw, _item, selected in selections:
                for record in selected.records:
                    if record.record_id in members:
                        return False
                    members[record.record_id] = (
                        record,
                        _ref(selected.idempotency_key, raw, decision_id),
                    )
            for decision in runtime._owner_decisions().snapshot().decisions:
                raw = next(
                    raw
                    for key, _, raw in runtime._owner_decisions()._raw.entries()
                    if key == decision.decision_id
                )
                for owner_record in decision.prepared.request.complete_records:
                    row = next((row for row in rows if row[0] == owner_record.record_id), None)
                    if row is None:
                        continue
                    if owner_record.record_id in members:
                        return False
                    members[owner_record.record_id] = (
                        owner_record,
                        _ref(
                            decision.prepared.request.identity.command_id, raw, decision.decision_id
                        ),
                    )
            if set(members) != {row[0] for row in rows}:
                return False
            # Reconstruct C's materialization from an exact private copy. Future
            # mutable AMR changes that cannot be reconstructed cause HOLD.
            with closing(sqlite3.connect(":memory:")) as historical:
                connection.backup(historical)
                historical.execute(
                    "DELETE FROM records WHERE tenant_id=? AND commit_sequence>?",
                    (cut.tenant_id, cut.tenant_commit_sequence),
                )
                historical.execute(
                    "DELETE FROM publications WHERE tenant_id=? AND commit_sequence>?",
                    (cut.tenant_id, cut.tenant_commit_sequence),
                )
                historical.execute(
                    "UPDATE tenant_heads SET head=? WHERE tenant_id=?",
                    (cut.tenant_commit_sequence, cut.tenant_id),
                )
                if (
                    historical.execute(
                        "SELECT 1 FROM evidence_inbox WHERE tenant_id=? LIMIT 1", (cut.tenant_id,)
                    ).fetchone()
                    is not None
                ):
                    return False
                if (
                    capture_authority_snapshot_commitment(historical, cut.tenant_id)
                    != cut.materialization_commitment
                ):
                    return False
                sequence = cut.tenant_commit_sequence + 1
                for record in batch.records:
                    historical.execute(
                        "INSERT INTO records VALUES(?,?,?,?,?,?)",
                        (
                            cut.tenant_id,
                            record.record_id,
                            record.owner,
                            record.schema_id,
                            record.canonical_bytes,
                            sequence,
                        ),
                    )
                historical.execute(
                    "INSERT INTO publications VALUES(?,?,?,?,?,?)",
                    (
                        cut.tenant_id,
                        batch.operation_kind,
                        batch.idempotency_key,
                        batch.request_fingerprint,
                        sequence,
                        "\n".join(record.record_id for record in batch.records),
                    ),
                )
                historical.execute(
                    "UPDATE tenant_heads SET head=? WHERE tenant_id=?", (sequence, cut.tenant_id)
                )
                if capture_authority_snapshot_commitment(historical, cut.tenant_id) != entry.get(
                    "resulting"
                ):
                    return False
            v3_profiles = [
                row
                for row in rows
                if row[2] == "chiplog.execution.native-one-call-recovery-profile.v3"
            ]
            if v3_profiles and cut.original_closures:
                if len(v3_profiles) != 1:
                    return False
                return _authenticate_v3_accepted_cut(
                    runtime, evidence, cut, rows, members
                )
            accounting = [
                (item, selected)
                for _, _, item, selected in selections
                if selected.operation_kind == NATIVE_ACCOUNTING_OPERATION
            ]
            if len(accounting) != 1:
                return False
            fanouts = [
                RetainedExecutionFanOutPreparation.model_validate_json(item["execution_fanout"])
                for _, _, item, _ in selections
                if "execution_fanout" in item
            ]
            cancellations = [
                decode_execution_cancellation_preparation(
                    item["execution_cancellation_preparation"]
                )
                for _, _, item, _ in selections
                if "execution_cancellation_preparation" in item
            ]
            runs = [
                ExecutionRunRecord.model_validate_json(row[3])
                for row in rows
                if row[2] == "chiplog.agent-loop.execution-record.v2"
            ]
            from chiplog.capabilities.agent_loop.contracts import RunRecord

            history_records: list[RunRecord | ExecutionRunRecord] = list(runs)
            bounded = decode_selected_native_accounting(
                runtime,
                connection,
                accounting[0][1],
                accounting[0][0],
                fanouts,
                cancellations,
                history_records,
            )
            facts = TypeAdapter(R14NativeOneCallCancelledFacts).validate_json(
                bounded.selected_source_bytes
            )
            binding = facts.source.original_prepare.original_native_scope_binding
            if binding is None:
                return False
            authenticated = NativeOneCallScopeService(runtime).read_historical(binding.anchor)
            if bind_native_prepare_scope(authenticated) != binding:
                return False
            native_refs = {ref.revision.head: ref for ref in facts.source.target_run_lineage}
            from .r14_execution_fanout_records import reference

            terminal, result = cancellations[0].proposal.terminal, cancellations[0].proposal.result
            exact_refs = (
                facts.selected_seal.response_seal,
                facts.initialized,
                reference(terminal.terminal_id, terminal),
                reference(result.result_id, result),
            )
            native_refs.update({row.revision.head: row for row in exact_refs})
            physical = []
            for record_id, owner, schema, raw, _sequence in rows:
                selected_record, selected_decision = members[record_id]
                if (
                    selected_record.owner,
                    selected_record.schema_id,
                    selected_record.canonical_bytes,
                ) != (owner, schema, raw):
                    return False
                digest = hashlib.sha256(raw).hexdigest()
                subject = next(
                    (ref for ref in native_refs.values() if ref.revision.fingerprint == digest),
                    _ref(record_id, raw),
                )
                physical.append(
                    RecoverySourceRecord(
                        owner=owner,
                        schema_id=schema,
                        subject=subject,
                        canonical_record_bytes=raw,
                        selected_decision=selected_decision,
                        physical_record=_ref(record_id, raw),
                    )
                )
            profiles = [
                row for row in physical if row.schema_id in (SCHEMA, SCHEMA_V2, SCHEMA_V3)
            ]
            if len(profiles) != 1 or len(fanouts) != 1 or len(cancellations) != 1:
                return False
            profile = (
                decode_native_one_call_recovery_profile(profiles[0].canonical_record_bytes)
                if profiles[0].schema_id == SCHEMA
                else decode_native_one_call_recovery_profile_v3(
                    profiles[0].canonical_record_bytes
                )
                if profiles[0].schema_id == SCHEMA_V3
                else decode_native_one_call_recovery_profile_v2(
                    profiles[0].canonical_record_bytes
                )
            )
            run = runs[-1]
            if (
                any(row.run_id != authenticated.scope.planned_run_id for row in runs)
                or tuple(_ref(row.run_id, row.canonical_bytes(), row.head) for row in runs)
                != facts.source.target_run_lineage
                or run.state != "ACTIVE"
                or run.root_binding != "NOT_APPLICABLE"
                or run.worker_session != authenticated.scope.worker_session_id
                or run.suspension_baseline is not None
                or run.original_obligations
                or run.no_retry_references
                or run.delivery_acceptance is not None
                or len(run.turns) != 1
                or run.turns[0].ordinal != 1
            ):
                return False
            from chiplog.platform.ingress_authenticated_contracts import AuthenticatedCustodyCommand
            from chiplog.platform.ingress_custody_records import CustodyCommand
            from chiplog.platform.owner_decision_journal import snapshot_from_captured_entries
            from chiplog.platform.owner_publications import source_commands

            from .h1_inventory_leaf_contracts import H1RawInventoryItem
            from .h1_inventory_owner_cut import (
                OwnerJournalSqlPublication,
                OwnerJournalSqlRecord,
                reconcile_h1_owner_cut,
            )
            from .h1_owner_inventory import _PhysicalRow
            from .r16_native_inventory import _physical_occurrence

            occurrences = list(
                _physical_occurrence(_PhysicalRow(*row), cut.tenant_id) for row in rows
            )
            owner_journal = runtime._owner_decisions()
            owner_entries = tuple(owner_journal._raw.entries())
            captured_owner = snapshot_from_captured_entries(owner_entries, cut.tenant_id)
            if captured_owner != owner_journal.snapshot():
                return False
            owner_replay_entries = owner_entries
            if through_sequence is not None:
                # Reconcile the historical SQL prefix against an independently
                # scanned journal prefix, including each MATERIALIZED marker.
                # The full journal is authenticated first; future decisions may
                # not be silently dropped from the selected C' proof.
                boundary = len(owner_entries)
                for index in range(1, len(owner_entries) + 1):
                    candidate = snapshot_from_captured_entries(
                        owner_entries[:index], cut.tenant_id
                    )
                    if any(
                        decision.tenant_commit_sequence > through_sequence
                        for decision in candidate.decisions
                    ):
                        boundary = index - 1
                        break
                owner_replay_entries = owner_entries[:boundary]
                selected_prefix = snapshot_from_captured_entries(
                    owner_replay_entries, cut.tenant_id
                )
                expected_prefix = tuple(
                    decision
                    for decision in captured_owner.decisions
                    if decision.tenant_commit_sequence <= through_sequence
                )
                if (
                    selected_prefix.decisions != expected_prefix
                    or any(
                        decision.prepared.request.identity.command_id
                        not in selected_prefix.materialized_command_ids
                        for decision in expected_prefix
                    )
                ):
                    return False
            owner_cut = reconcile_h1_owner_cut(
                tenant=cut.tenant_id,
                raw_entries=owner_replay_entries,
                publications=tuple(
                    OwnerJournalSqlPublication(
                        operation, identity, fingerprint, sequence, tuple(ids.split("\n"))
                    )
                    for operation, identity, fingerprint, sequence, ids in connection.execute(
                        "SELECT operation_kind,idempotency_key,request_fingerprint,"
                        "commit_sequence,record_ids FROM publications WHERE tenant_id=? "
                        "AND commit_sequence<=? "
                        "ORDER BY commit_sequence,operation_kind,idempotency_key",
                        (cut.tenant_id, through_sequence or 2**63 - 1),
                    )
                ),
                records=tuple(
                    OwnerJournalSqlRecord(*row)
                    for row in connection.execute(
                        "SELECT record_id,owner,schema_id,canonical_bytes,commit_sequence "
                        "FROM records WHERE tenant_id=? AND commit_sequence<=? "
                        "ORDER BY commit_sequence,record_id",
                        (cut.tenant_id, through_sequence or 2**63 - 1),
                    )
                ),
                phase="PRE_SEAL",
            )
            if captured_owner != owner_journal.snapshot():
                return False
            owner_prefix = {
                decision.decision_id
                for decision in owner_cut.decisions
                if decision.tenant_commit_sequence <= cut.tenant_commit_sequence
            }
            if any(
                not decision.materialized
                for decision in owner_cut.decisions
                if decision.decision_id in owner_prefix
            ):
                return False
            for decision in captured_owner.decisions:
                if decision.decision_id not in owner_prefix:
                    continue
                for index, owner_command in enumerate(source_commands(decision.prepared.request)):
                    command_id = None
                    command_types: dict[
                        str, type[AuthenticatedCustodyCommand] | type[CustodyCommand]
                    ] = {
                        "chiplog.ingress.authenticated-command.v2": AuthenticatedCustodyCommand,
                        "chiplog.ingress.retained-command.v1": CustodyCommand,
                    }
                    kind = command_types.get(owner_command.schema_id)
                    if owner_command.owner == "broker_ingress" and kind is not None:
                        command_id = kind.model_validate_json(
                            owner_command.canonical_bytes
                        ).command_id
                    occurrences.append(
                        H1RawInventoryItem(
                            "OWNER_COMMAND",
                            f"selected/{decision.decision_id}/command/{index}",
                            cut.tenant_id,
                            owner_command.owner,
                            owner_command.schema_id,
                            None,
                            command_id,
                            owner_command.fingerprint,
                            owner_command.canonical_bytes,
                        )
                    )
            known_ids = {
                row.physical_record.subject_id
                for row in physical
                if row.subject in native_refs.values()
                or row.schema_id in (SCHEMA, SCHEMA_V2, SCHEMA_V3)
                or row.physical_record.subject_id == bounded.record.record_id
                or row.schema_id
                in {
                    "chiplog.execution.sealed-accounting.v1",
                    "chiplog.execution.continuation-ready.v1",
                }
            }
            if not classify_native_occurrences(
                occurrences,
                {},
                {
                    row.physical_record.subject_id: row
                    for row in physical
                    if row.physical_record.subject_id in known_ids
                },
                authenticated.scope,
            ):
                return False
            subset = R14NativeRecoverySourceSubset(
                cut.tenant_id,
                runtime._database_identity,
                cut.tenant_commit_sequence,
                cut.materialization_commitment,
                bounded.record,
                facts,
                (),
            )
            rebuilt = source._assemble(
                runtime,
                subset,
                profiles[0],
                profile,
                tuple(runs),
                fanouts[0].proposal.fan_out.response_seal,
                cancellations[0],
                tuple(physical),
                binding,
                authenticated.scope,
            )
            return rebuilt.canonical_bytes() == cut.canonical_bytes()
    except OSError, RuntimeError, TypeError, ValueError, KeyError, sqlite3.Error:
        return False
