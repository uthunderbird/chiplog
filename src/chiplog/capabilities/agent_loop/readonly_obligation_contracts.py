"""Fixed Stage-1 contracts for an original read-only recovery obligation.

This module owns a closed codec for its single new physical row.  It does not
make that row independently publishable: later terminal assembly owns that.
"""

from __future__ import annotations

import hashlib
from typing import Literal, Protocol

from pydantic import ValidationError

from .call_acceptance_contracts import (
    CallSubjectHead,
    InitializedCallRecord,
    InitializedReadOnlyLineage,
)
from .call_acceptance_preparation import call_record_reference, call_subject_id
from .original_recovery_contracts import (
    HeldReduction,
    LoopRecoveryStream,
    LoopSemanticReductionRecord,
    UnresolvedReductionAnchor,
)
from .readonly_execution_contracts import (
    ReadOnlyAttemptAcceptedRecord,
    ReadOnlyAttemptOutcomeRecord,
    ReadOnlyCounterRecord,
    ReadOnlyCurrentCut,
    ReadOnlyLineageSnapshot,
    ReadOnlyRecoveryRequired,
)
from .readonly_history_tool_contracts import ReadOnlyDTO
from .readonly_record_contracts import ReadOnlyPendingRecord, decode_readonly_record_member
from .readonly_source_contracts import (
    SelectedReadOnlyAcceptedRecoveryEvidenceV1,
    SelectedReadOnlyPendingV1,
    SelectedReadOnlyReducerSourceV1,
    readonly_lineage_manifest_fingerprint,
    validate_selected_readonly_accepted_recovery_evidence,
    validate_selected_readonly_pending,
    validate_selected_readonly_reducer_source,
)
from .recovery_contracts import Absent, Digest, Identity, OriginalObligationBinding, Present, UInt64
from .recovery_record_contracts import (
    AGENT_LOOP_OWNER,
    DecodedRecoveryRecordMember,
    RecoveryRecordIntegrityError,
    RecoveryRecordMember,
    RecoveryRecordRow,
)

ORIGINAL_READONLY_OBLIGATION_KIND = "ORIGINAL_READONLY_OBLIGATION"
ORIGINAL_READONLY_OBLIGATION_SCHEMA = "chiplog.loop.original-readonly-obligation.v1"


class OriginalReadOnlyObligationRecordV1(ReadOnlyDTO):
    schema_id: Literal["chiplog.loop.original-readonly-obligation.v1"] = (
        "chiplog.loop.original-readonly-obligation.v1"
    )
    state: Literal["OPEN"] = "OPEN"
    tenant_id: Identity
    database_id: Identity
    original_run_id: Identity
    original_call_id: Identity
    obligation_id: Identity
    obligation_stream_id: Identity
    initialized: CallSubjectHead
    accepted: CallSubjectHead
    lineage_id: Identity
    attempt_id: Identity
    ordinal: UInt64
    closure_predicate_id: Identity
    closure_predicate_version: Identity
    resolver_id: Identity
    resolver_version: Identity
    reducer_id: Identity
    reducer_version: Identity
    evidence_stream_id: Identity
    evidence_head: Absent


ORIGINAL_READONLY_OBLIGATION_ROW = RecoveryRecordRow(
    AGENT_LOOP_OWNER,
    ORIGINAL_READONLY_OBLIGATION_KIND,
    ORIGINAL_READONLY_OBLIGATION_SCHEMA,
    OriginalReadOnlyObligationRecordV1,
    None,
)


class SelectedReadOnlyAcceptedAttemptV1(ReadOnlyDTO):
    kind: Literal["SELECTED_READONLY_ACCEPTED_ATTEMPT_V1"] = "SELECTED_READONLY_ACCEPTED_ATTEMPT_V1"
    accepted: RecoveryRecordMember
    counter: RecoveryRecordMember
    selected_decision: CallSubjectHead
    pending: SelectedReadOnlyPendingV1


class ReadOnlyOriginalOpeningSpecV1(ReadOnlyDTO):
    kind: Literal["READONLY_ORIGINAL_OPENING_SPEC_V1"] = "READONLY_ORIGINAL_OPENING_SPEC_V1"
    obligation_id: Identity
    obligation_stream_id: Identity
    closure_predicate_id: Identity
    closure_predicate_version: Identity
    resolver_id: Identity
    resolver_version: Identity
    reducer_id: Identity
    reducer_version: Identity
    evidence_stream_id: Identity
    reduction_stream_id: Identity
    reduction_registry: CallSubjectHead


