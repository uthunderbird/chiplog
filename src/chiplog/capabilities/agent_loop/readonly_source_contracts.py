"""Fixed retained inputs and hash domains for read-only preparation.

These pure contracts preserve selected source bytes and joins.  They do not
authenticate selected decisions, broker sources, or a current transaction cut.
"""

from __future__ import annotations

import hashlib
from itertools import pairwise
from typing import Annotated, Literal

from pydantic import Field, ValidationError

from .call_acceptance_contracts import CallSubjectHead, InitializedCallRecord
from .execution_recovery_observations import RecoverySourceRecord
from .execution_run_record_contracts import (
    ExecutionRunCanonicalMember,
    ExecutionRunRecordIntegrityError,
    decode_execution_run_member,
)
from .execution_run_versions import ExecutionRun
from .original_recovery_contracts import LoopSemanticReductionRecord, UnresolvedReductionAnchor
from .readonly_execution_contracts import (
    ObservedReadOnlyAttempt,
    PrepareReadOnlyAttempt,
    ReadOnlyAttemptAcceptedRecord,
    ReadOnlyAttemptDisposition,
    ReadOnlyCurrentCut,
    ReadOnlyLineageSnapshot,
    RegisteredReadOnlyProof,
)
from .readonly_history_tool_contracts import ReadOnlyDTO
from .readonly_record_contracts import (
    AGENT_LOOP_OWNER,
    ReadOnlyPendingRecord,
    decode_readonly_record_member,
    pending_frontier_from_body,
)
from .recovery_contracts import (
    Absent,
    Digest,
    Identity,
    OriginalObligationBinding,
    Present,
    RecoveryDTO,
    UInt64,
)
from .recovery_frontier_contracts import PendingCallFrontier, ReadOnlyRetryLineage
from .recovery_record_contracts import (
    RecoveryRecordIntegrityError,
    RecoveryRecordMember,
    decode_recovery_record_member,
    validate_recovery_source_record,
)
from .successor_record_contracts import (
    ExecutionSuccessorInitializationRecord,
    decode_successor_record_member,
)


class ReadOnlyPhysicalManifestV1(RecoveryDTO):
    kind: Literal["READONLY_PHYSICAL_MANIFEST_V1"] = "READONLY_PHYSICAL_MANIFEST_V1"
    members: tuple[RecoveryRecordMember, ...]


class ReadOnlyLineageManifestV1(ReadOnlyDTO):
    kind: Literal["READONLY_LINEAGE_MANIFEST_V1"] = "READONLY_LINEAGE_MANIFEST_V1"
    initialized_head: CallSubjectHead
    initialized: InitializedCallRecord
    lineage: ReadOnlyRetryLineage
    complete_ordered_attempts: tuple[ObservedReadOnlyAttempt, ...]
    shared_counter: CallSubjectHead
    attempts_consumed: UInt64
    call_outcome: Annotated[Absent | Present, Field(discriminator="kind")]
    terminal: Annotated[Absent | Present, Field(discriminator="kind")]
    pending: Annotated[Absent | PendingCallFrontier, Field(discriminator="kind")]


class SelectedReadOnlyPendingV1(ReadOnlyDTO):
    kind: Literal["SELECTED_READONLY_PENDING_V1"] = "SELECTED_READONLY_PENDING_V1"
    pending_body: RecoveryRecordMember
    pending_frontier: RecoveryRecordMember
    selected_decision: CallSubjectHead


class ReadOnlyAttemptEvidenceBodyV1(ReadOnlyDTO):
    schema_id: Literal["chiplog.source.readonly-attempt-evidence.v1"] = (
        "chiplog.source.readonly-attempt-evidence.v1"
    )
    tenant_id: Identity
    database_id: Identity
    original_call_id: Identity
    lineage_id: Identity
    attempt_id: Identity
    ordinal: UInt64
    query_identity: CallSubjectHead
    snapshot: CallSubjectHead
    implementation: CallSubjectHead
    proof: CallSubjectHead
    disposition: ReadOnlyAttemptDisposition


