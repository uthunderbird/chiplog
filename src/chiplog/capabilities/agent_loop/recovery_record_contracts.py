"""Closed, owner-local decoding of Phase-C recovery record members.

Decoding proves only that supplied bytes are one of the registered record shapes
and that their claimed physical identity is exact.  It neither authenticates a
selected decision nor grants publication authority.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from pydantic import ValidationError

from .call_acceptance_contracts import CallSubjectHead
from .execution_recovery_contracts import ExecutionSuccessorEdge
from .execution_recovery_observations import (
    ContinuationReadyRecord,
    ExecutionSuspensionBaseline,
    ExecutionSuspensionPair,
    ExecutionTerminalManifest,
    RecoverySourceRecord,
    SealedAccountingRecord,
    SelectedExecutionSuspension,
)
from .model_attempt_recovery_contracts import LateExecutionResponseRecord
from .original_recovery_contracts import (
    LoopSemanticReductionRecord,
    OriginalObligationClosureRecord,
    OriginalResolutionBasis,
    OriginalResolverBatchRecord,
    RecoveredCallOutcomeRecord,
    ResolvedReductionAnchor,
)
from .readonly_execution_contracts import (
    ReadOnlyAttemptAcceptedRecord,
    ReadOnlyAttemptOutcomeRecord,
    ReadOnlyCallOutcomeRecord,
    ReadOnlyCounterRecord,
)
from .recovery_contracts import Absent, Digest, Identity, Present, RecoveryDTO

AGENT_LOOP_OWNER = "agent_loop"


class RecoveryRecordIntegrityError(ValueError):
    """A recovery member or its immutable companions disagree."""


class RecoveryRecordMember(RecoveryDTO):
    """The physical envelope for one of the closed Phase-C recovery rows."""

    owner: Identity
    record_kind: Identity
    schema_id: Identity
    record_id: Identity
    canonical_record_bytes: bytes
    fingerprint: Digest


@dataclass(frozen=True)
class RecoveryRecordRow:
    owner: str
    record_kind: str
    schema_id: str
    record_type: type[RecoveryDTO]
    identity_field: str | None


@dataclass(frozen=True)
class DecodedRecoveryRecordMember:
    member: RecoveryRecordMember
    record: RecoveryDTO


# This is deliberately a literal, closed table.  Registry kind is distinct from
# a DTO's own discriminant (where it has one).
RECOVERY_RECORD_ROWS = (
    RecoveryRecordRow(
        AGENT_LOOP_OWNER,
        "SEALED_ACCOUNTING",
        "chiplog.execution.sealed-accounting.v1",
        SealedAccountingRecord,
        "accounting_id",
    ),
    RecoveryRecordRow(
        AGENT_LOOP_OWNER,
        "CONTINUATION_READY",
        "chiplog.execution.continuation-ready.v1",
        ContinuationReadyRecord,
        "readiness_id",
    ),
    RecoveryRecordRow(
        AGENT_LOOP_OWNER,
        "SUSPENSION_BASELINE",
        "chiplog.execution.suspension-baseline.v2",
        ExecutionSuspensionBaseline,
        "baseline_id",
    ),
    RecoveryRecordRow(
        AGENT_LOOP_OWNER,
        "SUSPENSION_PAIR",
        "chiplog.execution.suspension-pair.v2",
        ExecutionSuspensionPair,
        None,
    ),
    RecoveryRecordRow(
        AGENT_LOOP_OWNER,
        "TERMINAL_MANIFEST",
        "chiplog.execution.terminal-manifest.v1",
        ExecutionTerminalManifest,
        "manifest_id",
    ),
    RecoveryRecordRow(
        AGENT_LOOP_OWNER,
        "SUCCESSOR_EDGE",
        "chiplog.execution.successor-edge.v1",
        ExecutionSuccessorEdge,
        None,
    ),
    RecoveryRecordRow(
        AGENT_LOOP_OWNER,
        "ORIGINAL_RESOLUTION_BASIS",
        "chiplog.loop.original-resolution-basis.v1",
        OriginalResolutionBasis,
        "basis_id",
    ),
    RecoveryRecordRow(
        AGENT_LOOP_OWNER,
        "ORIGINAL_OBLIGATION_CLOSURE",
        "chiplog.loop.original-obligation-closure.v1",
        OriginalObligationClosureRecord,
        "closure_id",
    ),
    RecoveryRecordRow(
        AGENT_LOOP_OWNER,
        "RECOVERED_CALL_OUTCOME",
        "chiplog.loop.recovered-call-outcome.v1",
        RecoveredCallOutcomeRecord,
        "outcome_id",
    ),
    RecoveryRecordRow(
        AGENT_LOOP_OWNER,
        "ORIGINAL_RESOLVER_BATCH",
        "chiplog.loop.original-resolver-batch.v1",
        OriginalResolverBatchRecord,
        "batch_id",
    ),
    RecoveryRecordRow(
        AGENT_LOOP_OWNER,
        "LOOP_SEMANTIC_REDUCTION",
        "chiplog.loop.semantic-reduction.v1",
        LoopSemanticReductionRecord,
        None,
    ),
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
        "LATE_EXECUTION_RESPONSE",
        "chiplog.execution.late-response.v1",
        LateExecutionResponseRecord,
        "evidence_id",
    ),
)

_ROWS = {(row.owner, row.record_kind, row.schema_id): row for row in RECOVERY_RECORD_ROWS}


def _reference(member: DecodedRecoveryRecordMember) -> Present:
    return Present(head=member.member.record_id, fingerprint=member.member.fingerprint)


def _head(reference: CallSubjectHead) -> Present:
    return reference.revision


def _require(actual: object, expected: object, reason: str) -> None:
    if actual != expected:
        raise RecoveryRecordIntegrityError(reason)


def _content_id(schema_id: str, canonical_bytes: bytes) -> str:
    return schema_id + ":" + hashlib.sha256(canonical_bytes).hexdigest()


def decode_recovery_record_member(member: RecoveryRecordMember) -> DecodedRecoveryRecordMember:
    """Decode one exact registered member; arbitrary owner/schema callbacks are impossible."""
    row = _ROWS.get((member.owner, member.record_kind, member.schema_id))
    if row is None:
        raise RecoveryRecordIntegrityError("unregistered recovery record owner, kind, or schema")
    try:
        record = row.record_type.model_validate_json(member.canonical_record_bytes)
    except ValidationError as error:
        raise RecoveryRecordIntegrityError("invalid recovery record bytes") from error
    if record.canonical_bytes() != member.canonical_record_bytes:
        raise RecoveryRecordIntegrityError("recovery record bytes are not canonical")
    fingerprint = hashlib.sha256(member.canonical_record_bytes).hexdigest()
    _require(member.fingerprint, fingerprint, "recovery record fingerprint mismatch")
    embedded_schema = getattr(record, "schema_id", None)
    if embedded_schema is not None:
        _require(embedded_schema, member.schema_id, "embedded recovery schema mismatch")
    expected_id = (
        str(getattr(record, row.identity_field))
        if row.identity_field is not None
        else _content_id(row.schema_id, member.canonical_record_bytes)
    )
    _require(member.record_id, expected_id, "recovery record physical ID mismatch")
    return DecodedRecoveryRecordMember(member=member, record=record)


def validate_recovery_source_record(
    source: RecoverySourceRecord,
    physical: DecodedRecoveryRecordMember,
    selected_decision: CallSubjectHead,
) -> None:
    """Bind a source wrapper to a physical member without conflating its decision."""
    _require(source.owner, physical.member.owner, "source owner differs from physical member")
    _require(
        source.schema_id, physical.member.schema_id, "source schema differs from physical member"
    )
    _require(
        source.canonical_record_bytes, physical.member.canonical_record_bytes, "source bytes differ"
    )
    _require(_head(source.physical_record), _reference(physical), "source physical record differs")
    _require(source.selected_decision, selected_decision, "source selected decision differs")


def validate_selected_execution_suspension(
    selected: SelectedExecutionSuspension,
    baseline: DecodedRecoveryRecordMember,
    suspended_run: CallSubjectHead,
    pair: DecodedRecoveryRecordMember,
    selected_decision: CallSubjectHead,
) -> None:
    """Join baseline -> suspended Run -> pair, preserving selected bytes and decision."""
    if not isinstance(baseline.record, ExecutionSuspensionBaseline) or not isinstance(
        pair.record, ExecutionSuspensionPair
    ):
        raise RecoveryRecordIntegrityError("suspension members have wrong registered rows")
    _require(selected.baseline, baseline.record, "selected baseline body differs")
    _require(selected.pair, pair.record, "selected pair body differs")
    _require(_head(pair.record.baseline), _reference(baseline), "pair baseline differs")
    _require(_head(pair.record.suspended_run), _head(suspended_run), "pair suspended Run differs")
    _require(
        pair.record.suspension_command_id,
        baseline.record.suspension_command_id,
        "pair command differs",
    )
    _require(
        pair.record.source_cut_fingerprint,
        baseline.record.source_cut_fingerprint,
        "pair cut differs",
    )
    _require(
        pair.record.predecessor_run, baseline.record.predecessor_run, "pair predecessor differs"
    )
    _require(
        selected.canonical_pair_bytes,
        pair.member.canonical_record_bytes,
        "selected pair bytes differ",
    )
    _require(_head(selected.selected_pair), _reference(pair), "selected pair physical head differs")
    _require(selected.selected_decision, selected_decision, "selected decision differs")


def validate_accounting_continuation(
    accounting: DecodedRecoveryRecordMember,
    continuation: DecodedRecoveryRecordMember,
) -> None:
    """Bind the continuation's embedded accounting body to its selected member."""
    if not isinstance(accounting.record, SealedAccountingRecord) or not isinstance(
        continuation.record, ContinuationReadyRecord
    ):
        raise RecoveryRecordIntegrityError("accounting or continuation row mismatch")
    _require(
        continuation.record.accounting, accounting.record, "continuation accounting body differs"
    )


