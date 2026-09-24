"""Focused consumer checks for the closed Phase-C recovery member codec."""

import hashlib
import json

import pytest

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.execution_recovery_observations import (
    ExecutionTerminalManifest,
    RecoverySourceRecord,
    SealedAccountingRecord,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Present, RecoveryDTO
from chiplog.capabilities.agent_loop.recovery_record_contracts import (
    AGENT_LOOP_OWNER,
    RECOVERY_RECORD_ROWS,
    RecoveryRecordIntegrityError,
    RecoveryRecordMember,
    decode_recovery_record_member,
    validate_recovery_source_record,
    validate_terminal_accounting_manifest,
)


def head(name: str) -> CallSubjectHead:
    return CallSubjectHead(
        subject_id=name, revision=Present(head="physical:" + name, fingerprint="a" * 64)
    )


def member(kind: str, schema: str, record: RecoveryDTO, record_id: str) -> RecoveryRecordMember:
    raw = record.canonical_bytes()
    return RecoveryRecordMember(
        owner=AGENT_LOOP_OWNER,
        record_kind=kind,
        schema_id=schema,
        record_id=record_id,
        canonical_record_bytes=raw,
        fingerprint=hashlib.sha256(raw).hexdigest(),
    )


def accounting() -> SealedAccountingRecord:
    return SealedAccountingRecord(
        accounting_id="accounting",
        source_cut_fingerprint="b" * 64,
        sealed_response=head("response"),
        sealed_manifest_fingerprint="c" * 64,
        registry=head("registry"),
        frontier=head("frontier"),
        complete_ordered_calls=(),
    )


def test_literal_closed_table_has_all_sixteen_exact_rows() -> None:
    assert len(RECOVERY_RECORD_ROWS) == 16
    assert {(row.owner, row.record_kind, row.schema_id) for row in RECOVERY_RECORD_ROWS} == {
        (AGENT_LOOP_OWNER, "SEALED_ACCOUNTING", "chiplog.execution.sealed-accounting.v1"),
        (AGENT_LOOP_OWNER, "CONTINUATION_READY", "chiplog.execution.continuation-ready.v1"),
        (AGENT_LOOP_OWNER, "SUSPENSION_BASELINE", "chiplog.execution.suspension-baseline.v2"),
        (AGENT_LOOP_OWNER, "SUSPENSION_PAIR", "chiplog.execution.suspension-pair.v2"),
        (AGENT_LOOP_OWNER, "TERMINAL_MANIFEST", "chiplog.execution.terminal-manifest.v1"),
        (AGENT_LOOP_OWNER, "SUCCESSOR_EDGE", "chiplog.execution.successor-edge.v1"),
        (
            AGENT_LOOP_OWNER,
            "ORIGINAL_RESOLUTION_BASIS",
            "chiplog.loop.original-resolution-basis.v1",
        ),
        (
            AGENT_LOOP_OWNER,
            "ORIGINAL_OBLIGATION_CLOSURE",
            "chiplog.loop.original-obligation-closure.v1",
        ),
        (AGENT_LOOP_OWNER, "RECOVERED_CALL_OUTCOME", "chiplog.loop.recovered-call-outcome.v1"),
        (AGENT_LOOP_OWNER, "ORIGINAL_RESOLVER_BATCH", "chiplog.loop.original-resolver-batch.v1"),
        (AGENT_LOOP_OWNER, "LOOP_SEMANTIC_REDUCTION", "chiplog.loop.semantic-reduction.v1"),
        (AGENT_LOOP_OWNER, "READONLY_ATTEMPT_ACCEPTED", "chiplog.readonly.attempt-accepted.v1"),
        (AGENT_LOOP_OWNER, "READONLY_ATTEMPT_OUTCOME", "chiplog.readonly.attempt-outcome.v1"),
        (AGENT_LOOP_OWNER, "READONLY_COUNTER", "chiplog.readonly.counter.v1"),
        (AGENT_LOOP_OWNER, "READONLY_CALL_OUTCOME", "chiplog.readonly.call-outcome.v1"),
        (AGENT_LOOP_OWNER, "LATE_EXECUTION_RESPONSE", "chiplog.execution.late-response.v1"),
    }


def test_decoder_binds_canonical_bytes_identity_and_dto_discriminant_separately() -> None:
    record = accounting()
    supplied = member(
        "SEALED_ACCOUNTING", "chiplog.execution.sealed-accounting.v1", record, "accounting"
    )
    decoded = decode_recovery_record_member(supplied)
    assert decoded.record == record
    assert decoded.member.canonical_record_bytes == record.canonical_bytes()
    assert decoded.member.fingerprint == hashlib.sha256(record.canonical_bytes()).hexdigest()


@pytest.mark.parametrize("field", ["owner", "record_kind", "schema_id", "record_id", "fingerprint"])
def test_decoder_rejects_one_envelope_dimension_from_a_valid_member(field: str) -> None:
    supplied = member(
        "SEALED_ACCOUNTING", "chiplog.execution.sealed-accounting.v1", accounting(), "accounting"
    )
    value = "rival" if field != "fingerprint" else "0" * 64
    with pytest.raises(RecoveryRecordIntegrityError):
        decode_recovery_record_member(supplied.model_copy(update={field: value}))


def test_decoder_rejects_noncanonical_valid_json_bytes() -> None:
    supplied = member(
        "SEALED_ACCOUNTING", "chiplog.execution.sealed-accounting.v1", accounting(), "accounting"
    )
    payload = json.loads(supplied.canonical_record_bytes)
    raw = json.dumps(payload, indent=1).encode()
    noncanonical = supplied.model_copy(
        update={"canonical_record_bytes": raw, "fingerprint": hashlib.sha256(raw).hexdigest()}
    )
    with pytest.raises(RecoveryRecordIntegrityError):
        decode_recovery_record_member(noncanonical)


def test_source_wrapper_binds_physical_member_and_keeps_decision_independent() -> None:
    supplied = member(
        "SEALED_ACCOUNTING", "chiplog.execution.sealed-accounting.v1", accounting(), "accounting"
    )
    decoded = decode_recovery_record_member(supplied)
    decision = head("decision")
    source = RecoverySourceRecord(
        owner=AGENT_LOOP_OWNER,
        subject=head("subject"),
        schema_id=supplied.schema_id,
        canonical_record_bytes=supplied.canonical_record_bytes,
        selected_decision=decision,
        physical_record=CallSubjectHead(
            subject_id="accounting",
            revision=Present(head=supplied.record_id, fingerprint=supplied.fingerprint),
        ),
    )
    validate_recovery_source_record(source, decoded, decision)
    with pytest.raises(RecoveryRecordIntegrityError):
        validate_recovery_source_record(source, decoded, head("other-decision"))


def test_content_derived_counter_identity_is_schema_scoped_and_canonical() -> None:
    from chiplog.capabilities.agent_loop.readonly_execution_contracts import ReadOnlyCounterRecord

    record = ReadOnlyCounterRecord(
        original_call_id="call",
        lineage_id="lineage",
        predecessor=head("counter"),
        attempts_consumed=1,
        closed=False,
    )
    raw = record.canonical_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    supplied = member(
        "READONLY_COUNTER",
        "chiplog.readonly.counter.v1",
        record,
        "chiplog.readonly.counter.v1:" + digest,
    )
    assert decode_recovery_record_member(supplied).record == record
    with pytest.raises(RecoveryRecordIntegrityError):
        decode_recovery_record_member(
            supplied.model_copy(update={"record_id": "chiplog.readonly.counter.v1:" + "0" * 64})
        )


def test_terminal_manifest_binds_the_complete_ordered_accounting_batch() -> None:
    first = accounting()
    second = first.model_copy(update={"accounting_id": "accounting-2"})
    first_member = decode_recovery_record_member(
        member(
            "SEALED_ACCOUNTING",
            "chiplog.execution.sealed-accounting.v1",
            first,
            first.accounting_id,
        )
    )
    second_member = decode_recovery_record_member(
        member(
            "SEALED_ACCOUNTING",
            "chiplog.execution.sealed-accounting.v1",
            second,
            second.accounting_id,
        )
    )
    terminal = ExecutionTerminalManifest(
        manifest_id="terminal",
        command_id="terminal-command",
        prior_run=head("run"),
        target="CANCELLED",
        source_cut_fingerprint="b" * 64,
        complete_accounting=(first, second),
        complete_open_original_obligations=(),
    )
    terminal_member = decode_recovery_record_member(
        member("TERMINAL_MANIFEST", "chiplog.execution.terminal-manifest.v1", terminal, "terminal")
    )
    validate_terminal_accounting_manifest((first_member, second_member), terminal_member)
    with pytest.raises(RecoveryRecordIntegrityError):
        validate_terminal_accounting_manifest((second_member, first_member), terminal_member)