class ReadOnlyAcceptedRecoveryEvidenceBodyV1(ReadOnlyDTO):
    schema_id: Literal["chiplog.source.readonly-accepted-recovery-evidence.v1"] = (
        "chiplog.source.readonly-accepted-recovery-evidence.v1"
    )
    tenant_id: Identity
    database_id: Identity
    original_call_id: Identity
    lineage_id: Identity
    attempt_id: Identity
    ordinal: UInt64
    accepted: CallSubjectHead
    query_identity: CallSubjectHead
    snapshot: CallSubjectHead
    implementation: CallSubjectHead
    proof: CallSubjectHead
    registry: CallSubjectHead
    disposition: Literal["RECOVERY_REQUIRED"]
    reason_code: Identity
    canonical_diagnostic_bytes: bytes = Field(min_length=1)


class SelectedReadOnlyAttemptEvidenceV1(ReadOnlyDTO):
    kind: Literal["SELECTED_READONLY_ATTEMPT_EVIDENCE_V1"] = "SELECTED_READONLY_ATTEMPT_EVIDENCE_V1"
    source: RecoverySourceRecord
    body: ReadOnlyAttemptEvidenceBodyV1


class SelectedReadOnlyAcceptedRecoveryEvidenceV1(ReadOnlyDTO):
    kind: Literal["SELECTED_READONLY_ACCEPTED_RECOVERY_EVIDENCE_V1"] = (
        "SELECTED_READONLY_ACCEPTED_RECOVERY_EVIDENCE_V1"
    )
    source: RecoverySourceRecord
    body: ReadOnlyAcceptedRecoveryEvidenceBodyV1


class ReadOnlyReducerSourceBodyV1(ReadOnlyDTO):
    schema_id: Literal["chiplog.source.readonly-reducer.v1"] = "chiplog.source.readonly-reducer.v1"
    tenant_id: Identity
    database_id: Identity
    registry: CallSubjectHead
    implementation: CallSubjectHead
    reducer_id: Identity
    reducer_version: Identity
    budget_version: Identity
    retryable_error_classes: tuple[Identity, ...]
    exhaustion_provenance: Literal["FROZEN_BUDGET_EXHAUSTED"]


class SelectedReadOnlyReducerSourceV1(ReadOnlyDTO):
    kind: Literal["SELECTED_READONLY_REDUCER_SOURCE_V1"] = "SELECTED_READONLY_REDUCER_SOURCE_V1"
    source: RecoverySourceRecord
    body: ReadOnlyReducerSourceBodyV1


class NoReadOnlyRecoverySourceV1(RecoveryDTO):
    kind: Literal["NO_READONLY_RECOVERY_SOURCE_V1"] = "NO_READONLY_RECOVERY_SOURCE_V1"


class SelectedReadOnlyOriginalRecoverySourceV1(ReadOnlyDTO):
    kind: Literal["SELECTED_READONLY_ORIGINAL_RECOVERY_SOURCE_V1"] = (
        "SELECTED_READONLY_ORIGINAL_RECOVERY_SOURCE_V1"
    )
    reduction: RecoveryRecordMember
    source: RecoverySourceRecord
    original: OriginalObligationBinding


ReadOnlyRecoverySourceV1 = Annotated[
    NoReadOnlyRecoverySourceV1 | SelectedReadOnlyOriginalRecoverySourceV1,
    Field(discriminator="kind"),
]


class SelectedReadOnlySuccessorInitializationV1(ReadOnlyDTO):
    kind: Literal["SELECTED_READONLY_SUCCESSOR_INITIALIZATION_V1"] = (
        "SELECTED_READONLY_SUCCESSOR_INITIALIZATION_V1"
    )
    initialization: RecoveryRecordMember
    selected_decision: CallSubjectHead
    original_pending: SelectedReadOnlyPendingV1
    complete_created_to_active_run_lineage: tuple[ExecutionRun, ...] = Field(min_length=2)


def _require(actual: object, expected: object, reason: str) -> None:
    if actual != expected:
        raise RecoveryRecordIntegrityError(reason)


def _fingerprint(value: RecoveryDTO) -> Digest:
    return hashlib.sha256(value.canonical_bytes()).hexdigest()


def _member_reference(member: RecoveryRecordMember) -> Present:
    return Present(head=member.record_id, fingerprint=member.fingerprint)


def _run_reference(run: ExecutionRun) -> CallSubjectHead:
    return CallSubjectHead(
        subject_id=run.run_id,
        revision=Present(head=run.head, fingerprint=run.digest()),
    )


def _reparse_successor_initialization(
    value: SelectedReadOnlySuccessorInitializationV1,
) -> SelectedReadOnlySuccessorInitializationV1:
    try:
        return SelectedReadOnlySuccessorInitializationV1.model_validate_json(
            value.model_dump_json()
        )
    except (TypeError, ValueError, ValidationError) as error:
        raise RecoveryRecordIntegrityError(
            "invalid selected successor initialization wrapper"
        ) from error