def validate_terminal_accounting_manifest(
    complete_accounting: tuple[DecodedRecoveryRecordMember, ...],
    terminal: DecodedRecoveryRecordMember,
) -> None:
    """Bind the full ordered terminal accounting manifest, never merely a member."""
    if not isinstance(terminal.record, ExecutionTerminalManifest):
        raise RecoveryRecordIntegrityError("terminal row mismatch")
    if any(not isinstance(value.record, SealedAccountingRecord) for value in complete_accounting):
        raise RecoveryRecordIntegrityError("terminal accounting has a non-accounting row")
    if len({value.member.record_id for value in complete_accounting}) != len(complete_accounting):
        raise RecoveryRecordIntegrityError("terminal accounting repeats a physical record")
    _require(
        tuple(value.record for value in complete_accounting),
        terminal.record.complete_accounting,
        "terminal accounting sequence differs",
    )


def validate_original_resolution(
    basis: DecodedRecoveryRecordMember,
    closure: DecodedRecoveryRecordMember,
    outcome: DecodedRecoveryRecordMember,
    batch: DecodedRecoveryRecordMember,
    reduction: DecodedRecoveryRecordMember,
    predecessor: Absent | Present,
    ordered_evidence: tuple[CallSubjectHead, ...],
) -> None:
    """Validate basis -> closure/outcome -> batch and a resolved reduction anchor."""
    if not (
        isinstance(basis.record, OriginalResolutionBasis)
        and isinstance(closure.record, OriginalObligationClosureRecord)
        and isinstance(outcome.record, RecoveredCallOutcomeRecord)
        and isinstance(batch.record, OriginalResolverBatchRecord)
        and isinstance(reduction.record, LoopSemanticReductionRecord)
    ):
        raise RecoveryRecordIntegrityError("original resolution members have wrong registered rows")
    basis_record = basis.record
    closure_record = closure.record
    outcome_record = outcome.record
    batch_record = batch.record
    reduction_record = reduction.record
    _require(_head(closure_record.basis), _reference(basis), "closure basis differs")
    _require(_head(outcome_record.basis), _reference(basis), "outcome basis differs")
    _require(_head(outcome_record.closure), _reference(closure), "outcome closure differs")
    _require(closure_record.original, basis_record.original, "closure original differs")
    _require(outcome_record.original, basis_record.original, "outcome original differs")
    _require(
        closure_record.accepted_witness, basis_record.selected_witness, "closure witness differs"
    )
    _require(
        outcome_record.accepted_witness, basis_record.selected_witness, "outcome witness differs"
    )
    _require(_head(batch_record.basis), _reference(basis), "batch basis differs")
    _require(_head(batch_record.closure), _reference(closure), "batch closure differs")
    _require(_head(batch_record.recovered_outcome), _reference(outcome), "batch outcome differs")
    _require(reduction_record.predecessor, predecessor, "reduction predecessor differs")
    _require(
        reduction_record.complete_ordered_evidence, ordered_evidence, "reduction evidence differs"
    )
    anchor = reduction_record.anchor
    if not isinstance(anchor, ResolvedReductionAnchor):
        raise RecoveryRecordIntegrityError("reduction must have a resolved anchor")
    _require(anchor.original, basis_record.original, "reduction anchor original differs")
    _require(
        anchor.accepted_witness, basis_record.selected_witness, "reduction anchor witness differs"
    )
    _require(_head(anchor.closure), _reference(closure), "reduction anchor closure differs")
    _require(
        _head(anchor.recovered_outcome), _reference(outcome), "reduction anchor outcome differs"
    )
    _require(_head(anchor.resolver_batch), _reference(batch), "reduction anchor batch differs")


