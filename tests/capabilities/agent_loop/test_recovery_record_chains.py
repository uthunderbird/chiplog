"""End-to-end consumer checks for the closed recovery-record member graph."""

from __future__ import annotations

import hashlib
import json

import pytest
from tests.support.recovery_records import (
    RecoveryRecords,
    head,
    physical,
    records,
    records_with_closed_acceptance_counter,
)

from chiplog.capabilities.agent_loop.execution_recovery_observations import (
    ExecutionSuspensionBaseline,
    ExecutionSuspensionPair,
    RecoverySourceRecord,
    SelectedExecutionSuspension,
)
from chiplog.capabilities.agent_loop.original_recovery_contracts import OriginalResolutionBasis
from chiplog.capabilities.agent_loop.readonly_execution_contracts import (
    ReadOnlyAttemptOutcomeRecord,
    ReadOnlyCounterRecord,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Absent, Present
from chiplog.capabilities.agent_loop.recovery_record_contracts import (
    RECOVERY_RECORD_ROWS,
    DecodedRecoveryRecordMember,
    RecoveryRecordIntegrityError,
    decode_recovery_record_member,
    validate_accounting_continuation,
    validate_original_resolution,
    validate_readonly_reduction,
    validate_recovery_source_record,
    validate_selected_execution_suspension,
    validate_terminal_accounting_manifest,
)


def decoded_records() -> tuple[RecoveryRecords, dict[str, DecodedRecoveryRecordMember]]:
    fixture = records()
    return fixture, {
        kind: decode_recovery_record_member(member) for kind, member in fixture.members.items()
    }


def test_every_registered_row_decodes_its_actual_canonical_positive_record() -> None:
    fixture, decoded = decoded_records()
    actual_rows = {
        (member.owner, member.record_kind, member.schema_id) for member in fixture.members.values()
    }
    expected_rows = {(row.owner, row.record_kind, row.schema_id) for row in RECOVERY_RECORD_ROWS}
    assert actual_rows == expected_rows
    assert len(expected_rows) == 16
    for kind, member in fixture.members.items():
        result = decoded[kind]
        assert result.member.canonical_record_bytes == result.record.canonical_bytes()
        assert (
            result.member.fingerprint == hashlib.sha256(result.record.canonical_bytes()).hexdigest()
        )
        row = next(row for row in RECOVERY_RECORD_ROWS if row.record_kind == member.record_kind)
        expected_id = (
            str(getattr(result.record, row.identity_field))
            if row.identity_field is not None
            else member.schema_id + ":" + result.member.fingerprint
        )
        assert result.member.record_id == expected_id


@pytest.mark.parametrize(
    "kind",
    [
        "SUSPENSION_PAIR",
        "SUCCESSOR_EDGE",
        "LOOP_SEMANTIC_REDUCTION",
        "READONLY_ATTEMPT_ACCEPTED",
        "READONLY_ATTEMPT_OUTCOME",
        "READONLY_COUNTER_OPEN",
        "READONLY_CALL_OUTCOME",
    ],
)
def test_content_identity_is_schema_scoped_hash_of_actual_canonical_bytes(kind: str) -> None:
    fixture = records()
    member = fixture.members[kind]
    assert (
        member.record_id
        == member.schema_id + ":" + hashlib.sha256(member.canonical_record_bytes).hexdigest()
    )


def test_selected_suspension_binds_baseline_run_pair_bytes_and_decision() -> None:
    fixture, decoded = decoded_records()
    pair = decoded["SUSPENSION_PAIR"]
    assert isinstance(decoded["SUSPENSION_BASELINE"].record, ExecutionSuspensionBaseline)
    assert isinstance(pair.record, ExecutionSuspensionPair)
    selected = SelectedExecutionSuspension(
        baseline=decoded["SUSPENSION_BASELINE"].record,
        pair=pair.record,
        selected_pair=physical(pair.member, "selected-pair"),
        selected_decision=fixture.selected_decision,
        canonical_pair_bytes=pair.member.canonical_record_bytes,
    )
    validate_selected_execution_suspension(
        selected,
        decoded["SUSPENSION_BASELINE"],
        fixture.suspended_run,
        pair,
        fixture.selected_decision,
    )
    with pytest.raises(RecoveryRecordIntegrityError, match="selected pair bytes"):
        validate_selected_execution_suspension(
            selected.model_copy(update={"canonical_pair_bytes": b"different"}),
            decoded["SUSPENSION_BASELINE"],
            fixture.suspended_run,
            pair,
            fixture.selected_decision,
        )


def test_accounting_continuation_and_terminal_require_the_actual_full_ordered_tuple() -> None:
    _, decoded = decoded_records()
    validate_accounting_continuation(decoded["SEALED_ACCOUNTING"], decoded["CONTINUATION_READY"])
    accounting = (decoded["SEALED_ACCOUNTING"], decoded["SEALED_ACCOUNTING_SECOND"])
    validate_terminal_accounting_manifest(accounting, decoded["TERMINAL_MANIFEST"])
    with pytest.raises(RecoveryRecordIntegrityError, match="terminal accounting sequence"):
        validate_terminal_accounting_manifest(
            tuple(reversed(accounting)), decoded["TERMINAL_MANIFEST"]
        )


def test_original_basis_closure_outcome_batch_and_reduction_bind_all_physical_refs() -> None:
    fixture, decoded = decoded_records()
    validate_original_resolution(
        decoded["ORIGINAL_RESOLUTION_BASIS"],
        decoded["ORIGINAL_OBLIGATION_CLOSURE"],
        decoded["RECOVERED_CALL_OUTCOME"],
        decoded["ORIGINAL_RESOLVER_BATCH"],
        decoded["LOOP_SEMANTIC_REDUCTION"],
        Absent(),
        (head("witness"),),
    )
    with pytest.raises(RecoveryRecordIntegrityError, match="reduction evidence"):
        validate_original_resolution(
            decoded["ORIGINAL_RESOLUTION_BASIS"],
            decoded["ORIGINAL_OBLIGATION_CLOSURE"],
            decoded["RECOVERED_CALL_OUTCOME"],
            decoded["ORIGINAL_RESOLVER_BATCH"],
            decoded["LOOP_SEMANTIC_REDUCTION"],
            Absent(),
            (),
        )
    assert isinstance(decoded["ORIGINAL_RESOLUTION_BASIS"].record, OriginalResolutionBasis)
    assert decoded["ORIGINAL_RESOLUTION_BASIS"].record.original == fixture.original


def test_one_successful_readonly_attempt_requires_open_then_closed_same_count_counters() -> None:
    fixture, decoded = decoded_records()
    validate_readonly_reduction(
        decoded["READONLY_ATTEMPT_ACCEPTED"],
        decoded["READONLY_COUNTER_OPEN"],
        decoded["READONLY_ATTEMPT_OUTCOME"],
        decoded["READONLY_CALL_OUTCOME"],
        decoded["READONLY_COUNTER_CLOSED"],
        (decoded["READONLY_ATTEMPT_OUTCOME"],),
        fixture.terminal,
    )
    assert isinstance(decoded["READONLY_ATTEMPT_OUTCOME"].record, ReadOnlyAttemptOutcomeRecord)
    assert isinstance(decoded["READONLY_COUNTER_OPEN"].record, ReadOnlyCounterRecord)
    assert isinstance(decoded["READONLY_COUNTER_CLOSED"].record, ReadOnlyCounterRecord)
    assert decoded["READONLY_ATTEMPT_OUTCOME"].record.disposition.kind == "SUCCEEDED"
    assert decoded["READONLY_COUNTER_OPEN"].record.attempts_consumed == 1
    assert decoded["READONLY_COUNTER_CLOSED"].record.attempts_consumed == 1
    assert not decoded["READONLY_COUNTER_OPEN"].record.closed
    assert decoded["READONLY_COUNTER_CLOSED"].record.closed
    assert decoded["READONLY_COUNTER_CLOSED"].record.predecessor.revision == Present(
        head=decoded["READONLY_COUNTER_OPEN"].member.record_id,
        fingerprint=decoded["READONLY_COUNTER_OPEN"].member.fingerprint,
    )
    with pytest.raises(RecoveryRecordIntegrityError, match="outcomes"):
        validate_readonly_reduction(
            decoded["READONLY_ATTEMPT_ACCEPTED"],
            decoded["READONLY_COUNTER_OPEN"],
            decoded["READONLY_ATTEMPT_OUTCOME"],
            decoded["READONLY_CALL_OUTCOME"],
            decoded["READONLY_COUNTER_CLOSED"],
            (),
            fixture.terminal,
        )


def test_source_wrapper_uses_physical_record_head_without_conflating_selected_decision() -> None:
    fixture, decoded = decoded_records()
    member = decoded["SEALED_ACCOUNTING"].member
    source = RecoverySourceRecord(
        owner=member.owner,
        subject=head("accounting-subject"),
        schema_id=member.schema_id,
        canonical_record_bytes=member.canonical_record_bytes,
        selected_decision=fixture.selected_decision,
        physical_record=physical(member, "accounting-subject"),
    )
    validate_recovery_source_record(source, decoded["SEALED_ACCOUNTING"], fixture.selected_decision)
    with pytest.raises(RecoveryRecordIntegrityError, match="selected decision"):
        validate_recovery_source_record(
            source, decoded["SEALED_ACCOUNTING"], head("other-decision")
        )
    with pytest.raises(RecoveryRecordIntegrityError, match="source owner"):
        validate_recovery_source_record(
            source.model_copy(update={"owner": "rival-owner"}),
            decoded["SEALED_ACCOUNTING"],
            fixture.selected_decision,
        )


def test_readonly_reduction_rejects_a_canonically_closed_acceptance_counter() -> None:
    fixture = records_with_closed_acceptance_counter()
    decoded = {
        kind: decode_recovery_record_member(member) for kind, member in fixture.members.items()
    }
    assert isinstance(decoded["READONLY_COUNTER_OPEN"].record, ReadOnlyCounterRecord)
    assert decoded["READONLY_COUNTER_OPEN"].record.closed
    with pytest.raises(
        RecoveryRecordIntegrityError, match="accepted attempt requires an open counter"
    ):
        validate_readonly_reduction(
            decoded["READONLY_ATTEMPT_ACCEPTED"],
            decoded["READONLY_COUNTER_OPEN"],
            decoded["READONLY_ATTEMPT_OUTCOME"],
            decoded["READONLY_CALL_OUTCOME"],
            decoded["READONLY_COUNTER_CLOSED"],
            (decoded["READONLY_ATTEMPT_OUTCOME"],),
            fixture.terminal,
        )


@pytest.mark.parametrize("field", ["record_id", "fingerprint", "schema_id"])
def test_decoder_rejects_identity_and_schema_mutants_after_member_validation(field: str) -> None:
    fixture = records()
    member = fixture.members["SUSPENSION_PAIR"]
    value = "rival" if field != "fingerprint" else "0" * 64
    with pytest.raises(RecoveryRecordIntegrityError):
        decode_recovery_record_member(member.model_copy(update={field: value}))


def test_decoder_rejects_valid_json_with_noncanonical_order_and_omitted_join_reference() -> None:
    fixture = records()
    member = fixture.members["SUSPENSION_PAIR"]
    # The DTO parser accepts this JSON, but the codec requires the exact canonical byte sequence.
    payload = json.loads(member.canonical_record_bytes)
    reordered = json.dumps(dict(reversed(tuple(payload.items()))), separators=(",", ":")).encode()
    with pytest.raises(RecoveryRecordIntegrityError):
        decode_recovery_record_member(
            member.model_copy(
                update={
                    "canonical_record_bytes": reordered,
                    "fingerprint": hashlib.sha256(reordered).hexdigest(),
                }
            )
        )