def _native_run(run: ExecutionRun) -> ExecutionRun:
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
    except (TypeError, ValueError, ValidationError, ExecutionRunRecordIntegrityError) as error:
        raise RecoveryRecordIntegrityError("retained successor Run is not native") from error
    _require(decoded.run, run, "retained successor Run differs from native member")
    return decoded.run


def readonly_physical_batch_fingerprint(members: tuple[RecoveryRecordMember, ...]) -> Digest:
    """Hash an ordered finite read-only physical manifest after strict decoding."""
    for member in members:
        decode_readonly_record_member(member)
    return _fingerprint(ReadOnlyPhysicalManifestV1(members=members))


def readonly_lineage_manifest_fingerprint(snapshot: ReadOnlyLineageSnapshot) -> Digest:
    """Hash every staged snapshot field other than its derived manifest fingerprint."""
    return _fingerprint(
        ReadOnlyLineageManifestV1(
            initialized_head=snapshot.initialized_head,
            initialized=snapshot.initialized,
            lineage=snapshot.lineage,
            complete_ordered_attempts=snapshot.complete_ordered_attempts,
            shared_counter=snapshot.shared_counter,
            attempts_consumed=snapshot.attempts_consumed,
            call_outcome=snapshot.call_outcome,
            terminal=snapshot.terminal,
            pending=snapshot.pending,
        )
    )


def validate_selected_readonly_pending(value: SelectedReadOnlyPendingV1) -> PendingCallFrontier:
    """Bind a pending body to its acyclic historical frontier projection."""
    body = decode_readonly_record_member(value.pending_body)
    frontier = decode_readonly_record_member(value.pending_frontier)
    if not isinstance(body.record, ReadOnlyPendingRecord) or not isinstance(
        frontier.record, PendingCallFrontier
    ):
        raise RecoveryRecordIntegrityError("selected read-only pending rows have wrong types")
    _require(
        pending_frontier_from_body(value.pending_body),
        frontier.record,
        "selected read-only pending projection differs",
    )
    return frontier.record


def validate_selected_readonly_successor_initialization(
    value: SelectedReadOnlySuccessorInitializationV1, request: PrepareReadOnlyAttempt
) -> ExecutionSuccessorInitializationRecord:
    """Bind selected pending and every retained Run revision from CREATED through ACTIVE."""
    value = _reparse_successor_initialization(value)
    decoded = decode_successor_record_member(value.initialization)
    if not isinstance(decoded.record, ExecutionSuccessorInitializationRecord):
        raise RecoveryRecordIntegrityError("selected successor initialization row has wrong type")
    pending = validate_selected_readonly_pending(value.original_pending)
    route = request.route
    if route.kind != "SUCCESSOR_PENDING":
        raise RecoveryRecordIntegrityError(
            "selected successor initialization requires successor route"
        )
    _require(request.observed.pending, pending, "successor observed pending differs")
    _require(route.exact_pending, pending, "successor route pending differs")
    _require(decoded.record.predecessor_run, route.predecessor_run, "successor predecessor differs")
    _require(
        route.successor_initialization,
        CallSubjectHead(
            subject_id=decoded.record.successor_run.subject_id,
            revision=_member_reference(value.initialization),
        ),
        "successor initialization physical reference differs",
    )
    _require(
        decoded.record.inherited_pending_branches.count(pending),
        1,
        "successor inherited pending must occur exactly once",
    )
    _require(
        route.inherited_pending_reference,
        CallSubjectHead(subject_id=pending.original_call_id, revision=pending.pending),
        "successor inherited pending reference differs",
    )
    runs = tuple(_native_run(run) for run in value.complete_created_to_active_run_lineage)
    _require(runs[0].state, "CREATED", "successor lineage must begin CREATED")
    _require(_run_reference(runs[0]), decoded.record.successor_run, "successor created run differs")
    _require(runs[-1].state, "ACTIVE", "successor lineage must end ACTIVE")
    _require(runs[-1], request.run, "successor active run differs")
    _require(request.cut.tenant_id, runs[0].tenant, "successor request-cut tenant differs")
    for previous, current in pairwise(runs):
        _require(current.run_id, previous.run_id, "successor lineage run identity differs")
        _require(current.schema_id, previous.schema_id, "successor lineage schema differs")
        _require(current.tenant, previous.tenant, "successor lineage tenant differs")
        _require(current.principal, previous.principal, "successor lineage principal differs")
        _require(
            current.root_binding,
            previous.root_binding,
            "successor lineage root binding differs",
        )
        _require(current.predecessor, previous.head, "successor lineage predecessor differs")
    return decoded.record