class PrepareReadOnlyRecoverySemanticReductionV1(ReadOnlyDTO):
    kind: Literal["PREPARE_READONLY_RECOVERY_SEMANTIC_REDUCTION_V1"] = (
        "PREPARE_READONLY_RECOVERY_SEMANTIC_REDUCTION_V1"
    )
    command_id: Identity
    opening: RecoveryRecordMember
    outcome: RecoveryRecordMember
    evidence: SelectedReadOnlyAcceptedRecoveryEvidenceV1
    opening_spec: ReadOnlyOriginalOpeningSpecV1
    cut: ReadOnlyCurrentCut


class PreparedReadOnlyRecoverySemanticReductionV1(ReadOnlyDTO):
    kind: Literal["PREPARED_READONLY_RECOVERY_SEMANTIC_REDUCTION_V1"] = (
        "PREPARED_READONLY_RECOVERY_SEMANTIC_REDUCTION_V1"
    )
    source_request_fingerprint: Digest
    record: LoopSemanticReductionRecord
    physical_fingerprint: Digest


class ReadOnlyRecoverySemanticReductionPreparationPortV1(Protocol):
    async def prepare_readonly_recovery_semantic_reduction(
        self, request: PrepareReadOnlyRecoverySemanticReductionV1
    ) -> PreparedReadOnlyRecoverySemanticReductionV1: ...


def _require(actual: object, expected: object, reason: str) -> None:
    if actual != expected:
        raise RecoveryRecordIntegrityError(reason)


def _member_reference(member: RecoveryRecordMember) -> Present:
    return Present(head=member.record_id, fingerprint=member.fingerprint)


def _native_record_reference(
    record: ReadOnlyAttemptAcceptedRecord | ReadOnlyAttemptOutcomeRecord,
) -> Present:
    raw = record.canonical_bytes()
    fingerprint = hashlib.sha256(raw).hexdigest()
    return Present(head=record.schema_id + ":" + fingerprint, fingerprint=fingerprint)


def make_original_readonly_obligation_member(
    record: OriginalReadOnlyObligationRecordV1,
) -> RecoveryRecordMember:
    """Make the one canonical, content-addressed physical original-obligation row."""
    raw = record.canonical_bytes()
    fingerprint = hashlib.sha256(raw).hexdigest()
    return RecoveryRecordMember(
        owner=ORIGINAL_READONLY_OBLIGATION_ROW.owner,
        record_kind=ORIGINAL_READONLY_OBLIGATION_ROW.record_kind,
        schema_id=ORIGINAL_READONLY_OBLIGATION_ROW.schema_id,
        record_id=ORIGINAL_READONLY_OBLIGATION_SCHEMA + ":" + fingerprint,
        canonical_record_bytes=raw,
        fingerprint=fingerprint,
    )


def decode_original_readonly_obligation_member(
    member: RecoveryRecordMember,
) -> DecodedRecoveryRecordMember:
    """Decode only the fixed original-obligation member and its original bytes."""
    row = ORIGINAL_READONLY_OBLIGATION_ROW
    if (member.owner, member.record_kind, member.schema_id) != (
        row.owner,
        row.record_kind,
        row.schema_id,
    ):
        raise RecoveryRecordIntegrityError("unregistered original read-only obligation row")
    try:
        record = OriginalReadOnlyObligationRecordV1.model_validate_json(
            member.canonical_record_bytes
        )
    except ValidationError as error:
        raise RecoveryRecordIntegrityError("invalid original read-only obligation bytes") from error
    if record.canonical_bytes() != member.canonical_record_bytes:
        raise RecoveryRecordIntegrityError("original read-only obligation bytes are not canonical")
    fingerprint = hashlib.sha256(member.canonical_record_bytes).hexdigest()
    _require(member.fingerprint, fingerprint, "original read-only obligation fingerprint mismatch")
    _require(
        member.record_id,
        row.schema_id + ":" + fingerprint,
        "original obligation physical ID mismatch",
    )
    return DecodedRecoveryRecordMember(member=member, record=record)


