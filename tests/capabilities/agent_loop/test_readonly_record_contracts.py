"""Closed physical codec for the ten read-only recovery rows."""

from __future__ import annotations

import hashlib
import json

import pytest
from tests.support.recovery_records import head, records

from chiplog.capabilities.agent_loop.readonly_execution_contracts import ReadOnlyPendingTransition
from chiplog.capabilities.agent_loop.readonly_record_contracts import (
    AGENT_LOOP_OWNER,
    READONLY_RECORD_ROWS,
    ReadOnlyPendingRecord,
    ReadOnlyReducerBatchRecord,
    ReadOnlyTerminalDispositionRecord,
    decode_readonly_record_member,
    pending_frontier_from_body,
)
from chiplog.capabilities.agent_loop.recovery_contracts import (
    Absent,
    NotApplicable,
    Present,
    RecoveryDTO,
)
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import (
    ReadOnlyAcceptedCall,
    ReadOnlyAttemptMember,
    ReadOnlyRetryLineage,
    SuccessResult,
    TerminalCallFrontier,
)
from chiplog.capabilities.agent_loop.recovery_record_contracts import (
    RecoveryRecordIntegrityError,
    RecoveryRecordMember,
)


def _member(kind: str, record: RecoveryDTO) -> RecoveryRecordMember:
    row = next(value for value in READONLY_RECORD_ROWS if value.record_kind == kind)
    raw = record.canonical_bytes()
    fingerprint = hashlib.sha256(raw).hexdigest()
    return RecoveryRecordMember(
        owner=AGENT_LOOP_OWNER,
        record_kind=row.record_kind,
        schema_id=row.schema_id,
        record_id=row.schema_id + ":" + fingerprint,
        canonical_record_bytes=raw,
        fingerprint=fingerprint,
    )


def _lineage() -> ReadOnlyRetryLineage:
    return ReadOnlyRetryLineage(
        lineage_id="lineage",
        original_call_id="original-call",
        max_attempts=2,
        budget_version="budget.v1",
        reducer_id="reducer",
        reducer_version="1",
    )


def _pending_body() -> ReadOnlyPendingRecord:
    lineage = _lineage()
    return ReadOnlyPendingRecord(
        original_call_id=lineage.original_call_id,
        response_id="original-response",
        terminal=Absent(),
        call_outcome=Absent(),
        initialized=head("initialized").revision,
        lineage=lineage,
        ordered_attempts=(
            ReadOnlyAttemptMember(
                ordinal=0,
                attempt_id="attempt:0",
                initialized=head("initialized").revision,
                accepted=head("accepted").revision,
                outcome=head("outcome").revision,
                result_or_obligation=head("result").revision,
                predecessor=Absent(),
            ),
        ),
        last_retryable_failure=head("outcome").revision,
        counter=head("counter").revision,
        attempts_consumed=1,
        next_ordinal=1,
        readonly_proof=head("proof").revision,
        snapshot=head("snapshot").revision,
        execution_binding=head("execution").revision,
        crossed_binding_heads=(head("crossed").revision,),
        closure_registry_id="closure",
        closure_registry_version="1",
    )


def _terminal() -> TerminalCallFrontier:
    lineage = _lineage()
    attempts = _pending_body().ordered_attempts
    return TerminalCallFrontier(
        original_call_id=lineage.original_call_id,
        response_id="original-response",
        acceptance=ReadOnlyAcceptedCall(
            initialized=head("initialized").revision,
            accepted=head("accepted").revision,
            lineage_id=lineage.lineage_id,
            ordinal=0,
            readonly_proof=head("proof").revision,
            snapshot_binding=head("snapshot").revision,
            lineage=lineage,
            complete_ordered_attempts=attempts,
            shared_counter=head("closed-counter").revision,
            attempts_consumed=1,
            call_level_outcome=head("call-outcome").revision,
            complete_lineage_reducer_batch=head("reducer-batch").revision,
        ),
        disposition=SuccessResult(
            terminal=head("terminal").revision,
            result=head("result").revision,
            recovered_outcome=NotApplicable(),
        ),
    )


