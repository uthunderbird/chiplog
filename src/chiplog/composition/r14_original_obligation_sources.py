"""Selected immutable sources that anchor an R14 original obligation."""

from __future__ import annotations

import hashlib

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.execution_recovery_observations import RecoverySourceRecord
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.composition.r14_accepted_open_accounting_records import (
    RetainedAcceptedOpenRecoveryPublication,
    accepted_open_recovery_command,
)
from chiplog.platform._sqlite import PhysicalRecord


def selected_recovery_source(
    record: PhysicalRecord,
    *,
    subject: CallSubjectHead,
    selected_decision_id: str,
    selected_decision_bytes: bytes,
) -> RecoverySourceRecord:
    """Bind one semantic source to its exact selected decision and physical member."""
    if not selected_decision_id or not selected_decision_bytes:
        raise ValueError("selected R14 recovery source requires its decision bytes")
    if not all((record.record_id, record.owner, record.schema_id, record.canonical_bytes)):
        raise ValueError("selected R14 recovery source has an incomplete physical member")
    digest = hashlib.sha256(record.canonical_bytes).hexdigest()
    if record.fingerprint != digest:
        raise ValueError("selected R14 recovery source physical fingerprint differs")
    if not subject.subject_id or subject.revision.kind != "PRESENT":
        raise ValueError("selected R14 recovery source requires a present semantic subject")
    decision_digest = hashlib.sha256(selected_decision_bytes).hexdigest()
    return RecoverySourceRecord(
        owner=record.owner,
        subject=subject,
        schema_id=record.schema_id,
        canonical_record_bytes=record.canonical_bytes,
        selected_decision=CallSubjectHead(
            subject_id=selected_decision_id,
            revision=Present(head=selected_decision_id, fingerprint=decision_digest),
        ),
        physical_record=CallSubjectHead(
            subject_id=record.record_id,
            revision=Present(head="record:" + digest, fingerprint=digest),
        ),
    )


def accepted_open_recovery_sources(
    evidence: RetainedAcceptedOpenRecoveryPublication,
    *,
    selected_decision_id: str,
    selected_decision_bytes: bytes,
) -> tuple[RecoverySourceRecord, RecoverySourceRecord]:
    """Expose only the selected original-obligation opening and call terminal."""
    command = accepted_open_recovery_command(evidence)
    opening = evidence.result.opening
    terminal = evidence.result.terminal
    records = {record.record_id: record for record in command.records}
    try:
        opening_record = records[opening.opening_id]
        terminal_record = records[terminal.terminal_id]
    except KeyError as error:
        raise ValueError("selected accepted/open batch omits an original source") from error
    opening_bytes = opening.canonical_bytes()
    opening_source = selected_recovery_source(
        opening_record,
        subject=CallSubjectHead(
            subject_id=opening.original.obligation_id,
            revision=Present(
                head=opening.original.obligation_head,
                fingerprint=hashlib.sha256(opening_bytes).hexdigest(),
            ),
        ),
        selected_decision_id=selected_decision_id,
        selected_decision_bytes=selected_decision_bytes,
    )
    terminal_bytes = terminal.canonical_bytes()
    terminal_digest = hashlib.sha256(terminal_bytes).hexdigest()
    terminal_source = selected_recovery_source(
        terminal_record,
        subject=CallSubjectHead(
            subject_id=terminal.original_call_id,
            revision=Present(head="record:" + terminal_digest, fingerprint=terminal_digest),
        ),
        selected_decision_id=selected_decision_id,
        selected_decision_bytes=selected_decision_bytes,
    )
    return opening_source, terminal_source