def original_readonly_obligation_binding(member: RecoveryRecordMember) -> OriginalObligationBinding:
    """Project the immutable original binding from the decoded physical row."""
    decoded = decode_original_readonly_obligation_member(member)
    record = decoded.record
    assert isinstance(record, OriginalReadOnlyObligationRecordV1)
    return OriginalObligationBinding(
        original_run_id=record.original_run_id,
        original_call_id=record.original_call_id,
        obligation_id=record.obligation_id,
        obligation_stream_id=record.obligation_stream_id,
        obligation_head=decoded.member.record_id,
        closure_predicate_id=record.closure_predicate_id,
        closure_predicate_version=record.closure_predicate_version,
        resolver_id=record.resolver_id,
        resolver_version=record.resolver_version,
        reducer_id=record.reducer_id,
        reducer_version=record.reducer_version,
        evidence_stream_id=record.evidence_stream_id,
        evidence_head=record.evidence_head,
    )


def original_readonly_obligation_logical_head(member: RecoveryRecordMember) -> CallSubjectHead:
    decoded = decode_original_readonly_obligation_member(member)
    record = decoded.record
    assert isinstance(record, OriginalReadOnlyObligationRecordV1)
    return CallSubjectHead(
        subject_id=record.obligation_id, revision=_member_reference(decoded.member)
    )


def _native_selected_attempt(
    value: SelectedReadOnlyAcceptedAttemptV1,
) -> SelectedReadOnlyAcceptedAttemptV1:
    try:
        return SelectedReadOnlyAcceptedAttemptV1.model_validate_json(value.model_dump_json())
    except (TypeError, ValueError, ValidationError) as error:
        raise RecoveryRecordIntegrityError(
            "invalid selected read-only accepted attempt wrapper"
        ) from error


def _native_snapshot(snapshot: ReadOnlyLineageSnapshot) -> ReadOnlyLineageSnapshot:
    try:
        return ReadOnlyLineageSnapshot.model_validate_json(snapshot.model_dump_json())
    except (TypeError, ValueError, ValidationError) as error:
        raise RecoveryRecordIntegrityError("invalid read-only lineage snapshot") from error


def _native_initialized(record: InitializedCallRecord) -> InitializedCallRecord:
    try:
        return InitializedCallRecord.model_validate_json(record.model_dump_json())
    except (TypeError, ValueError, ValidationError) as error:
        raise RecoveryRecordIntegrityError("invalid initialized read-only call") from error


