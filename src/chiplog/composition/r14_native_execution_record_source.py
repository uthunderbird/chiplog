"""Read-only physical provenance for the bounded V24 native cancellation.

The semantic history is deliberately owned by ``read_execution_recovery_history``
through the existing selected-accounting collector.  This module only joins its
already-authenticated target values to the selected journal bytes and SQLite
publication rows; it does not replay execution history.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.native_one_call_accounting_contracts import (
    NativeOneCallAccountingSourceStampV2,
)
from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
from chiplog.composition.r14_cancellation_contracts import CANCELLATION_SCHEMA, NOT_EXECUTED_SCHEMA
from chiplog.composition.r14_execution_fanout_contracts import EXECUTION_RUN_SCHEMA
from chiplog.composition.r14_execution_fanout_records import reference
from chiplog.composition.r14_fanout_contracts import INITIALIZED_SCHEMA, SEAL_SCHEMA
from chiplog.composition.r14_native_selected_accounting_source import R14NativeOneCallCancelledFacts
from chiplog.platform._sqlite import PhysicalPublicationCommand
from chiplog.platform.publication_readback import inspect_publication
from chiplog.platform.workspace_snapshot import read_connection


@dataclass(frozen=True, slots=True)
class R14NativeExecutionRecordSourceMember:
    """One exact selected command member and its materialized SQLite row."""

    owner: str
    schema_id: str
    record_id: str
    canonical_record_bytes: bytes
    selected_decision_id: str
    selected_decision_bytes: bytes
    selected_decision_fingerprint: str
    selected_operation: str
    physical_commit_sequence: int


@dataclass(frozen=True, slots=True)
class R14NativeExecutionRecordSourceManifest:
    """Supported execution subset, never a claim of complete recovery sources."""

    tenant_id: str
    source_stamp: NativeOneCallAccountingSourceStampV2
    original_prepared_run: CallSubjectHead
    selected_response_seal: CallSubjectHead
    initialized: CallSubjectHead
    terminal: CallSubjectHead
    result: CallSubjectHead
    complete_ordered_run_lineage: tuple[CallSubjectHead, ...]
    ordered_members: tuple[R14NativeExecutionRecordSourceMember, ...]
    fingerprint: str


def _fingerprint(
    tenant_id: str,
    source_stamp: NativeOneCallAccountingSourceStampV2,
    prepared: CallSubjectHead,
    seal: CallSubjectHead,
    initialized: CallSubjectHead,
    terminal: CallSubjectHead,
    result: CallSubjectHead,
    lineage: tuple[CallSubjectHead, ...],
    members: tuple[R14NativeExecutionRecordSourceMember, ...],
) -> str:
    value = {
        "tenant_id": tenant_id,
        "source_stamp": source_stamp.model_dump(mode="json"),
        "original_prepared_run": prepared.model_dump(mode="json"),
        "selected_response_seal": seal.model_dump(mode="json"),
        "initialized": initialized.model_dump(mode="json"),
        "terminal": terminal.model_dump(mode="json"),
        "result": result.model_dump(mode="json"),
        "complete_ordered_run_lineage": [item.model_dump(mode="json") for item in lineage],
        "ordered_members": [
            {
                "owner": item.owner,
                "schema_id": item.schema_id,
                "record_id": item.record_id,
                "canonical_record_bytes": item.canonical_record_bytes.hex(),
                "selected_decision_id": item.selected_decision_id,
                "selected_decision_bytes": item.selected_decision_bytes.hex(),
                "selected_decision_fingerprint": item.selected_decision_fingerprint,
                "selected_operation": item.selected_operation,
                "physical_commit_sequence": item.physical_commit_sequence,
            }
            for item in members
        ],
    }
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


class _R14NativeExecutionRecordSource:
    """V24-private selected/physical join; its caller owns semantic reopening."""

    def __init__(self, runtime: CommonCliExecutionRuntime) -> None:
        if type(runtime) is not CommonCliExecutionRuntime:
            raise TypeError("native record source requires the installed CLI runtime")
        self._runtime = runtime

    def collect_held(
        self,
        selected: R14NativeOneCallCancelledFacts,
        source_stamp: NativeOneCallAccountingSourceStampV2,
    ) -> R14NativeExecutionRecordSourceManifest | None:
        if type(selected) is not R14NativeOneCallCancelledFacts:
            raise TypeError("native record source requires exact selected cancellation facts")
        if type(source_stamp) is not NativeOneCallAccountingSourceStampV2:
            raise TypeError("native record source requires an exact V24 source stamp")
        # V24 already reopens the selected-accounting source in this authority
        # hold and mounted workspace snapshot.  Replaying it here would turn
        # provenance collection into a second semantic history reader.
        try:
            self._runtime._authority_gate().require_held()
            self._runtime._check_database_identity()
            result = self._collect_held(selected, source_stamp)
            self._runtime._check_database_identity()
            return result
        except OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError:
            return None

    def _collect_held(
        self,
        selected: R14NativeOneCallCancelledFacts,
        source_stamp: NativeOneCallAccountingSourceStampV2,
    ) -> R14NativeExecutionRecordSourceManifest | None:
        source = selected.source
        cancellation = source.target_cancellations[0]
        terminal = cancellation.proposal.terminal
        result = cancellation.proposal.result
        terminal_ref = reference(terminal.terminal_id, terminal)
        result_ref = reference(result.result_id, result)
        run_members = [
            (EXECUTION_RUN_SCHEMA, ref.revision.head, None) for ref in source.target_run_lineage
        ]
        initialized_member = (
            INITIALIZED_SCHEMA,
            "record:" + selected.selected_seal.initialized_records[0].digest(),
            None,
        )
        seal_member = (
            SEAL_SCHEMA,
            "record:" + selected.selected_seal.response_seal.revision.fingerprint,
            None,
        )
        terminal_member = (CANCELLATION_SCHEMA, terminal.terminal_id, None)
        result_member = (NOT_EXECUTED_SCHEMA, result.result_id, None)
        expected = tuple(
            (*run_members, seal_member, initialized_member, terminal_member, result_member)
        )
        # A decision is the only authority that selects a command.  The history
        # reader already authenticated the command semantics; here we retain its
        # raw decision identity/bytes and demand one selected command per member.
        decisions: list[tuple[str, bytes, str, PhysicalPublicationCommand]] = []
        for decision_id, _predecessor, raw in self._runtime._loop_decisions().entries():
            entry = json.loads(raw)
            if entry.get("kind") != "DECIDED":
                continue
            command = self._runtime._publication(entry)
            if command.tenant_id != self._runtime._tenant_id:
                return None
            decisions.append((str(decision_id), raw, hashlib.sha256(raw).hexdigest(), command))
        members: list[R14NativeExecutionRecordSourceMember] = []
        with read_connection(self._runtime._database) as connection:
            for schema, record_id, _unused in expected:
                candidates = []
                for decision_id, raw, decision_fingerprint, command in decisions:
                    matches = [
                        item
                        for item in command.records
                        if item.record_id == record_id and item.schema_id == schema
                    ]
                    if len(matches) > 1:
                        return None
                    if matches:
                        state = inspect_publication(connection, command, command.expected_head + 1)
                        if state != "COMPLETE":
                            return None
                        candidates.append(
                            (decision_id, raw, decision_fingerprint, command, matches[0])
                        )
                if len(candidates) != 1:
                    return None
                decision_id, raw, decision_fingerprint, command, member = candidates[0]
                row = connection.execute(
                    "SELECT owner,schema_id,canonical_bytes,commit_sequence FROM records "
                    "WHERE tenant_id=? AND record_id=?",
                    (self._runtime._tenant_id, record_id),
                ).fetchone()
                if (
                    row is None
                    or row[0] != member.owner
                    or row[1] != member.schema_id
                    or row[2] != member.canonical_bytes
                    or row[3] != command.expected_head + 1
                ):
                    return None
                members.append(
                    R14NativeExecutionRecordSourceMember(
                        member.owner,
                        member.schema_id,
                        member.record_id,
                        member.canonical_bytes,
                        decision_id,
                        raw,
                        decision_fingerprint,
                        command.operation_kind,
                        int(row[3]),
                    )
                )
        ordered = tuple(members)
        if len({item.record_id for item in ordered}) != len(ordered):
            return None
        prepared = source.original_prepare.prepared_run
        manifest_fingerprint = _fingerprint(
            source.tenant_id,
            source_stamp,
            prepared,
            selected.selected_seal.response_seal,
            selected.initialized,
            terminal_ref,
            result_ref,
            source.target_run_lineage,
            ordered,
        )
        return R14NativeExecutionRecordSourceManifest(
            source.tenant_id,
            source_stamp,
            prepared,
            selected.selected_seal.response_seal,
            selected.initialized,
            terminal_ref,
            result_ref,
            source.target_run_lineage,
            ordered,
            manifest_fingerprint,
        )


__all__ = [
    "R14NativeExecutionRecordSourceManifest",
    "R14NativeExecutionRecordSourceMember",
]