def _validate_broker_source(
    source: RecoverySourceRecord,
    body: (
        ReadOnlyAttemptEvidenceBodyV1
        | ReadOnlyAcceptedRecoveryEvidenceBodyV1
        | ReadOnlyReducerSourceBodyV1
    ),
) -> None:
    raw = body.canonical_bytes()
    fingerprint = hashlib.sha256(raw).hexdigest()
    identity = (
        body.attempt_id
        if isinstance(body, ReadOnlyAttemptEvidenceBodyV1 | ReadOnlyAcceptedRecoveryEvidenceBodyV1)
        else body.reducer_id
    )
    expected = Present(head=body.schema_id + ":" + fingerprint, fingerprint=fingerprint)
    _require(source.owner, "broker", "read-only broker source owner differs")
    _require(source.schema_id, body.schema_id, "read-only broker source schema differs")
    _require(source.canonical_record_bytes, raw, "read-only broker source bytes differ")
    _require(source.subject.subject_id, identity, "read-only broker source subject differs")
    _require(
        source.subject,
        source.physical_record,
        "read-only broker source physical subject differs",
    )
    _require(
        source.physical_record.revision,
        expected,
        "read-only broker source physical ID differs",
    )


def validate_selected_readonly_attempt_evidence(
    value: SelectedReadOnlyAttemptEvidenceV1, request: PrepareReadOnlyAttempt
) -> ReadOnlyAttemptEvidenceBodyV1:
    _validate_broker_source(value.source, value.body)
    _require(value.body.tenant_id, request.cut.tenant_id, "attempt evidence tenant differs")
    _require(value.body.database_id, request.cut.database_id, "attempt evidence database differs")
    _require(
        value.body.original_call_id,
        request.observed.lineage.original_call_id,
        "attempt evidence original call differs",
    )
    _require(
        value.body.lineage_id,
        request.observed.lineage.lineage_id,
        "attempt evidence lineage differs",
    )
    _require(
        value.body.query_identity,
        request.proof.query_identity,
        "attempt evidence query differs",
    )
    _require(value.body.snapshot, request.proof.snapshot, "attempt evidence snapshot differs")
    _require(
        value.body.implementation,
        request.proof.implementation,
        "attempt evidence implementation differs",
    )
    _require(value.body.proof, request.proof.no_mutation_proof, "attempt evidence proof differs")
    return value.body


def validate_selected_readonly_accepted_recovery_evidence(
    value: SelectedReadOnlyAcceptedRecoveryEvidenceV1,
    accepted_member: RecoveryRecordMember,
    current_proof: RegisteredReadOnlyProof,
    cut: ReadOnlyCurrentCut,
) -> ReadOnlyAcceptedRecoveryEvidenceBodyV1:
    """Bind broker recovery evidence to an already selected physical acceptance."""
    try:
        value = SelectedReadOnlyAcceptedRecoveryEvidenceV1.model_validate_json(
            value.model_dump_json()
        )
    except (TypeError, ValueError, ValidationError) as error:
        raise RecoveryRecordIntegrityError("invalid accepted recovery evidence wrapper") from error
    try:
        decoded_body = ReadOnlyAcceptedRecoveryEvidenceBodyV1.model_validate_json(
            value.source.canonical_record_bytes
        )
    except ValidationError as error:
        raise RecoveryRecordIntegrityError("invalid accepted recovery evidence bytes") from error
    _require(
        decoded_body.canonical_bytes(),
        value.source.canonical_record_bytes,
        "accepted recovery evidence bytes are not canonical",
    )
    _require(decoded_body, value.body, "accepted recovery evidence body differs")
    _validate_broker_source(value.source, value.body)
    accepted = decode_readonly_record_member(accepted_member)
    if not isinstance(accepted.record, ReadOnlyAttemptAcceptedRecord):
        raise RecoveryRecordIntegrityError(
            "accepted recovery evidence requires an accepted attempt member"
        )
    record = accepted.record
    _require(current_proof, record.proof, "accepted recovery current proof differs")
    _require(value.body.tenant_id, cut.tenant_id, "accepted recovery evidence tenant differs")
    _require(value.body.database_id, cut.database_id, "accepted recovery evidence database differs")
    _require(
        value.body.accepted,
        CallSubjectHead(
            subject_id=record.attempt_id,
            revision=Present(
                head=accepted_member.record_id,
                fingerprint=accepted_member.fingerprint,
            ),
        ),
        "accepted recovery evidence accepted reference differs",
    )
    _require(
        value.body.original_call_id,
        record.original_call_id,
        "accepted recovery evidence original call differs",
    )
    _require(value.body.lineage_id, record.lineage_id, "accepted recovery evidence lineage differs")
    _require(value.body.attempt_id, record.attempt_id, "accepted recovery evidence attempt differs")
    _require(value.body.ordinal, record.ordinal, "accepted recovery evidence ordinal differs")
    _require(
        value.body.query_identity,
        record.proof.query_identity,
        "accepted recovery evidence query differs",
    )
    _require(
        value.body.snapshot,
        record.proof.snapshot,
        "accepted recovery evidence snapshot differs",
    )
    _require(
        value.body.implementation,
        record.proof.implementation,
        "accepted recovery evidence implementation differs",
    )
    _require(
        value.body.proof,
        record.proof.no_mutation_proof,
        "accepted recovery evidence proof differs",
    )
    _require(
        value.body.registry,
        record.proof.registry,
        "accepted recovery evidence registry differs",
    )
    return value.body