def validate_selected_readonly_accepted_attempt(
    value: SelectedReadOnlyAcceptedAttemptV1,
    observed: ReadOnlyLineageSnapshot,
) -> ReadOnlyAttemptAcceptedRecord:
    """Join a selected unfinished acceptance to the exact current pending branch."""
    value = _native_selected_attempt(value)
    observed = _native_snapshot(observed)
    _require(
        observed.complete_manifest_fingerprint,
        readonly_lineage_manifest_fingerprint(observed),
        "observed lineage manifest differs",
    )
    _require(observed.call_outcome, Absent(), "observed call outcome is not absent")
    _require(observed.terminal, Absent(), "observed terminal is not absent")
    accepted_decoded = decode_readonly_record_member(value.accepted)
    counter_decoded = decode_readonly_record_member(value.counter)
    if not isinstance(accepted_decoded.record, ReadOnlyAttemptAcceptedRecord):
        raise RecoveryRecordIntegrityError("selected accepted row has wrong type")
    if not isinstance(counter_decoded.record, ReadOnlyCounterRecord):
        raise RecoveryRecordIntegrityError("selected counter row has wrong type")
    accepted = accepted_decoded.record
    counter = counter_decoded.record
    pending = validate_selected_readonly_pending(value.pending)
    if not isinstance(observed.pending, type(pending)):
        raise RecoveryRecordIntegrityError(
            "selected acceptance requires a current pending frontier"
        )
    _require(observed.pending, pending, "observed pending differs from selected pending")
    body_decoded = decode_readonly_record_member(value.pending.pending_body)
    if not isinstance(body_decoded.record, ReadOnlyPendingRecord):
        raise RecoveryRecordIntegrityError("selected pending body has wrong type")
    body = body_decoded.record
    _require(observed.lineage, body.lineage, "observed lineage differs from pending")
    _require(
        observed.shared_counter, accepted.next_counter, "observed counter differs from accepted"
    )
    _require(
        pending.counter, _member_reference(value.counter), "pending counter differs from member"
    )
    _require(
        accepted.next_counter.revision,
        _member_reference(value.counter),
        "accepted next counter differs",
    )
    _require(counter.closed, False, "selected counter is closed")
    _require(counter.original_call_id, body.original_call_id, "counter original call differs")
    _require(counter.lineage_id, body.lineage.lineage_id, "counter lineage differs")
    _require(accepted.original_call_id, body.original_call_id, "accepted original call differs")
    _require(accepted.lineage_id, body.lineage.lineage_id, "accepted lineage differs")
    _require(counter.predecessor, accepted.previous_counter, "counter predecessor differs")
    _require(counter.attempts_consumed, body.attempts_consumed, "counter count differs")
    _require(
        body.readonly_proof, accepted.proof.no_mutation_proof.revision, "pending proof differs"
    )
    _require(body.snapshot, accepted.proof.snapshot.revision, "pending snapshot differs")
    attempts = body.ordered_attempts
    if not attempts:
        raise RecoveryRecordIntegrityError("pending attempt history is empty")
    identities = tuple(attempt.attempt_id for attempt in attempts)
    ordinals = tuple(attempt.ordinal for attempt in attempts)
    if identities != tuple(dict.fromkeys(identities)) or ordinals != tuple(range(len(attempts))):
        raise RecoveryRecordIntegrityError("pending attempt history is not unique and ordered")
    _require(body.attempts_consumed, len(attempts), "pending attempts consumed differs")
    _require(
        observed.attempts_consumed, body.attempts_consumed, "observed attempts consumed differs"
    )
    _require(
        len(observed.complete_ordered_attempts), len(attempts), "observed attempt history differs"
    )
    final = attempts[-1]
    _require(final.ordinal, accepted.ordinal, "selected accepted ordinal differs")
    _require(final.attempt_id, accepted.attempt_id, "selected accepted attempt ID differs")
    _require(
        final.initialized, accepted.initialized.revision, "selected accepted initialized differs"
    )
    _require(
        final.accepted, _member_reference(value.accepted), "selected accepted physical head differs"
    )
    _require(final.outcome, Absent(), "selected accepted attempt is already finished")
    _require(body.next_ordinal, accepted.ordinal + 1, "pending next ordinal differs")
    expected_predecessor = Absent() if len(attempts) == 1 else attempts[-2].accepted
    _require(final.predecessor, expected_predecessor, "pending final predecessor differs")
    _require(accepted.predecessor_attempt, expected_predecessor, "accepted predecessor differs")
    for index, attempt in enumerate(attempts):
        observed_attempt = observed.complete_ordered_attempts[index]
        _require(attempt.ordinal, index, "pending attempt ordinal is discontinuous")
        expected = Absent() if index == 0 else attempts[index - 1].accepted
        _require(attempt.predecessor, expected, "pending attempt predecessor is discontinuous")
        _require(
            observed_attempt.accepted_head.subject_id,
            attempt.attempt_id,
            "observed attempt subject differs",
        )
        _require(
            observed_attempt.accepted_head.revision,
            attempt.accepted,
            "observed attempt physical head differs",
        )
        _require(
            observed_attempt.accepted_head.revision,
            _native_record_reference(observed_attempt.accepted),
            "observed attempt accepted is not native",
        )
        _require(
            observed_attempt.accepted.ordinal, attempt.ordinal, "observed attempt ordinal differs"
        )
        _require(
            observed_attempt.accepted.attempt_id, attempt.attempt_id, "observed attempt ID differs"
        )
        _require(
            observed_attempt.accepted.initialized.revision,
            attempt.initialized,
            "observed attempt initialized differs",
        )
        if index == len(attempts) - 1:
            _require(observed_attempt.accepted, accepted, "selected observed acceptance differs")
        observed_outcome = (
            Absent()
            if isinstance(observed_attempt.outcome, Absent)
            else observed_attempt.outcome.head.revision
        )
        _require(observed_outcome, attempt.outcome, "observed attempt outcome differs")
        if not isinstance(observed_attempt.outcome, Absent):
            outcome = observed_attempt.outcome
            _require(
                outcome.head.subject_id,
                outcome.record.attempt_id,
                "observed outcome subject differs",
            )
            _require(
                outcome.head.revision,
                _native_record_reference(outcome.record),
                "observed attempt outcome is not native",
            )
    initialized = _native_initialized(observed.initialized)
    expected_initialized = call_record_reference(initialized.original_call_id, initialized)
    _require(
        observed.initialized_head, expected_initialized, "observed initialized head is not native"
    )
    _require(accepted.initialized, expected_initialized, "accepted initialized head is not native")
    _require(
        initialized.original_call_id,
        call_subject_id(initialized.call.original),
        "initialized call ID differs",
    )
    _require(initialized.call.classification, "READ_ONLY", "initialized call is not read-only")
    if not isinstance(initialized.call.retry_lineage, InitializedReadOnlyLineage):
        raise RecoveryRecordIntegrityError("initialized call has no frozen read-only lineage")
    _require(initialized.call.retry_lineage.lineage, body.lineage, "frozen lineage differs")
    _require(
        initialized.call.retry_lineage.lineage.original_call_id,
        body.original_call_id,
        "frozen lineage call differs",
    )
    _require(observed.initialized_head.revision, body.initialized, "pending initialized differs")
    return accepted


