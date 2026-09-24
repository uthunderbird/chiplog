"""Consumer checks for fixed read-only source wrappers and manifest domains."""

import hashlib

import pytest
from tests.support.recovery_records import records
from tests.support.successor_records import content_member, non_scheduler_successor

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.contracts import SchedulerRootReference
from chiplog.capabilities.agent_loop.execution_recovery_observations import RecoverySourceRecord
from chiplog.capabilities.agent_loop.execution_run_versions import ExecutionRun
from chiplog.capabilities.agent_loop.original_recovery_contracts import (
    HeldReduction,
    LoopRecoveryStream,
    LoopSemanticReductionRecord,
    UnresolvedReductionAnchor,
)
from chiplog.capabilities.agent_loop.readonly_execution_contracts import (
    PrepareReadOnlyAttempt,
    ReadOnlyAttemptAcceptedRecord,
    ReadOnlyCurrentCut,
    ReadOnlyLineageSnapshot,
    ReadOnlySucceeded,
    SuccessorReadOnlyAttempt,
)
from chiplog.capabilities.agent_loop.readonly_record_contracts import (
    ReadOnlyPendingRecord,
    decode_readonly_record_member,
)
from chiplog.capabilities.agent_loop.readonly_source_contracts import (
    ReadOnlyAcceptedRecoveryEvidenceBodyV1,
    ReadOnlyAttemptEvidenceBodyV1,
    ReadOnlyPhysicalManifestV1,
    ReadOnlyReducerSourceBodyV1,
    SelectedReadOnlyAcceptedRecoveryEvidenceV1,
    SelectedReadOnlyAttemptEvidenceV1,
    SelectedReadOnlyOriginalRecoverySourceV1,
    SelectedReadOnlyPendingV1,
    SelectedReadOnlyReducerSourceV1,
    SelectedReadOnlySuccessorInitializationV1,
    _validate_broker_source,
    readonly_physical_batch_fingerprint,
    validate_selected_readonly_accepted_recovery_evidence,
    validate_selected_readonly_original_recovery_source,
    validate_selected_readonly_pending,
    validate_selected_readonly_successor_initialization,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Absent, Present, RecoveryDTO
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import (
    ReadOnlyAttemptMember,
    ReadOnlyRetryLineage,
)
from chiplog.capabilities.agent_loop.recovery_record_contracts import (
    RecoveryRecordIntegrityError,
    RecoveryRecordMember,
)
from chiplog.capabilities.agent_loop.successor_record_contracts import (
    SUCCESSOR_INITIALIZATION_SCHEMA,
    decode_successor_record_member,
)


def _source(
    body: (
        ReadOnlyAttemptEvidenceBodyV1
        | ReadOnlyAcceptedRecoveryEvidenceBodyV1
        | ReadOnlyReducerSourceBodyV1
    ),
) -> RecoverySourceRecord:
    raw = body.canonical_bytes()
    fingerprint = hashlib.sha256(raw).hexdigest()
    identity = (
        body.attempt_id
        if isinstance(body, ReadOnlyAttemptEvidenceBodyV1 | ReadOnlyAcceptedRecoveryEvidenceBodyV1)
        else body.reducer_id
    )
    physical = Present(head=body.schema_id + ":" + fingerprint, fingerprint=fingerprint)
    reference = CallSubjectHead(subject_id=identity, revision=physical)
    return RecoverySourceRecord(
        owner="broker",
        subject=reference,
        schema_id=body.schema_id,
        canonical_record_bytes=raw,
        selected_decision=reference,
        physical_record=reference,
    )


def test_physical_manifest_rejects_a_global_nonreadonly_member() -> None:
    unrelated = records().members["SEALED_ACCOUNTING"]
    manifest = ReadOnlyPhysicalManifestV1(members=(unrelated,))
    with pytest.raises(RecoveryRecordIntegrityError):
        readonly_physical_batch_fingerprint(manifest.members)


def test_physical_manifest_binds_exact_member_order_and_original_bytes() -> None:
    values = records().members
    members = (
        values["READONLY_COUNTER_OPEN"],
        values["READONLY_ATTEMPT_ACCEPTED"],
    )
    expected = hashlib.sha256(
        ReadOnlyPhysicalManifestV1(members=members).canonical_bytes()
    ).hexdigest()
    assert readonly_physical_batch_fingerprint(members) == expected
    assert readonly_physical_batch_fingerprint(tuple(reversed(members))) != expected


def test_accepted_recovery_evidence_binds_the_actual_accepted_member_and_proof() -> None:
    accepted_member = records().members["READONLY_ATTEMPT_ACCEPTED"]
    accepted = decode_readonly_record_member(accepted_member).record
    assert isinstance(accepted, ReadOnlyAttemptAcceptedRecord)
    body = ReadOnlyAcceptedRecoveryEvidenceBodyV1(
        tenant_id="tenant",
        database_id="database",
        original_call_id=accepted.original_call_id,
        lineage_id=accepted.lineage_id,
        attempt_id=accepted.attempt_id,
        ordinal=accepted.ordinal,
        accepted=CallSubjectHead(
            subject_id=accepted.attempt_id,
            revision=Present(
                head=accepted_member.record_id,
                fingerprint=accepted_member.fingerprint,
            ),
        ),
        query_identity=accepted.proof.query_identity,
        snapshot=accepted.proof.snapshot,
        implementation=accepted.proof.implementation,
        proof=accepted.proof.no_mutation_proof,
        registry=accepted.proof.registry,
        disposition="RECOVERY_REQUIRED",
        reason_code="recovery-needed",
        canonical_diagnostic_bytes=b"diagnostic",
    )
    selected = SelectedReadOnlyAcceptedRecoveryEvidenceV1(source=_source(body), body=body)
    cut = ReadOnlyCurrentCut.model_construct(tenant_id="tenant", database_id="database")
    assert (
        validate_selected_readonly_accepted_recovery_evidence(
            selected, accepted_member, accepted.proof, cut
        )
        == body
    )
    with pytest.raises(RecoveryRecordIntegrityError):
        validate_selected_readonly_accepted_recovery_evidence(
            selected.model_copy(update={"body": body.model_copy(update={"ordinal": 9})}),
            accepted_member,
            accepted.proof,
            cut,
        )


def test_selected_pending_requires_the_exact_body_to_frontier_projection() -> None:
    body = _pending_body()
    body_member = _readonly_member("READONLY_PENDING", body)
    from chiplog.capabilities.agent_loop.readonly_record_contracts import pending_frontier_from_body

    frontier_member = _readonly_member(
        "READONLY_PENDING_FRONTIER", pending_frontier_from_body(body_member)
    )
    selected = SelectedReadOnlyPendingV1(
        pending_body=body_member,
        pending_frontier=frontier_member,
        selected_decision=_source_ref("selected-decision"),
    )
    assert validate_selected_readonly_pending(selected).pending == Present(
        head=body_member.record_id,
        fingerprint=body_member.fingerprint,
    )
    changed = selected.model_copy(update={"selected_decision": _source_ref("other-decision")})
    assert validate_selected_readonly_pending(changed) == validate_selected_readonly_pending(
        selected
    )


def test_original_recovery_source_keeps_logical_and_physical_references_distinct() -> None:
    original = records().original
    evidence = _attempt_evidence()
    logical = CallSubjectHead(
        subject_id=original.obligation_id,
        revision=Present(head=original.obligation_head, fingerprint="a" * 64),
    )
    record = LoopSemanticReductionRecord(
        stream=LoopRecoveryStream(
            stream_id="reduction-stream",
            registry=_source_ref("registry"),
            subject=logical,
            schema_id="stream.v1",
        ),
        reduction_id="reduction",
        reducer_id=original.reducer_id,
        reducer_version=original.reducer_version,
        predecessor=Absent(),
        complete_ordered_evidence=(evidence.source.subject,),
        anchor=UnresolvedReductionAnchor(
            original=original,
            closure=Absent(),
            recovered_outcome=Absent(),
        ),
        disposition=HeldReduction(reason="INCOMPLETE", conflicting_or_unclassified_evidence=()),
    )
    reduction = _global_member("LOOP_SEMANTIC_REDUCTION", record)
    source = RecoverySourceRecord(
        owner="agent_loop",
        subject=logical,
        schema_id=reduction.schema_id,
        canonical_record_bytes=reduction.canonical_record_bytes,
        selected_decision=_source_ref("source-decision"),
        physical_record=CallSubjectHead(
            subject_id=record.reduction_id,
            revision=Present(head=reduction.record_id, fingerprint=reduction.fingerprint),
        ),
    )
    selected = SelectedReadOnlyOriginalRecoverySourceV1(
        reduction=reduction,
        source=source,
        original=original,
    )
    assert validate_selected_readonly_original_recovery_source(selected, evidence) == record
    with pytest.raises(RecoveryRecordIntegrityError):
        validate_selected_readonly_original_recovery_source(
            selected.model_copy(
                update={"source": source.model_copy(update={"subject": _source_ref("other")})}
            ),
            evidence,
        )


async def test_selected_successor_initialization_requires_created_to_active_lineage() -> None:
    original_pending = _selected_pending()
    pending = validate_selected_readonly_pending(original_pending)
    fixture = await non_scheduler_successor()
    created = _native_run(fixture.result.successor_run)
    active = _native_run(
        created.model_copy(
            update={"state": "ACTIVE", "head": "pending", "predecessor": created.head}
        )
    )
    route = SuccessorReadOnlyAttempt(
        exact_pending=pending,
        predecessor_run=fixture.request.original_suspension.pair.suspended_run,
        successor_initialization=CallSubjectHead(
            subject_id=created.run_id,
            revision=Present(head="initialization", fingerprint="b" * 64),
        ),
        inherited_pending_reference=CallSubjectHead(
            subject_id=pending.original_call_id,
            revision=pending.pending,
        ),
    )
    original = decode_successor_record_member(fixture.initialization_member).record
    initialization = original.model_copy(
        update={
            "predecessor_run": route.predecessor_run,
            "successor_run": _run_reference(created),
            "inherited_pending_branches": (pending,),
        }
    )
    member = content_member(
        "SUCCESSOR_INITIALIZATION", SUCCESSOR_INITIALIZATION_SCHEMA, initialization
    )
    route = route.model_copy(
        update={
            "successor_initialization": CallSubjectHead(
                subject_id=created.run_id,
                revision=Present(head=member.record_id, fingerprint=member.fingerprint),
            )
        }
    )
    request = PrepareReadOnlyAttempt.model_construct(
        route=route,
        run=active,
        observed=ReadOnlyLineageSnapshot.model_construct(pending=pending),
        cut=ReadOnlyCurrentCut.model_construct(tenant_id=active.tenant),
    )
    selected = SelectedReadOnlySuccessorInitializationV1(
        initialization=member,
        selected_decision=_source_ref("selected-decision"),
        original_pending=original_pending,
        complete_created_to_active_run_lineage=(created, active),
    )
    assert validate_selected_readonly_successor_initialization(selected, request) == initialization
    disconnected = active.model_copy(update={"predecessor": "wrong"})
    with pytest.raises(RecoveryRecordIntegrityError):
        validate_selected_readonly_successor_initialization(
            selected.model_copy(
                update={"complete_created_to_active_run_lineage": (created, disconnected)}
            ),
            request,
        )
    with pytest.raises(RecoveryRecordIntegrityError):
        validate_selected_readonly_successor_initialization(
            selected.model_copy(update={"complete_created_to_active_run_lineage": ()}), request
        )
    forged_created = created.model_copy(update={"head": "loop:forged"})
    with pytest.raises(RecoveryRecordIntegrityError):
        validate_selected_readonly_successor_initialization(
            selected.model_copy(
                update={"complete_created_to_active_run_lineage": (forged_created, active)}
            ),
            request,
        )
    drifts: tuple[tuple[str, object, str], ...] = (
        ("tenant", "other-tenant", "tenant"),
        ("principal", "other-principal", "principal"),
        (
            "root_binding",
            SchedulerRootReference(
                root_id="root",
                subject_canonical_base64="YQ==",
                subject_schema_version="chiplog.execution-lineage-subject.v1",
                root_fingerprint="a" * 64,
                initial_run_id=created.run_id,
            ),
            "root binding",
        ),
    )
    for field, value, reason in drifts:
        drift = _native_run(
            created.model_copy(
                update={
                    field: value,
                    "state": "SUSPENDED",
                    "head": "pending",
                    "predecessor": created.head,
                }
            )
        )
        final = _native_run(
            active.model_copy(update={"head": "pending", "predecessor": drift.head})
        )
        drift_request = request.model_copy(update={"run": final})
        with pytest.raises(RecoveryRecordIntegrityError, match=reason):
            validate_selected_readonly_successor_initialization(
                selected.model_copy(
                    update={"complete_created_to_active_run_lineage": (created, drift, final)}
                ),
                drift_request,
            )


def _readonly_member(kind: str, record: RecoveryDTO) -> RecoveryRecordMember:
    from chiplog.capabilities.agent_loop.readonly_record_contracts import READONLY_RECORD_ROWS

    row = next(value for value in READONLY_RECORD_ROWS if value.record_kind == kind)
    raw = record.canonical_bytes()
    fingerprint = hashlib.sha256(raw).hexdigest()
    return RecoveryRecordMember(
        owner="agent_loop",
        record_kind=kind,
        schema_id=row.schema_id,
        record_id=row.schema_id + ":" + fingerprint,
        canonical_record_bytes=raw,
        fingerprint=fingerprint,
    )


def _selected_pending() -> SelectedReadOnlyPendingV1:
    from chiplog.capabilities.agent_loop.readonly_record_contracts import pending_frontier_from_body

    body_member = _readonly_member("READONLY_PENDING", _pending_body())
    frontier_member = _readonly_member(
        "READONLY_PENDING_FRONTIER", pending_frontier_from_body(body_member)
    )
    return SelectedReadOnlyPendingV1(
        pending_body=body_member,
        pending_frontier=frontier_member,
        selected_decision=_source_ref("selected-decision"),
    )


def _run_reference(run: ExecutionRun) -> CallSubjectHead:
    return CallSubjectHead(
        subject_id=run.run_id,
        revision=Present(head=run.head, fingerprint=run.digest()),
    )


def _native_run(run: ExecutionRun) -> ExecutionRun:
    pending = run.model_copy(update={"head": "pending"})
    return pending.model_copy(update={"head": "loop:" + pending.digest()})


def _global_member(kind: str, record: RecoveryDTO) -> RecoveryRecordMember:
    from chiplog.capabilities.agent_loop.recovery_record_contracts import RECOVERY_RECORD_ROWS

    row = next(value for value in RECOVERY_RECORD_ROWS if value.record_kind == kind)
    raw = record.canonical_bytes()
    fingerprint = hashlib.sha256(raw).hexdigest()
    return RecoveryRecordMember(
        owner=row.owner,
        record_kind=kind,
        schema_id=row.schema_id,
        record_id=row.schema_id + ":" + fingerprint,
        canonical_record_bytes=raw,
        fingerprint=fingerprint,
    )


def _attempt_evidence() -> SelectedReadOnlyAttemptEvidenceV1:
    body = ReadOnlyAttemptEvidenceBodyV1(
        tenant_id="tenant",
        database_id="database",
        original_call_id="call",
        lineage_id="lineage",
        attempt_id="attempt",
        ordinal=1,
        query_identity=_source_ref("query"),
        snapshot=_source_ref("snapshot"),
        implementation=_source_ref("implementation"),
        proof=_source_ref("proof"),
        disposition=ReadOnlySucceeded(
            result=_source_ref("result"),
            result_schema="r",
            canonical_result_bytes=b"result",
        ),
    )
    return SelectedReadOnlyAttemptEvidenceV1(source=_source(body), body=body)


def _pending_body() -> ReadOnlyPendingRecord:
    lineage = ReadOnlyRetryLineage(
        lineage_id="lineage",
        original_call_id="call",
        max_attempts=2,
        budget_version="budget",
        reducer_id="reducer",
        reducer_version="1",
    )
    return ReadOnlyPendingRecord(
        original_call_id="call",
        response_id="response",
        terminal=Absent(),
        call_outcome=Absent(),
        initialized=_source_ref("initialized").revision,
        lineage=lineage,
        ordered_attempts=(
            ReadOnlyAttemptMember(
                ordinal=0,
                attempt_id="attempt",
                initialized=_source_ref("initialized").revision,
                accepted=_source_ref("accepted").revision,
                outcome=_source_ref("outcome").revision,
                result_or_obligation=_source_ref("result").revision,
                predecessor=Absent(),
            ),
        ),
        last_retryable_failure=_source_ref("outcome").revision,
        counter=_source_ref("counter").revision,
        attempts_consumed=1,
        next_ordinal=1,
        readonly_proof=_source_ref("proof").revision,
        snapshot=_source_ref("snapshot").revision,
        execution_binding=_source_ref("execution").revision,
        crossed_binding_heads=(),
        closure_registry_id="closure",
        closure_registry_version="1",
    )


def test_broker_attempt_evidence_requires_exact_canonical_physical_source() -> None:
    body = ReadOnlyAttemptEvidenceBodyV1(
        tenant_id="tenant",
        database_id="database",
        original_call_id="call",
        lineage_id="lineage",
        attempt_id="attempt",
        ordinal=1,
        query_identity=_source_ref("query"),
        snapshot=_source_ref("snapshot"),
        implementation=_source_ref("implementation"),
        proof=_source_ref("proof"),
        disposition=ReadOnlySucceeded(
            result=_source_ref("result"),
            result_schema="r",
            canonical_result_bytes=b"result",
        ),
    )
    selected = SelectedReadOnlyAttemptEvidenceV1(source=_source(body), body=body)
    _validate_broker_source(selected.source, selected.body)
    with pytest.raises(RecoveryRecordIntegrityError):
        _validate_broker_source(
            selected.source.model_copy(update={"owner": "other"}), selected.body
        )


def test_broker_reducer_source_rejects_unsorted_or_repeated_error_classes() -> None:
    body = ReadOnlyReducerSourceBodyV1(
        tenant_id="tenant",
        database_id="database",
        registry=_source_ref("registry"),
        implementation=_source_ref("implementation"),
        reducer_id="reducer",
        reducer_version="1",
        budget_version="budget",
        retryable_error_classes=("a", "z"),
        exhaustion_provenance="FROZEN_BUDGET_EXHAUSTED",
    )
    selected = SelectedReadOnlyReducerSourceV1(source=_source(body), body=body)
    _validate_broker_source(selected.source, selected.body)
    with pytest.raises(RecoveryRecordIntegrityError):
        _validate_broker_source(
            _source(body.model_copy(update={"retryable_error_classes": ("z", "a")})),
            body,
        )


def _source_ref(name: str) -> CallSubjectHead:
    return CallSubjectHead(
        subject_id=name,
        revision=Present(
            head="physical:" + name,
            fingerprint=hashlib.sha256(name.encode()).hexdigest(),
        ),
    )
