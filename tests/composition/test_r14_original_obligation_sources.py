from __future__ import annotations

import hashlib

import pytest

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.composition.r14_original_obligation_sources import selected_recovery_source
from chiplog.platform._sqlite import PhysicalRecord


def test_selected_recovery_source_binds_decision_and_physical_member() -> None:
    raw = b'{"opening_id":"opening-1"}'
    record = PhysicalRecord(
        record_id="opening-1",
        owner="agent_loop",
        schema_id="chiplog.execution.accepted-open-recovery-opening.v1",
        canonical_bytes=raw,
        fingerprint=hashlib.sha256(raw).hexdigest(),
    )
    decision = b'{"kind":"DECIDED","operation_id":"op-1"}'

    source = selected_recovery_source(
        record,
        subject=CallSubjectHead(
            subject_id="obligation-1",
            revision=Present(head="open:obligation-1", fingerprint="1" * 64),
        ),
        selected_decision_id="decision-1",
        selected_decision_bytes=decision,
    )

    assert source.owner == "agent_loop"
    assert source.schema_id == record.schema_id
    assert source.canonical_record_bytes == raw
    assert source.subject.subject_id == "obligation-1"
    assert source.subject.revision.head == "open:obligation-1"
    assert source.physical_record.subject_id == "opening-1"
    assert source.physical_record.revision.fingerprint == hashlib.sha256(raw).hexdigest()
    assert source.selected_decision.subject_id == "decision-1"
    assert source.selected_decision.revision.fingerprint == hashlib.sha256(decision).hexdigest()


def test_selected_recovery_source_rejects_physical_fingerprint_mismatch() -> None:
    record = PhysicalRecord(
        record_id="opening-1",
        owner="agent_loop",
        schema_id="chiplog.execution.accepted-open-recovery-opening.v1",
        canonical_bytes=b"record",
        fingerprint="0" * 64,
    )

    with pytest.raises(ValueError, match="physical fingerprint differs"):
        selected_recovery_source(
            record,
            subject=CallSubjectHead(
                subject_id="obligation-1",
                revision=Present(head="open:obligation-1", fingerprint="1" * 64),
            ),
            selected_decision_id="decision-1",
            selected_decision_bytes=b"decision",
        )
