"""Closed physical-member codec for read-only lineage records.

This boundary verifies immutable member shapes and content identities only.  It
does not assemble operations: in particular, it does not decide whether a
successor attempt is followed by another pending frontier or immediate terminal
reduction.
"""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import Field, ValidationError

from .call_acceptance_contracts import CallSubjectHead
from .readonly_execution_contracts import (
    ReadOnlyAttemptAcceptedRecord,
    ReadOnlyAttemptOutcomeRecord,
    ReadOnlyCallOutcomeRecord,
    ReadOnlyCounterRecord,
    ReadOnlyPendingTransition,
)
from .recovery_contracts import (
    Absent,
    Digest,
    HeadMarker,
    Identity,
    Present,
    RecoveryDTO,
    UInt64,
)
from .recovery_frontier_contracts import (
    PendingCallFrontier,
    ReadOnlyAttemptMember,
    ReadOnlyRetryLineage,
    TerminalCallFrontier,
)
from .recovery_record_contracts import AGENT_LOOP_OWNER as RECOVERY_AGENT_LOOP_OWNER
from .recovery_record_contracts import (
    DecodedRecoveryRecordMember,
    RecoveryRecordIntegrityError,
    RecoveryRecordMember,
    RecoveryRecordRow,
    decode_recovery_record_member,
)

AGENT_LOOP_OWNER = RECOVERY_AGENT_LOOP_OWNER


class ReadOnlyPendingRecord(RecoveryDTO):
    """Acyclic body for a pending frontier; its physical reference is projected later."""

    kind: Literal["READ_ONLY_RETRY_PENDING"] = "READ_ONLY_RETRY_PENDING"
    schema_id: Literal["chiplog.readonly.pending.v1"] = "chiplog.readonly.pending.v1"
    original_call_id: Identity
    response_id: Identity
    terminal: Absent
    call_outcome: Absent
    initialized: Present
    lineage: ReadOnlyRetryLineage
    ordered_attempts: tuple[ReadOnlyAttemptMember, ...] = Field(min_length=1)
    last_retryable_failure: Present
    counter: Present
    attempts_consumed: UInt64
    next_ordinal: UInt64
    readonly_proof: Present
    snapshot: Present
    execution_binding: HeadMarker
    crossed_binding_heads: tuple[Present, ...]
    closure_registry_id: Identity
    closure_registry_version: Identity


class ReadOnlyTerminalDispositionRecord(RecoveryDTO):
    schema_id: Literal["chiplog.readonly.terminal-disposition.v1"] = (
        "chiplog.readonly.terminal-disposition.v1"
    )
    original_call_id: Identity
    response_id: Identity
    lineage_id: Identity
    selected_attempt_outcome: CallSubjectHead
    disposition: Literal["SUCCEEDED", "FAILED_DEFINITE", "OUTCOME_UNKNOWN", "RECOVERY_REQUIRED"]
    provenance: Literal["ATTEMPT_RESULT", "FROZEN_BUDGET_EXHAUSTED"]
    result_or_obligation: CallSubjectHead


class ReadOnlyReducerBatchRecord(RecoveryDTO):
    schema_id: Literal["chiplog.readonly.reducer-batch.v1"] = "chiplog.readonly.reducer-batch.v1"
    source_request_fingerprint: Digest
    original_call_id: Identity
    response_id: Identity
    lineage_id: Identity
    reducer_id: Identity
    reducer_version: Identity
    budget_version: Identity
    complete_ordered_outcomes: tuple[CallSubjectHead, ...] = Field(min_length=1)
    complete_manifest_fingerprint: Digest
    selected_attempt_outcome: CallSubjectHead
    closed_counter: CallSubjectHead
    terminal_disposition: CallSubjectHead
    call_outcome: CallSubjectHead
    core_members_fingerprint: Digest