def prepare_readonly_original_opening(
    spec: ReadOnlyOriginalOpeningSpecV1,
    selected: SelectedReadOnlyAcceptedAttemptV1,
    observed: ReadOnlyLineageSnapshot,
    evidence: SelectedReadOnlyAcceptedRecoveryEvidenceV1,
    reducer: SelectedReadOnlyReducerSourceV1,
    cut: ReadOnlyCurrentCut,
) -> RecoveryRecordMember:
    """Construct role 1 only as an unselected input to a later terminal batch."""
    accepted = validate_selected_readonly_accepted_attempt(selected, observed)
    _require(cut.tenant_id, observed.initialized.call.original.tenant_id, "opening tenant differs")
    validate_selected_readonly_accepted_recovery_evidence(
        evidence, selected.accepted, accepted.proof, cut
    )
    reducer_body = validate_selected_readonly_reducer_source(reducer, observed.lineage, cut)
    _require(spec.reducer_id, observed.lineage.reducer_id, "opening reducer ID differs")
    _require(
        spec.reducer_version, observed.lineage.reducer_version, "opening reducer version differs"
    )
    _require(spec.reducer_id, reducer_body.reducer_id, "opening reducer source ID differs")
    _require(
        spec.reducer_version, reducer_body.reducer_version, "opening reducer source version differs"
    )
    _require(spec.reduction_registry, reducer_body.registry, "opening reduction registry differs")
    return make_original_readonly_obligation_member(
        OriginalReadOnlyObligationRecordV1(
            tenant_id=cut.tenant_id,
            database_id=cut.database_id,
            original_run_id=observed.initialized.call.original.original_run_id,
            original_call_id=accepted.original_call_id,
            obligation_id=spec.obligation_id,
            obligation_stream_id=spec.obligation_stream_id,
            initialized=accepted.initialized,
            accepted=CallSubjectHead(
                subject_id=accepted.attempt_id, revision=_member_reference(selected.accepted)
            ),
            lineage_id=accepted.lineage_id,
            attempt_id=accepted.attempt_id,
            ordinal=accepted.ordinal,
            closure_predicate_id=spec.closure_predicate_id,
            closure_predicate_version=spec.closure_predicate_version,
            resolver_id=spec.resolver_id,
            resolver_version=spec.resolver_version,
            reducer_id=spec.reducer_id,
            reducer_version=spec.reducer_version,
            evidence_stream_id=spec.evidence_stream_id,
            evidence_head=Absent(),
        )
    )


def make_readonly_recovery_required_outcome_member(
    opening: RecoveryRecordMember,
    evidence: SelectedReadOnlyAcceptedRecoveryEvidenceV1,
) -> RecoveryRecordMember:
    """Construct unselected role 2 from exact role 1 and prior broker evidence."""
    original = decode_original_readonly_obligation_member(opening).record
    assert isinstance(original, OriginalReadOnlyObligationRecordV1)
    binding = original_readonly_obligation_binding(opening)
    outcome = ReadOnlyAttemptOutcomeRecord(
        original_call_id=original.original_call_id,
        lineage_id=original.lineage_id,
        attempt_id=original.attempt_id,
        ordinal=original.ordinal,
        accepted=original.accepted,
        source_evidence=evidence.source.physical_record,
        disposition=ReadOnlyRecoveryRequired(obligation=binding, result=Absent()),
    )
    raw = outcome.canonical_bytes()
    fingerprint = hashlib.sha256(raw).hexdigest()
    return RecoveryRecordMember(
        owner=AGENT_LOOP_OWNER,
        record_kind="READONLY_ATTEMPT_OUTCOME",
        schema_id="chiplog.readonly.attempt-outcome.v1",
        record_id="chiplog.readonly.attempt-outcome.v1:" + fingerprint,
        canonical_record_bytes=raw,
        fingerprint=fingerprint,
    )


def _semantic_member(record: LoopSemanticReductionRecord) -> RecoveryRecordMember:
    raw = record.canonical_bytes()
    fingerprint = hashlib.sha256(raw).hexdigest()
    return RecoveryRecordMember(
        owner=AGENT_LOOP_OWNER,
        record_kind="LOOP_SEMANTIC_REDUCTION",
        schema_id="chiplog.loop.semantic-reduction.v1",
        record_id="chiplog.loop.semantic-reduction.v1:" + fingerprint,
        canonical_record_bytes=raw,
        fingerprint=fingerprint,
    )