def validate_readonly_reduction(
    accepted: DecodedRecoveryRecordMember,
    acceptance_counter: DecodedRecoveryRecordMember,
    outcome: DecodedRecoveryRecordMember,
    call_outcome: DecodedRecoveryRecordMember,
    closed_counter: DecodedRecoveryRecordMember,
    ordered_outcomes: tuple[DecodedRecoveryRecordMember, ...],
    terminal: CallSubjectHead,
) -> None:
    """Validate accepted -> counter/outcome -> call outcome with exact physical heads."""
    if not (
        isinstance(accepted.record, ReadOnlyAttemptAcceptedRecord)
        and isinstance(acceptance_counter.record, ReadOnlyCounterRecord)
        and isinstance(outcome.record, ReadOnlyAttemptOutcomeRecord)
        and isinstance(call_outcome.record, ReadOnlyCallOutcomeRecord)
        and isinstance(closed_counter.record, ReadOnlyCounterRecord)
    ):
        raise RecoveryRecordIntegrityError("read-only members have wrong registered rows")
    accepted_record = accepted.record
    acceptance_counter_record = acceptance_counter.record
    outcome_record = outcome.record
    call_record = call_outcome.record
    _require(
        _head(accepted_record.next_counter),
        _reference(acceptance_counter),
        "accepted next counter differs",
    )
    _require(_head(outcome_record.accepted), _reference(accepted), "outcome accepted differs")
    _require(
        outcome_record.original_call_id, accepted_record.original_call_id, "outcome call differs"
    )
    _require(outcome_record.lineage_id, accepted_record.lineage_id, "outcome lineage differs")
    _require(outcome_record.attempt_id, accepted_record.attempt_id, "outcome attempt differs")
    _require(outcome_record.ordinal, accepted_record.ordinal, "outcome ordinal differs")
    _require(
        acceptance_counter_record.original_call_id,
        accepted_record.original_call_id,
        "acceptance counter call differs",
    )
    _require(
        acceptance_counter_record.lineage_id,
        accepted_record.lineage_id,
        "acceptance counter lineage differs",
    )
    if acceptance_counter_record.closed:
        raise RecoveryRecordIntegrityError("accepted attempt requires an open counter")
    _require(
        _head(closed_counter.record.predecessor),
        _reference(acceptance_counter),
        "closed counter predecessor differs",
    )
    _require(
        closed_counter.record.attempts_consumed,
        acceptance_counter_record.attempts_consumed,
        "closed counter attempts differ",
    )
    if not ordered_outcomes or any(
        not isinstance(value.record, ReadOnlyAttemptOutcomeRecord) for value in ordered_outcomes
    ):
        raise RecoveryRecordIntegrityError("read-only outcomes are absent or have wrong rows")
    _require(
        tuple(_reference(value) for value in ordered_outcomes),
        tuple(_head(value) for value in call_record.complete_ordered_outcomes),
        "call outcome sequence differs",
    )
    for ordinal, value in enumerate(ordered_outcomes):
        if not isinstance(value.record, ReadOnlyAttemptOutcomeRecord):
            raise RecoveryRecordIntegrityError("read-only outcome row mismatch")
        _require(
            value.record.original_call_id, accepted_record.original_call_id, "outcome call differs"
        )
        _require(value.record.lineage_id, accepted_record.lineage_id, "outcome lineage differs")
        _require(value.record.ordinal, ordinal, "outcome order differs")
    _require(
        _head(call_record.selected_attempt_outcome),
        _reference(outcome),
        "call outcome selection differs",
    )
    if _reference(outcome) not in tuple(_reference(value) for value in ordered_outcomes):
        raise RecoveryRecordIntegrityError("call outcome omits attempt outcome")
    _require(
        _head(call_record.closed_counter),
        _reference(closed_counter),
        "call outcome counter differs",
    )
    _require(
        _head(call_record.terminal_disposition), _head(terminal), "call outcome terminal differs"
    )
    _require(
        call_record.original_call_id, accepted_record.original_call_id, "call outcome call differs"
    )
    _require(
        call_record.lineage.lineage_id, accepted_record.lineage_id, "call outcome lineage differs"
    )
    _require(
        closed_counter.record.original_call_id,
        accepted_record.original_call_id,
        "closed counter call differs",
    )
    _require(
        closed_counter.record.lineage_id,
        accepted_record.lineage_id,
        "closed counter lineage differs",
    )
    if not closed_counter.record.closed:
        raise RecoveryRecordIntegrityError("call outcome requires a closed counter")


def decode_successor_edge(member: RecoveryRecordMember) -> DecodedRecoveryRecordMember:
    """R2's narrow dependency: decode only the registered successor edge row."""
    decoded = decode_recovery_record_member(member)
    if not isinstance(decoded.record, ExecutionSuccessorEdge):
        raise RecoveryRecordIntegrityError("member is not a successor edge")
    return decoded