READONLY_RECORD_ROWS = (
    RecoveryRecordRow(
        AGENT_LOOP_OWNER,
        "READONLY_ATTEMPT_ACCEPTED",
        "chiplog.readonly.attempt-accepted.v1",
        ReadOnlyAttemptAcceptedRecord,
        None,
    ),
    RecoveryRecordRow(
        AGENT_LOOP_OWNER,
        "READONLY_ATTEMPT_OUTCOME",
        "chiplog.readonly.attempt-outcome.v1",
        ReadOnlyAttemptOutcomeRecord,
        None,
    ),
    RecoveryRecordRow(
        AGENT_LOOP_OWNER,
        "READONLY_COUNTER",
        "chiplog.readonly.counter.v1",
        ReadOnlyCounterRecord,
        None,
    ),
    RecoveryRecordRow(
        AGENT_LOOP_OWNER,
        "READONLY_CALL_OUTCOME",
        "chiplog.readonly.call-outcome.v1",
        ReadOnlyCallOutcomeRecord,
        None,
    ),
    RecoveryRecordRow(
        AGENT_LOOP_OWNER,
        "READONLY_PENDING",
        "chiplog.readonly.pending.v1",
        ReadOnlyPendingRecord,
        None,
    ),
    RecoveryRecordRow(
        AGENT_LOOP_OWNER,
        "READONLY_PENDING_FRONTIER",
        "chiplog.readonly.pending-frontier.v1",
        PendingCallFrontier,
        None,
    ),
    RecoveryRecordRow(
        AGENT_LOOP_OWNER,
        "READONLY_TERMINAL_DISPOSITION",
        "chiplog.readonly.terminal-disposition.v1",
        ReadOnlyTerminalDispositionRecord,
        None,
    ),
    RecoveryRecordRow(
        AGENT_LOOP_OWNER,
        "READONLY_REDUCER_BATCH",
        "chiplog.readonly.reducer-batch.v1",
        ReadOnlyReducerBatchRecord,
        None,
    ),
    RecoveryRecordRow(
        AGENT_LOOP_OWNER,
        "READONLY_TERMINAL_FRONTIER",
        "chiplog.readonly.terminal-frontier.v1",
        TerminalCallFrontier,
        None,
    ),
    RecoveryRecordRow(
        AGENT_LOOP_OWNER,
        "READONLY_PENDING_TRANSITION",
        "chiplog.readonly.pending-transition.v1",
        ReadOnlyPendingTransition,
        None,
    ),
)

_ROWS = {(row.owner, row.record_kind, row.schema_id): row for row in READONLY_RECORD_ROWS}
_EXISTING_KINDS = frozenset(
    {
        "READONLY_ATTEMPT_ACCEPTED",
        "READONLY_ATTEMPT_OUTCOME",
        "READONLY_COUNTER",
        "READONLY_CALL_OUTCOME",
    }
)


def _content_id(schema_id: str, canonical_bytes: bytes) -> str:
    return schema_id + ":" + hashlib.sha256(canonical_bytes).hexdigest()


def _require(actual: object, expected: object, reason: str) -> None:
    if actual != expected:
        raise RecoveryRecordIntegrityError(reason)


def _decode_new_member(
    member: RecoveryRecordMember, row: RecoveryRecordRow
) -> DecodedRecoveryRecordMember:
    try:
        record = row.record_type.model_validate_json(member.canonical_record_bytes)
    except ValidationError as error:
        raise RecoveryRecordIntegrityError("invalid read-only record bytes") from error
    if record.canonical_bytes() != member.canonical_record_bytes:
        raise RecoveryRecordIntegrityError("read-only record bytes are not canonical")
    fingerprint = hashlib.sha256(member.canonical_record_bytes).hexdigest()
    _require(member.fingerprint, fingerprint, "read-only record fingerprint mismatch")
    embedded_schema = getattr(record, "schema_id", None)
    if embedded_schema is not None:
        _require(embedded_schema, member.schema_id, "embedded read-only schema mismatch")
    _require(
        member.record_id,
        _content_id(row.schema_id, member.canonical_record_bytes),
        "read-only record physical ID mismatch",
    )
    return DecodedRecoveryRecordMember(member=member, record=record)


def decode_readonly_record_member(member: RecoveryRecordMember) -> DecodedRecoveryRecordMember:
    """Decode one member of the closed ten-row read-only physical registry."""
    row = _ROWS.get((member.owner, member.record_kind, member.schema_id))
    if row is None:
        raise RecoveryRecordIntegrityError("unregistered read-only record owner, kind, or schema")
    if member.record_kind in _EXISTING_KINDS:
        return decode_recovery_record_member(member)
    return _decode_new_member(member, row)


def pending_frontier_from_body(body_member: RecoveryRecordMember) -> PendingCallFrontier:
    """Project a hashed pending body into the historical pending-frontier shape."""
    decoded = decode_readonly_record_member(body_member)
    if not isinstance(decoded.record, ReadOnlyPendingRecord):
        raise RecoveryRecordIntegrityError("pending projection requires a pending body member")
    body = decoded.record
    _require(body.terminal, Absent(), "pending body terminal must be absent")
    _require(body.call_outcome, Absent(), "pending body call outcome must be absent")
    return PendingCallFrontier(
        original_call_id=body.original_call_id,
        response_id=body.response_id,
        pending=Present(head=body_member.record_id, fingerprint=body_member.fingerprint),
        terminal=body.terminal,
        call_outcome=body.call_outcome,
        initialized=body.initialized,
        lineage=body.lineage,
        ordered_attempts=body.ordered_attempts,
        last_retryable_failure=body.last_retryable_failure,
        counter=body.counter,
        attempts_consumed=body.attempts_consumed,
        next_ordinal=body.next_ordinal,
        readonly_proof=body.readonly_proof,
        snapshot=body.snapshot,
        execution_binding=body.execution_binding,
        crossed_binding_heads=body.crossed_binding_heads,
        closure_registry_id=body.closure_registry_id,
        closure_registry_version=body.closure_registry_version,
    )