def _new_members() -> dict[str, RecoveryRecordMember]:
    pending_body = _member("READONLY_PENDING", _pending_body())
    pending_frontier = pending_frontier_from_body(pending_body)
    pending_frontier_member = _member("READONLY_PENDING_FRONTIER", pending_frontier)
    disposition = _member(
        "READONLY_TERMINAL_DISPOSITION",
        ReadOnlyTerminalDispositionRecord(
            original_call_id="original-call",
            response_id="original-response",
            lineage_id="lineage",
            selected_attempt_outcome=head("outcome"),
            disposition="SUCCEEDED",
            provenance="ATTEMPT_RESULT",
            result_or_obligation=head("result"),
        ),
    )
    batch = _member(
        "READONLY_REDUCER_BATCH",
        ReadOnlyReducerBatchRecord(
            source_request_fingerprint="a" * 64,
            original_call_id="original-call",
            response_id="original-response",
            lineage_id="lineage",
            reducer_id="reducer",
            reducer_version="1",
            budget_version="budget.v1",
            complete_ordered_outcomes=(head("outcome"),),
            complete_manifest_fingerprint="b" * 64,
            selected_attempt_outcome=head("outcome"),
            closed_counter=head("closed-counter"),
            terminal_disposition=head("terminal-disposition"),
            call_outcome=head("call-outcome"),
            core_members_fingerprint="c" * 64,
        ),
    )
    terminal = _terminal()
    terminal_member = _member("READONLY_TERMINAL_FRONTIER", terminal)
    transition = _member(
        "READONLY_PENDING_TRANSITION",
        ReadOnlyPendingTransition(
            previous=pending_frontier,
            closure="CALL_REDUCED_TERMINAL",
            replacement=terminal,
        ),
    )
    return {
        "READONLY_PENDING": pending_body,
        "READONLY_PENDING_FRONTIER": pending_frontier_member,
        "READONLY_TERMINAL_DISPOSITION": disposition,
        "READONLY_REDUCER_BATCH": batch,
        "READONLY_TERMINAL_FRONTIER": terminal_member,
        "READONLY_PENDING_TRANSITION": transition,
    }


def _all_members() -> dict[str, RecoveryRecordMember]:
    existing = records().members
    return {
        "READONLY_ATTEMPT_ACCEPTED": existing["READONLY_ATTEMPT_ACCEPTED"],
        "READONLY_ATTEMPT_OUTCOME": existing["READONLY_ATTEMPT_OUTCOME"],
        "READONLY_COUNTER": existing["READONLY_COUNTER_OPEN"],
        "READONLY_CALL_OUTCOME": existing["READONLY_CALL_OUTCOME"],
        **_new_members(),
    }


def test_closed_ten_row_table_decodes_each_canonical_member() -> None:
    values = _all_members()
    assert len(READONLY_RECORD_ROWS) == 10
    assert set(values) == {row.record_kind for row in READONLY_RECORD_ROWS}
    for member in values.values():
        decoded = decode_readonly_record_member(member)
        assert decoded.member.canonical_record_bytes == decoded.record.canonical_bytes()
        assert decoded.member.record_id == member.schema_id + ":" + member.fingerprint


@pytest.mark.parametrize("field", ["owner", "record_kind", "schema_id", "record_id", "fingerprint"])
def test_decoder_rejects_wrong_envelope_dimension(field: str) -> None:
    member = _all_members()["READONLY_PENDING"]
    value = "rival" if field != "fingerprint" else "0" * 64
    with pytest.raises(RecoveryRecordIntegrityError):
        decode_readonly_record_member(member.model_copy(update={field: value}))


def test_decoder_rejects_semantically_equal_noncanonical_bytes() -> None:
    member = _all_members()["READONLY_PENDING"]
    raw = json.dumps(json.loads(member.canonical_record_bytes), indent=1).encode()
    altered = member.model_copy(
        update={
            "canonical_record_bytes": raw,
            "fingerprint": hashlib.sha256(raw).hexdigest(),
            "record_id": member.schema_id + ":" + hashlib.sha256(raw).hexdigest(),
        }
    )
    with pytest.raises(RecoveryRecordIntegrityError):
        decode_readonly_record_member(altered)


def test_decoder_does_not_widen_to_the_global_recovery_registry() -> None:
    unrelated = records().members["SEALED_ACCOUNTING"]
    with pytest.raises(RecoveryRecordIntegrityError):
        decode_readonly_record_member(unrelated)


def test_pending_projection_uses_body_hash_without_self_referential_bytes() -> None:
    body = _member("READONLY_PENDING", _pending_body())
    frontier = pending_frontier_from_body(body)
    assert frontier.pending == Present(head=body.record_id, fingerprint=body.fingerprint)
    assert frontier.pending.head != frontier.original_call_id
    assert frontier.pending.head != frontier.lineage.lineage_id
    assert body.record_id == (
        body.schema_id + ":" + hashlib.sha256(body.canonical_record_bytes).hexdigest()
    )