def prepare_readonly_recovery_semantic_reduction(
    request: PrepareReadOnlyRecoverySemanticReductionV1,
) -> PreparedReadOnlyRecoverySemanticReductionV1:
    """Prepare unselected role 3 from its exact two preceding staged roles."""
    try:
        request = PrepareReadOnlyRecoverySemanticReductionV1.model_validate_json(
            request.model_dump_json()
        )
    except (TypeError, ValueError, ValidationError) as error:
        raise RecoveryRecordIntegrityError("invalid staged semantic reduction request") from error
    opening = decode_original_readonly_obligation_member(request.opening)
    original = opening.record
    assert isinstance(original, OriginalReadOnlyObligationRecordV1)
    outcome = decode_readonly_record_member(request.outcome)
    if not isinstance(outcome.record, ReadOnlyAttemptOutcomeRecord):
        raise RecoveryRecordIntegrityError("staged semantic reduction outcome has wrong type")
    body = outcome.record
    _require(body.original_call_id, original.original_call_id, "staged outcome call differs")
    _require(body.lineage_id, original.lineage_id, "staged outcome lineage differs")
    _require(body.attempt_id, original.attempt_id, "staged outcome attempt differs")
    _require(body.ordinal, original.ordinal, "staged outcome ordinal differs")
    _require(body.accepted, original.accepted, "staged outcome accepted differs")
    _require(
        body.source_evidence,
        request.evidence.source.physical_record,
        "staged outcome evidence differs",
    )
    if not isinstance(body.disposition, ReadOnlyRecoveryRequired):
        raise RecoveryRecordIntegrityError("staged outcome is not recovery required")
    _require(
        body.disposition.obligation,
        original_readonly_obligation_binding(request.opening),
        "staged outcome obligation differs",
    )
    _require(body.disposition.result, Absent(), "staged outcome fabricates a result")
    _require(request.opening_spec.reducer_id, original.reducer_id, "staged spec reducer differs")
    _require(
        request.opening_spec.reducer_version,
        original.reducer_version,
        "staged spec version differs",
    )
    stream = LoopRecoveryStream(
        stream_id=request.opening_spec.reduction_stream_id,
        registry=request.opening_spec.reduction_registry,
        subject=original_readonly_obligation_logical_head(request.opening),
        schema_id="chiplog.loop.semantic-reduction.v1",
    )
    record = LoopSemanticReductionRecord(
        stream=stream,
        reduction_id=request.command_id,
        reducer_id=original.reducer_id,
        reducer_version=original.reducer_version,
        predecessor=Absent(),
        complete_ordered_evidence=(request.evidence.source.physical_record,),
        anchor=UnresolvedReductionAnchor(
            original=original_readonly_obligation_binding(request.opening),
            closure=Absent(),
            recovered_outcome=Absent(),
        ),
        disposition=HeldReduction(reason="INCOMPLETE", conflicting_or_unclassified_evidence=()),
    )
    member = _semantic_member(record)
    return PreparedReadOnlyRecoverySemanticReductionV1(
        source_request_fingerprint=hashlib.sha256(request.canonical_bytes()).hexdigest(),
        record=record,
        physical_fingerprint=member.fingerprint,
    )


def validate_prepared_readonly_recovery_semantic_reduction(
    request: PrepareReadOnlyRecoverySemanticReductionV1,
    result: PreparedReadOnlyRecoverySemanticReductionV1,
) -> LoopSemanticReductionRecord:
    """Require the retained staged result to be the exact role-3 construction."""
    try:
        result = PreparedReadOnlyRecoverySemanticReductionV1.model_validate_json(
            result.model_dump_json()
        )
    except (TypeError, ValueError, ValidationError) as error:
        raise RecoveryRecordIntegrityError("invalid staged semantic reduction result") from error
    expected = prepare_readonly_recovery_semantic_reduction(request)
    _require(
        result.source_request_fingerprint,
        expected.source_request_fingerprint,
        "staged semantic reduction request fingerprint differs",
    )
    _require(result.record, expected.record, "staged semantic reduction record differs")
    _require(
        result.physical_fingerprint,
        expected.physical_fingerprint,
        "staged semantic reduction physical fingerprint differs",
    )
    return result.record
