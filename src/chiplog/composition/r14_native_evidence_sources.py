"""Exact child evidence provenance from an authenticated R16 dispatch history."""

from __future__ import annotations

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.execution_recovery_observations import RecoverySourceRecord
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.effects.dispatch_outcome_contracts import (
    DispatchOutcomeRecordV2,
)
from chiplog.capabilities.effects.dispatch_outcomes import evidence_head
from chiplog.composition.r16_dispatch_history import VerifiedDispatchHistory


def complete_ordered_evidence_sources(
    terminal: DispatchOutcomeRecordV2,
    history: VerifiedDispatchHistory,
) -> tuple[RecoverySourceRecord, ...] | None:
    """Map each exact terminal evidence head to its unique selected append carrier.

    The source bytes are the selected physical R16 outcome row that appended the
    child evidence. The child evidence head remains the semantic subject.
    """
    if type(terminal) is not DispatchOutcomeRecordV2:
        return None
    expected_heads = tuple(evidence_head(item) for item in terminal.snapshot.evidence)
    if expected_heads != terminal.snapshot.evidence_heads:
        return None
    if not expected_heads:
        return ()
    if len({head.subject_id for head in expected_heads}) != len(expected_heads):
        return None
    members_by_head = {member.record: member for member in history.members}
    if len(members_by_head) != len(history.members):
        return None

    sources: list[RecoverySourceRecord] = []
    for evidence, evidence_ref in zip(terminal.snapshot.evidence, expected_heads, strict=True):
        carriers = [
            record
            for record in history.v2_records
            if isinstance(record, DispatchOutcomeRecordV2)
            and record.snapshot.intent == terminal.snapshot.intent
            and record.kind == "EVIDENCE_RECORDED"
            and record.command.operation == "APPEND_EVIDENCE"
            and record.command.evidence == evidence
        ]
        if len(carriers) != 1:
            return None
        carrier = carriers[0]
        member = members_by_head.get(carrier.record)
        if (
            member is None
            or member.kind != carrier.kind
            or carrier.command.evidence is None
            or evidence_head(carrier.command.evidence) != evidence_ref
            or member.record_bytes != carrier.canonical_bytes()
            or member.intent.subject_id != terminal.snapshot.intent.intent_id
        ):
            return None
        sources.append(
            RecoverySourceRecord(
                owner="effects",
                subject=CallSubjectHead(
                    subject_id=evidence_ref.subject_id,
                    revision=Present(
                        head=evidence_ref.head,
                        fingerprint=evidence_ref.fingerprint,
                    ),
                ),
                schema_id=carrier.schema_id,
                canonical_record_bytes=member.record_bytes,
                selected_decision=CallSubjectHead(
                    subject_id=member.selected_decision.subject_id,
                    revision=Present(
                        head=member.selected_decision.head,
                        fingerprint=member.selected_decision.fingerprint,
                    ),
                ),
                physical_record=CallSubjectHead(
                    subject_id=member.record.subject_id,
                    revision=Present(
                        head=member.record.head,
                        fingerprint=member.record.fingerprint,
                    ),
                ),
            )
        )
    return tuple(sources)