def validate_selected_readonly_reducer_source(
    value: SelectedReadOnlyReducerSourceV1,
    lineage: ReadOnlyRetryLineage,
    cut: ReadOnlyCurrentCut,
) -> ReadOnlyReducerSourceBodyV1:
    _validate_broker_source(value.source, value.body)
    _require(value.body.tenant_id, cut.tenant_id, "reducer source tenant differs")
    _require(value.body.database_id, cut.database_id, "reducer source database differs")
    _require(value.body.reducer_id, lineage.reducer_id, "reducer source ID differs")
    _require(value.body.reducer_version, lineage.reducer_version, "reducer source version differs")
    _require(value.body.budget_version, lineage.budget_version, "reducer source budget differs")
    if tuple(sorted(set(value.body.retryable_error_classes))) != value.body.retryable_error_classes:
        raise RecoveryRecordIntegrityError(
            "reducer source retryable classes are not unique and sorted"
        )
    return value.body


def validate_selected_readonly_original_recovery_source(
    value: SelectedReadOnlyOriginalRecoverySourceV1,
    evidence: SelectedReadOnlyAttemptEvidenceV1,
) -> LoopSemanticReductionRecord:
    """Bind an unresolved original-stream reduction without claiming body decoding."""
    decoded = decode_recovery_record_member(value.reduction)
    if not isinstance(decoded.record, LoopSemanticReductionRecord):
        raise RecoveryRecordIntegrityError("selected original recovery row has wrong type")
    validate_recovery_source_record(value.source, decoded, value.source.selected_decision)
    record = decoded.record
    _require(
        value.source.physical_record.subject_id,
        record.reduction_id,
        "original recovery physical subject differs",
    )
    _require(
        value.source.subject,
        record.stream.subject,
        "original recovery logical subject differs",
    )
    _require(
        value.source.subject.subject_id,
        value.original.obligation_id,
        "original recovery obligation ID differs",
    )
    _require(
        value.source.subject.revision.head,
        value.original.obligation_head,
        "original recovery obligation head differs",
    )
    if not isinstance(record.anchor, UnresolvedReductionAnchor):
        raise RecoveryRecordIntegrityError("original recovery anchor must remain unresolved")
    _require(record.anchor.original, value.original, "original recovery anchor differs")
    _require(record.reducer_id, value.original.reducer_id, "original recovery reducer differs")
    _require(
        record.reducer_version,
        value.original.reducer_version,
        "original recovery version differs",
    )
    if value.source.owner != AGENT_LOOP_OWNER:
        raise RecoveryRecordIntegrityError("original recovery source owner differs")
    if value.source.schema_id != value.reduction.schema_id:
        raise RecoveryRecordIntegrityError("original recovery source schema differs")
    if evidence.source.subject not in record.complete_ordered_evidence:
        raise RecoveryRecordIntegrityError("original recovery evidence source is absent")
    return record
