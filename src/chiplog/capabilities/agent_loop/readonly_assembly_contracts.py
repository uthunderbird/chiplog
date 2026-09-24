"""Pure, bounded assemblies for ordinary read-only attempts and outcomes.

This is deliberately a consumer of retained physical rows.  It proves their
representation and joins, but does not authenticate selection or publish them.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Literal, cast

from pydantic import ValidationError

from .call_acceptance_contracts import (
    CallPreparationRejected,
    CallSubjectHead,
    InitializedReadOnlyLineage,
)
from .call_acceptance_preparation import call_record_reference, call_subject_id
from .execution_run_record_contracts import ExecutionRunCanonicalMember, decode_execution_run_member
from .readonly_execution_contracts import (
    PreparedReadOnlyAttempt,
    PreparedReadOnlyOutcome,
    PrepareReadOnlyAttempt,
    PrepareReadOnlyOutcome,
    ReadOnlyAttemptAcceptedRecord,
    ReadOnlyAttemptOutcomeRecord,
    ReadOnlyCounterRecord,
    ReadOnlyLineageSnapshot,
    ReadOnlyPendingTransition,
)
from .readonly_history_tool_contracts import ReadOnlyDTO
from .readonly_record_contracts import (
    ReadOnlyPendingRecord,
    decode_readonly_record_member,
    pending_frontier_from_body,
)
from .readonly_source_contracts import (
    ReadOnlyAttemptEvidenceBodyV1,
    SelectedReadOnlyAttemptEvidenceV1,
    SelectedReadOnlySuccessorInitializationV1,
    readonly_lineage_manifest_fingerprint,
    readonly_physical_batch_fingerprint,
    validate_selected_readonly_pending,
    validate_selected_readonly_successor_initialization,
)
from .recovery_contracts import Absent, Present
from .recovery_frontier_contracts import PendingCallFrontier
from .recovery_record_contracts import RecoveryRecordIntegrityError, RecoveryRecordMember


class SelectedReadOnlyCounterV1(ReadOnlyDTO):
    kind: Literal["SELECTED_READONLY_COUNTER_V1"] = "SELECTED_READONLY_COUNTER_V1"
    member: RecoveryRecordMember
    selected_decision: CallSubjectHead


class SameRunReadOnlyAttemptAssemblyV1(ReadOnlyDTO):
    kind: Literal["SAME_RUN_READONLY_ATTEMPT_ASSEMBLY_V1"] = "SAME_RUN_READONLY_ATTEMPT_ASSEMBLY_V1"
    request: PrepareReadOnlyAttempt
    selected_counter: SelectedReadOnlyCounterV1
    result: PreparedReadOnlyAttempt | CallPreparationRejected
    members: tuple[RecoveryRecordMember, ...]


class SuccessorPendingReadOnlyAttemptAssemblyV1(ReadOnlyDTO):
    kind: Literal["SUCCESSOR_PENDING_READONLY_ATTEMPT_ASSEMBLY_V1"] = (
        "SUCCESSOR_PENDING_READONLY_ATTEMPT_ASSEMBLY_V1"
    )
    request: PrepareReadOnlyAttempt
    selected_counter: SelectedReadOnlyCounterV1
    successor: SelectedReadOnlySuccessorInitializationV1
    result: PreparedReadOnlyAttempt | CallPreparationRejected
    members: tuple[RecoveryRecordMember, ...]


class ReadOnlyOutcomeAssemblyV1(ReadOnlyDTO):
    kind: Literal["READONLY_OUTCOME_ASSEMBLY_V1"] = "READONLY_OUTCOME_ASSEMBLY_V1"
    request: PrepareReadOnlyOutcome
    result: PreparedReadOnlyOutcome | CallPreparationRejected
    accepted: RecoveryRecordMember
    accepted_selected_decision: CallSubjectHead
    evidence: SelectedReadOnlyAttemptEvidenceV1
    members: tuple[RecoveryRecordMember, ...]


@dataclass(frozen=True)
class DecodedReadOnlyAttemptAssembly:
    counter: ReadOnlyCounterRecord | None
    accepted: ReadOnlyAttemptAcceptedRecord | None
    pending: PendingCallFrontier | None


@dataclass(frozen=True)
class DecodedReadOnlyOutcomeAssembly:
    outcome: ReadOnlyAttemptOutcomeRecord | None
    evidence: ReadOnlyAttemptEvidenceBodyV1 | None


def _require(actual: object, expected: object, reason: str) -> None:
    if actual != expected:
        raise RecoveryRecordIntegrityError(reason)


def _member_ref(member: RecoveryRecordMember) -> Present:
    return Present(head=member.record_id, fingerprint=member.fingerprint)


def _logical_ref(member: RecoveryRecordMember, subject_id: str) -> CallSubjectHead:
    return CallSubjectHead(subject_id=subject_id, revision=_member_ref(member))


def _reparse(value: ReadOnlyDTO, type_: type[ReadOnlyDTO], label: str) -> ReadOnlyDTO:
    try:
        parsed = type_.model_validate_json(value.canonical_bytes())
    except (TypeError, ValueError, ValidationError) as error:
        raise RecoveryRecordIntegrityError(f"invalid {label} bytes") from error
    _require(parsed.canonical_bytes(), value.canonical_bytes(), f"{label} bytes are not canonical")
    return parsed


def _native_active_run(request: PrepareReadOnlyAttempt) -> Any:
    run = request.run
    raw = run.canonical_bytes()
    try:
        decoded = decode_execution_run_member(
            ExecutionRunCanonicalMember(
                record_id=run.head,
                schema_id=run.schema_id,
                canonical_record_bytes=raw,
                fingerprint=hashlib.sha256(raw).hexdigest(),
            )
        )
    except (TypeError, ValueError, ValidationError) as error:
        raise RecoveryRecordIntegrityError("attempt Run is not native") from error
    _require(decoded.run, run, "attempt Run differs from native bytes")
    _require(run.state, "ACTIVE", "attempt Run is not ACTIVE")
    _require(run.tenant, request.cut.tenant_id, "attempt Run tenant differs from cut")
    return run


def _native_attempt_request(request: PrepareReadOnlyAttempt) -> PrepareReadOnlyAttempt:
    return _reparse(request, PrepareReadOnlyAttempt, "read-only attempt request")  # type: ignore[return-value]


def _native_outcome_request(request: PrepareReadOnlyOutcome) -> PrepareReadOnlyOutcome:
    return _reparse(request, PrepareReadOnlyOutcome, "read-only outcome request")  # type: ignore[return-value]


def _validate_snapshot(snapshot: ReadOnlyLineageSnapshot) -> None:
    _require(
        snapshot.complete_manifest_fingerprint,
        readonly_lineage_manifest_fingerprint(snapshot),
        "observed lineage manifest differs",
    )
    _require(snapshot.call_outcome, Absent(), "observed call outcome is not absent")
    _require(snapshot.terminal, Absent(), "observed terminal is not absent")
    initialized = snapshot.initialized
    _require(
        snapshot.initialized_head,
        call_record_reference(initialized.original_call_id, initialized),
        "observed initialized head is not native",
    )
    _require(
        initialized.original_call_id,
        call_subject_id(initialized.call.original),
        "initialized original call differs",
    )
    _require(initialized.call.classification, "READ_ONLY", "initialized call is not read-only")
    if not isinstance(initialized.call.retry_lineage, InitializedReadOnlyLineage):
        raise RecoveryRecordIntegrityError("initialized call has no frozen read-only lineage")
    _require(
        initialized.call.retry_lineage.lineage, snapshot.lineage, "frozen read-only lineage differs"
    )
    _require(
        snapshot.attempts_consumed,
        len(snapshot.complete_ordered_attempts),
        "observed attempts consumed differs",
    )
    for ordinal, observed in enumerate(snapshot.complete_ordered_attempts):
        accepted = observed.accepted
        _require(
            observed.accepted_head.subject_id,
            accepted.attempt_id,
            "observed accepted subject differs",
        )
        raw = accepted.canonical_bytes()
        _require(
            observed.accepted_head.revision,
            Present(
                head=accepted.schema_id + ":" + hashlib.sha256(raw).hexdigest(),
                fingerprint=hashlib.sha256(raw).hexdigest(),
            ),
            "observed accepted revision is not native",
        )
        _require(accepted.ordinal, ordinal, "observed accepted ordinal differs")
        expected_previous = (
            Absent()
            if ordinal == 0
            else snapshot.complete_ordered_attempts[ordinal - 1].accepted_head.revision
        )
        _require(
            accepted.predecessor_attempt, expected_previous, "observed accepted predecessor differs"
        )


def _validate_selected_counter(
    selected: SelectedReadOnlyCounterV1, snapshot: ReadOnlyLineageSnapshot
) -> ReadOnlyCounterRecord:
    selected = _reparse(selected, SelectedReadOnlyCounterV1, "selected counter")  # type: ignore[assignment]
    decoded = decode_readonly_record_member(selected.member)
    if not isinstance(decoded.record, ReadOnlyCounterRecord):
        raise RecoveryRecordIntegrityError("selected counter has wrong type")
    counter = decoded.record
    _require(counter.closed, False, "selected counter is closed")
    _require(
        counter.original_call_id,
        snapshot.lineage.original_call_id,
        "selected counter original call differs",
    )
    _require(counter.lineage_id, snapshot.lineage.lineage_id, "selected counter lineage differs")
    _require(
        counter.attempts_consumed, snapshot.attempts_consumed, "selected counter count differs"
    )
    _require(
        snapshot.shared_counter.revision,
        _member_ref(selected.member),
        "observed counter physical revision differs",
    )
    return counter


def _validate_attempt_core(
    request: PrepareReadOnlyAttempt, selected: SelectedReadOnlyCounterV1
) -> tuple[PrepareReadOnlyAttempt, ReadOnlyCounterRecord]:
    request = _native_attempt_request(request)
    _native_active_run(request)
    _validate_snapshot(request.observed)
    counter = _validate_selected_counter(selected, request.observed)
    if request.observed.attempts_consumed >= request.observed.lineage.max_attempts:
        raise RecoveryRecordIntegrityError("read-only retry bound exhausted")
    return request, counter


def _validate_result_common(
    request: ReadOnlyDTO,
    result: object,
    members: tuple[RecoveryRecordMember, ...],
    expected: type[ReadOnlyDTO],
) -> object:
    if not isinstance(result, ReadOnlyDTO | CallPreparationRejected):
        raise RecoveryRecordIntegrityError("invalid read-only assembly result")
    result = _reparse(
        cast(ReadOnlyDTO, result),
        cast(type[ReadOnlyDTO], type(result)),
        "read-only assembly result",
    )
    if isinstance(result, CallPreparationRejected):
        command_id = cast(Any, request).command_id
        _require(result.command_id, command_id, "rejection command differs")
        _require(members, (), "rejection must not carry output members")
        return result
    if not isinstance(result, expected):
        raise RecoveryRecordIntegrityError("result kind is incompatible with request")
    typed = cast(Any, result)
    _require(
        typed.source_request_fingerprint,
        hashlib.sha256(request.canonical_bytes()).hexdigest(),
        "result request fingerprint differs",
    )
    _require(
        typed.complete_batch_fingerprint,
        readonly_physical_batch_fingerprint(members),
        "result batch fingerprint differs",
    )
    return result


def _validate_new_attempt_rows(
    request: PrepareReadOnlyAttempt,
    selected: SelectedReadOnlyCounterV1,
    members: tuple[RecoveryRecordMember, ...],
) -> tuple[ReadOnlyCounterRecord, ReadOnlyAttemptAcceptedRecord]:
    counter_decoded, accepted_decoded = (decode_readonly_record_member(m) for m in members[:2])
    if not isinstance(counter_decoded.record, ReadOnlyCounterRecord) or not isinstance(
        accepted_decoded.record, ReadOnlyAttemptAcceptedRecord
    ):
        raise RecoveryRecordIntegrityError("attempt output rows have wrong types")
    counter, accepted = counter_decoded.record, accepted_decoded.record
    observed = request.observed
    subject = observed.shared_counter.subject_id
    _require(
        counter.original_call_id,
        observed.lineage.original_call_id,
        "new counter original call differs",
    )
    _require(counter.lineage_id, observed.lineage.lineage_id, "new counter lineage differs")
    _require(counter.predecessor, observed.shared_counter, "new counter predecessor differs")
    _require(counter.attempts_consumed, observed.attempts_consumed + 1, "new counter count differs")
    _require(counter.closed, False, "new counter is closed")
    _require(
        accepted.original_call_id,
        observed.lineage.original_call_id,
        "accepted original call differs",
    )
    _require(accepted.lineage_id, observed.lineage.lineage_id, "accepted lineage differs")
    _require(accepted.initialized, observed.initialized_head, "accepted initialized differs")
    _require(
        accepted.previous_counter, observed.shared_counter, "accepted previous counter differs"
    )
    _require(
        accepted.next_counter, _logical_ref(members[0], subject), "accepted next counter differs"
    )
    _require(
        accepted.active_run,
        CallSubjectHead(
            subject_id=request.run.run_id,
            revision=Present(head=request.run.head, fingerprint=request.run.digest()),
        ),
        "accepted active Run differs",
    )
    _require(accepted.proof, request.proof, "accepted proof differs")
    _require(accepted.fence, request.fence, "accepted fence differs")
    _require(accepted.ordinal, len(observed.complete_ordered_attempts), "accepted ordinal differs")
    _require(
        accepted.attempt_id
        not in tuple(item.accepted.attempt_id for item in observed.complete_ordered_attempts),
        True,
        "accepted attempt ID is duplicated",
    )
    expected_previous = (
        Absent()
        if not observed.complete_ordered_attempts
        else observed.complete_ordered_attempts[-1].accepted_head.revision
    )
    _require(accepted.predecessor_attempt, expected_previous, "accepted predecessor differs")
    return counter, accepted


def validate_same_run_readonly_attempt(
    value: SameRunReadOnlyAttemptAssemblyV1,
) -> DecodedReadOnlyAttemptAssembly:
    value = _reparse(value, SameRunReadOnlyAttemptAssemblyV1, "same-run attempt assembly")  # type: ignore[assignment]
    request, _old = _validate_attempt_core(value.request, value.selected_counter)
    _require(request.route.kind, "SAME_RUN", "same-run assembly has successor route")
    _require(request.observed.pending, Absent(), "same-run observed pending is not absent")
    result = _validate_result_common(request, value.result, value.members, PreparedReadOnlyAttempt)
    if isinstance(result, CallPreparationRejected):
        return DecodedReadOnlyAttemptAssembly(None, None, None)
    assert isinstance(result, PreparedReadOnlyAttempt)
    _require(len(value.members), 2, "same-run output must contain two rows")
    _require(
        tuple(m.record_kind for m in value.members),
        ("READONLY_COUNTER", "READONLY_ATTEMPT_ACCEPTED"),
        "same-run output role order differs",
    )
    counter, accepted = _validate_new_attempt_rows(request, value.selected_counter, value.members)
    _require(result.counter, counter, "same-run result counter differs")
    _require(result.acceptance, accepted, "same-run result acceptance differs")
    _require(result.pending_transition, None, "same-run result has pending transition")
    return DecodedReadOnlyAttemptAssembly(counter, accepted, None)


def validate_successor_pending_readonly_attempt(
    value: SuccessorPendingReadOnlyAttemptAssemblyV1,
) -> DecodedReadOnlyAttemptAssembly:
    value = _reparse(value, SuccessorPendingReadOnlyAttemptAssemblyV1, "successor attempt assembly")  # type: ignore[assignment]
    request, _old = _validate_attempt_core(value.request, value.selected_counter)
    _require(request.route.kind, "SUCCESSOR_PENDING", "successor assembly has same-run route")
    old_pending = validate_selected_readonly_pending(value.successor.original_pending)
    _require(request.observed.pending, old_pending, "successor observed pending differs")
    validate_selected_readonly_successor_initialization(value.successor, request)
    result = _validate_result_common(request, value.result, value.members, PreparedReadOnlyAttempt)
    if isinstance(result, CallPreparationRejected):
        return DecodedReadOnlyAttemptAssembly(None, None, None)
    assert isinstance(result, PreparedReadOnlyAttempt)
    _require(len(value.members), 5, "successor output must contain five rows")
    _require(
        tuple(m.record_kind for m in value.members),
        (
            "READONLY_COUNTER",
            "READONLY_ATTEMPT_ACCEPTED",
            "READONLY_PENDING",
            "READONLY_PENDING_FRONTIER",
            "READONLY_PENDING_TRANSITION",
        ),
        "successor output role order differs",
    )
    counter, accepted = _validate_new_attempt_rows(request, value.selected_counter, value.members)
    body_decoded, frontier_decoded = (
        decode_readonly_record_member(value.members[2]),
        decode_readonly_record_member(value.members[3]),
    )
    if not isinstance(body_decoded.record, ReadOnlyPendingRecord) or not isinstance(
        frontier_decoded.record, PendingCallFrontier
    ):
        raise RecoveryRecordIntegrityError("successor pending rows have wrong types")
    pending = frontier_decoded.record
    _require(
        pending_frontier_from_body(value.members[2]),
        pending,
        "successor pending projection differs",
    )
    _require(pending.counter, _member_ref(value.members[0]), "successor pending counter differs")
    _require(
        pending.attempts_consumed, counter.attempts_consumed, "successor pending count differs"
    )
    _require(
        pending.ordered_attempts[-1].attempt_id,
        accepted.attempt_id,
        "successor pending final attempt differs",
    )
    _require(
        pending.ordered_attempts[-1].accepted,
        _member_ref(value.members[1]),
        "successor pending accepted ref differs",
    )
    _require(
        pending.ordered_attempts[-1].outcome, Absent(), "successor pending final outcome is present"
    )
    transition = decode_readonly_record_member(value.members[4]).record
    if not isinstance(transition, ReadOnlyPendingTransition):
        raise RecoveryRecordIntegrityError("successor transition row has wrong type")
    _require(result.counter, counter, "successor result counter differs")
    _require(result.acceptance, accepted, "successor result acceptance differs")
    _require(result.pending_transition, transition, "successor pending transition differs")
    _require(transition.previous, old_pending, "successor transition previous differs")
    _require(transition.closure, "NEXT_ATTEMPT_ACCEPTED", "successor transition closure differs")
    _require(transition.replacement, pending, "successor transition replacement differs")
    return DecodedReadOnlyAttemptAssembly(counter, accepted, pending)


def validate_selected_readonly_outcome_evidence(
    value: SelectedReadOnlyAttemptEvidenceV1,
    request: PrepareReadOnlyOutcome,
    accepted_member: RecoveryRecordMember,
) -> ReadOnlyAttemptEvidenceBodyV1:
    value = _reparse(value, SelectedReadOnlyAttemptEvidenceV1, "selected outcome evidence")  # type: ignore[assignment]
    request = _native_outcome_request(request)
    try:
        body = ReadOnlyAttemptEvidenceBodyV1.model_validate_json(
            value.source.canonical_record_bytes
        )
    except ValidationError as error:
        raise RecoveryRecordIntegrityError("invalid outcome evidence bytes") from error
    _require(
        body.canonical_bytes(),
        value.source.canonical_record_bytes,
        "outcome evidence bytes are not canonical",
    )
    _require(body, value.body, "outcome evidence body differs")
    raw = body.canonical_bytes()
    fingerprint = hashlib.sha256(raw).hexdigest()
    physical = CallSubjectHead(
        subject_id=body.attempt_id,
        revision=Present(
            head=body.schema_id + ":" + fingerprint,
            fingerprint=fingerprint,
        ),
    )
    _require(value.source.owner, "broker", "outcome evidence owner differs")
    _require(value.source.schema_id, body.schema_id, "outcome evidence schema differs")
    _require(value.source.subject, physical, "outcome evidence subject differs")
    _require(value.source.physical_record, physical, "outcome evidence physical reference differs")
    if value.source.selected_decision == physical:
        raise RecoveryRecordIntegrityError(
            "outcome evidence selection cannot stand in for evidence"
        )
    decoded = decode_readonly_record_member(accepted_member)
    if not isinstance(decoded.record, ReadOnlyAttemptAcceptedRecord):
        raise RecoveryRecordIntegrityError("outcome accepted member has wrong type")
    accepted = decoded.record
    accepted_ref = _logical_ref(accepted_member, accepted.attempt_id)
    _require(request.accepted_attempt, accepted_ref, "outcome request accepted reference differs")
    _require(request.source_evidence, physical, "outcome request evidence differs")
    _require(request.evidence_schema, body.schema_id, "outcome request evidence schema differs")
    _require(request.canonical_evidence_bytes, raw, "outcome request evidence bytes differ")
    _require(body.tenant_id, request.cut.tenant_id, "outcome evidence tenant differs")
    _require(body.database_id, request.cut.database_id, "outcome evidence database differs")
    for field in ("original_call_id", "lineage_id", "attempt_id", "ordinal"):
        _require(
            getattr(body, field), getattr(accepted, field), f"outcome evidence {field} differs"
        )
    _require(body.query_identity, accepted.proof.query_identity, "outcome evidence query differs")
    _require(body.snapshot, accepted.proof.snapshot, "outcome evidence snapshot differs")
    _require(
        body.implementation,
        accepted.proof.implementation,
        "outcome evidence implementation differs",
    )
    _require(body.proof, accepted.proof.no_mutation_proof, "outcome evidence proof differs")
    matches = tuple(
        item
        for item in request.observed.complete_ordered_attempts
        if item.accepted_head == accepted_ref and item.accepted == accepted
    )
    _require(len(matches), 1, "outcome accepted member is not exactly selected in observed history")
    _require(matches[0].outcome, Absent(), "outcome accepted attempt is already finished")
    return body


def validate_readonly_outcome(value: ReadOnlyOutcomeAssemblyV1) -> DecodedReadOnlyOutcomeAssembly:
    value = _reparse(value, ReadOnlyOutcomeAssemblyV1, "outcome assembly")  # type: ignore[assignment]
    request = _native_outcome_request(value.request)
    _validate_snapshot(request.observed)
    evidence = validate_selected_readonly_outcome_evidence(value.evidence, request, value.accepted)
    result = _validate_result_common(request, value.result, value.members, PreparedReadOnlyOutcome)
    if isinstance(result, CallPreparationRejected):
        return DecodedReadOnlyOutcomeAssembly(None, None)
    assert isinstance(result, PreparedReadOnlyOutcome)
    _require(len(value.members), 1, "outcome output must contain one row")
    _require(
        value.members[0].record_kind, "READONLY_ATTEMPT_OUTCOME", "outcome output role differs"
    )
    decoded = decode_readonly_record_member(value.members[0])
    if not isinstance(decoded.record, ReadOnlyAttemptOutcomeRecord):
        raise RecoveryRecordIntegrityError("outcome output row has wrong type")
    outcome = decoded.record
    accepted = decode_readonly_record_member(value.accepted).record
    assert isinstance(accepted, ReadOnlyAttemptAcceptedRecord)
    _require(result.outcome, outcome, "outcome result body differs")
    _require(outcome.accepted, request.accepted_attempt, "outcome accepted differs")
    _require(outcome.source_evidence, request.source_evidence, "outcome evidence differs")
    _require(outcome.original_call_id, accepted.original_call_id, "outcome original call differs")
    _require(outcome.lineage_id, accepted.lineage_id, "outcome lineage differs")
    _require(outcome.attempt_id, accepted.attempt_id, "outcome attempt differs")
    _require(outcome.ordinal, accepted.ordinal, "outcome ordinal differs")
    _require(outcome.disposition, evidence.disposition, "outcome disposition differs")
    return DecodedReadOnlyOutcomeAssembly(outcome, evidence)
