"""Contract checks for the staged-only original read-only obligation primitive."""

from __future__ import annotations

import hashlib

import pytest
from tests.support.recovery_records import fence, head, readonly_proof

from chiplog.capabilities.agent_loop.call_acceptance_contracts import (
    CallAuthorityObservation,
    CallSubjectHead,
    InitializedCallRecord,
    InitializedReadOnlyLineage,
    OriginalCallKey,
    SealedCallInput,
)
from chiplog.capabilities.agent_loop.call_acceptance_preparation import (
    call_record_reference,
    call_subject_id,
)
from chiplog.capabilities.agent_loop.execution_recovery_observations import RecoverySourceRecord
from chiplog.capabilities.agent_loop.readonly_execution_contracts import (
    ObservedReadOnlyAttempt,
    ObservedReadOnlyOutcome,
    ReadOnlyAttemptAcceptedRecord,
    ReadOnlyAttemptOutcomeRecord,
    ReadOnlyCounterRecord,
    ReadOnlyCurrentCut,
    ReadOnlyLineageSnapshot,
    ReadOnlySucceeded,
)
from chiplog.capabilities.agent_loop.readonly_obligation_contracts import (
    ORIGINAL_READONLY_OBLIGATION_SCHEMA,
    OriginalReadOnlyObligationRecordV1,
    PrepareReadOnlyRecoverySemanticReductionV1,
    ReadOnlyOriginalOpeningSpecV1,
    SelectedReadOnlyAcceptedAttemptV1,
    decode_original_readonly_obligation_member,
    make_original_readonly_obligation_member,
    make_readonly_recovery_required_outcome_member,
    original_readonly_obligation_binding,
    original_readonly_obligation_logical_head,
    prepare_readonly_original_opening,
    prepare_readonly_recovery_semantic_reduction,
    validate_prepared_readonly_recovery_semantic_reduction,
    validate_selected_readonly_accepted_attempt,
)
from chiplog.capabilities.agent_loop.readonly_record_contracts import (
    READONLY_RECORD_ROWS,
    ReadOnlyPendingRecord,
    decode_readonly_record_member,
    pending_frontier_from_body,
)
from chiplog.capabilities.agent_loop.readonly_source_contracts import (
    ReadOnlyAcceptedRecoveryEvidenceBodyV1,
    ReadOnlyReducerSourceBodyV1,
    SelectedReadOnlyAcceptedRecoveryEvidenceV1,
    SelectedReadOnlyPendingV1,
    SelectedReadOnlyReducerSourceV1,
    readonly_lineage_manifest_fingerprint,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Absent, Present, RecoveryDTO
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import (
    PendingCallFrontier,
    ReadOnlyAttemptMember,
    ReadOnlyRetryLineage,
)
from chiplog.capabilities.agent_loop.recovery_record_contracts import (
    RecoveryRecordIntegrityError,
    RecoveryRecordMember,
)


def _member(
    kind: str,
    record: RecoveryDTO,
) -> RecoveryRecordMember:
    row = next(row for row in READONLY_RECORD_ROWS if row.record_kind == kind)
    raw = record.canonical_bytes()
    fingerprint = hashlib.sha256(raw).hexdigest()
    return RecoveryRecordMember(
        owner=row.owner,
        record_kind=row.record_kind,
        schema_id=row.schema_id,
        record_id=row.schema_id + ":" + fingerprint,
        canonical_record_bytes=raw,
        fingerprint=fingerprint,
    )


def _physical(member: RecoveryRecordMember, subject: str) -> CallSubjectHead:
    return CallSubjectHead(
        subject_id=subject, revision=Present(head=member.record_id, fingerprint=member.fingerprint)
    )


def _scenario() -> tuple[SelectedReadOnlyAcceptedAttemptV1, ReadOnlyLineageSnapshot]:
    original = OriginalCallKey(
        tenant_id="tenant",
        original_run_id="original-run",
        original_turn_id="turn",
        captured_response=head("response"),
        ordinal=0,
        model_call_label="call",
    )
    original_call_id = call_subject_id(original)
    lineage = ReadOnlyRetryLineage(
        lineage_id="lineage",
        original_call_id=original_call_id,
        max_attempts=2,
        budget_version="budget",
        reducer_id="reducer",
        reducer_version="1",
    )
    initialized = InitializedCallRecord(
        original_call_id=original_call_id,
        call=SealedCallInput(
            original=original,
            classification="READ_ONLY",
            tool_schema=head("tool-schema"),
            tool_policy=head("tool-policy"),
            canonical_call_base64="Y2FsbA==",
            retry_lineage=InitializedReadOnlyLineage(lineage=lineage),
        ),
        predecessor=Absent(),
    )
    initialized_head = call_record_reference(original_call_id, initialized)
    counter = ReadOnlyCounterRecord(
        original_call_id=original_call_id,
        lineage_id=lineage.lineage_id,
        predecessor=head("counter-root"),
        attempts_consumed=1,
        closed=False,
    )
    counter_member = _member("READONLY_COUNTER", counter)
    accepted = ReadOnlyAttemptAcceptedRecord(
        original_call_id=original_call_id,
        lineage_id=lineage.lineage_id,
        attempt_id="attempt-0",
        ordinal=0,
        initialized=initialized_head,
        predecessor_attempt=Absent(),
        active_run=head("active-run"),
        proof=readonly_proof(),
        previous_counter=head("counter-root"),
        next_counter=_physical(counter_member, "counter"),
        fence=fence(),
    )
    accepted_member = _member("READONLY_ATTEMPT_ACCEPTED", accepted)
    pending_body = ReadOnlyPendingRecord(
        original_call_id=original_call_id,
        response_id="response",
        terminal=Absent(),
        call_outcome=Absent(),
        initialized=initialized_head.revision,
        lineage=lineage,
        ordered_attempts=(
            ReadOnlyAttemptMember(
                ordinal=0,
                attempt_id=accepted.attempt_id,
                initialized=initialized_head.revision,
                accepted=_physical(accepted_member, accepted.attempt_id).revision,
                outcome=Absent(),
                result_or_obligation=Absent(),
                predecessor=Absent(),
            ),
        ),
        last_retryable_failure=Present(head="prior-failure", fingerprint="a" * 64),
        counter=_physical(counter_member, "counter").revision,
        attempts_consumed=1,
        next_ordinal=1,
        readonly_proof=readonly_proof().no_mutation_proof.revision,
        snapshot=readonly_proof().snapshot.revision,
        execution_binding=Absent(),
        crossed_binding_heads=(),
        closure_registry_id="closure",
        closure_registry_version="1",
    )
    pending_member = _member("READONLY_PENDING", pending_body)
    frontier_member = _member(
        "READONLY_PENDING_FRONTIER", pending_frontier_from_body(pending_member)
    )
    selected_pending = SelectedReadOnlyPendingV1(
        pending_body=pending_member,
        pending_frontier=frontier_member,
        selected_decision=head("pending-selection"),
    )
    snapshot = ReadOnlyLineageSnapshot(
        initialized_head=initialized_head,
        initialized=initialized,
        lineage=lineage,
        complete_ordered_attempts=(
            ObservedReadOnlyAttempt(
                accepted_head=_physical(accepted_member, accepted.attempt_id),
                accepted=accepted,
                outcome=Absent(),
            ),
        ),
        shared_counter=_physical(counter_member, "counter"),
        attempts_consumed=1,
        call_outcome=Absent(),
        terminal=Absent(),
        pending=pending_frontier_from_body(pending_member),
        complete_manifest_fingerprint="0" * 64,
    )
    snapshot = snapshot.model_copy(
        update={"complete_manifest_fingerprint": readonly_lineage_manifest_fingerprint(snapshot)}
    )
    return (
        SelectedReadOnlyAcceptedAttemptV1(
            accepted=accepted_member,
            counter=counter_member,
            selected_decision=head("accepted-selection"),
            pending=selected_pending,
        ),
        snapshot,
    )


def _obligation() -> OriginalReadOnlyObligationRecordV1:
    selected, snapshot = _scenario()
    accepted = validate_selected_readonly_accepted_attempt(selected, snapshot)
    return OriginalReadOnlyObligationRecordV1(
        tenant_id="tenant",
        database_id="database",
        original_run_id="original-run",
        original_call_id=accepted.original_call_id,
        obligation_id="obligation",
        obligation_stream_id="obligation-stream",
        initialized=accepted.initialized,
        accepted=_physical(selected.accepted, accepted.attempt_id),
        lineage_id=accepted.lineage_id,
        attempt_id=accepted.attempt_id,
        ordinal=accepted.ordinal,
        closure_predicate_id="closure",
        closure_predicate_version="1",
        resolver_id="resolver",
        resolver_version="1",
        reducer_id="reducer",
        reducer_version="1",
        evidence_stream_id="evidence-stream",
        evidence_head=Absent(),
    )


def _two_attempt_scenario() -> tuple[SelectedReadOnlyAcceptedAttemptV1, ReadOnlyLineageSnapshot]:
    _selected, observed = _scenario()
    assert isinstance(observed.pending, PendingCallFrontier)
    first = observed.complete_ordered_attempts[0]
    first_outcome = ReadOnlyAttemptOutcomeRecord(
        original_call_id=first.accepted.original_call_id,
        lineage_id=first.accepted.lineage_id,
        attempt_id=first.accepted.attempt_id,
        ordinal=first.accepted.ordinal,
        accepted=first.accepted_head,
        source_evidence=head("first-evidence"),
        disposition=ReadOnlySucceeded(
            result=head("first-result"), result_schema="result.v1", canonical_result_bytes=b"first"
        ),
    )
    first_outcome_member = _member("READONLY_ATTEMPT_OUTCOME", first_outcome)
    second_counter = ReadOnlyCounterRecord(
        original_call_id=first.accepted.original_call_id,
        lineage_id=first.accepted.lineage_id,
        predecessor=observed.shared_counter,
        attempts_consumed=2,
        closed=False,
    )
    second_counter_member = _member("READONLY_COUNTER", second_counter)
    second_accepted = first.accepted.model_copy(
        update={
            "attempt_id": "attempt-1",
            "ordinal": 1,
            "predecessor_attempt": first.accepted_head.revision,
            "previous_counter": observed.shared_counter,
            "next_counter": _physical(second_counter_member, "counter-2"),
        }
    )
    second_accepted_member = _member("READONLY_ATTEMPT_ACCEPTED", second_accepted)
    first_member = ReadOnlyAttemptMember(
        ordinal=0,
        attempt_id=first.accepted.attempt_id,
        initialized=first.accepted.initialized.revision,
        accepted=first.accepted_head.revision,
        outcome=_physical(first_outcome_member, first.accepted.attempt_id).revision,
        result_or_obligation=head("first-result").revision,
        predecessor=Absent(),
    )
    second_member = ReadOnlyAttemptMember(
        ordinal=1,
        attempt_id=second_accepted.attempt_id,
        initialized=second_accepted.initialized.revision,
        accepted=_physical(second_accepted_member, second_accepted.attempt_id).revision,
        outcome=Absent(),
        result_or_obligation=Absent(),
        predecessor=first_member.accepted,
    )
    body = ReadOnlyPendingRecord(
        original_call_id=observed.lineage.original_call_id,
        response_id=observed.pending.response_id,
        terminal=Absent(),
        call_outcome=Absent(),
        initialized=observed.initialized_head.revision,
        lineage=observed.lineage,
        ordered_attempts=(first_member, second_member),
        last_retryable_failure=_physical(first_outcome_member, first.accepted.attempt_id).revision,
        counter=_physical(second_counter_member, "counter-2").revision,
        attempts_consumed=2,
        next_ordinal=2,
        readonly_proof=second_accepted.proof.no_mutation_proof.revision,
        snapshot=second_accepted.proof.snapshot.revision,
        execution_binding=Absent(),
        crossed_binding_heads=(),
        closure_registry_id="closure",
        closure_registry_version="1",
    )
    body_member = _member("READONLY_PENDING", body)
    frontier_member = _member("READONLY_PENDING_FRONTIER", pending_frontier_from_body(body_member))
    pending = SelectedReadOnlyPendingV1(
        pending_body=body_member,
        pending_frontier=frontier_member,
        selected_decision=head("two-attempt-pending-selection"),
    )
    history = ObservedReadOnlyAttempt(
        accepted_head=first.accepted_head,
        accepted=first.accepted,
        outcome=ObservedReadOnlyOutcome(
            head=_physical(first_outcome_member, first.accepted.attempt_id), record=first_outcome
        ),
    )
    final = ObservedReadOnlyAttempt(
        accepted_head=_physical(second_accepted_member, second_accepted.attempt_id),
        accepted=second_accepted,
        outcome=Absent(),
    )
    snapshot = observed.model_copy(
        update={
            "complete_ordered_attempts": (history, final),
            "shared_counter": _physical(second_counter_member, "counter-2"),
            "attempts_consumed": 2,
            "pending": pending_frontier_from_body(body_member),
            "complete_manifest_fingerprint": "0" * 64,
        }
    )
    snapshot = snapshot.model_copy(
        update={"complete_manifest_fingerprint": readonly_lineage_manifest_fingerprint(snapshot)}
    )
    return (
        SelectedReadOnlyAcceptedAttemptV1(
            accepted=second_accepted_member,
            counter=second_counter_member,
            selected_decision=head("two-attempt-selection"),
            pending=pending,
        ),
        snapshot,
    )


def test_original_obligation_has_a_fixed_canonical_member_and_binding() -> None:
    member = make_original_readonly_obligation_member(_obligation())
    decoded = decode_original_readonly_obligation_member(member)
    assert decoded.record == _obligation()
    assert member.record_id == ORIGINAL_READONLY_OBLIGATION_SCHEMA + ":" + member.fingerprint
    binding = original_readonly_obligation_binding(member)
    assert binding.obligation_head == member.record_id
    assert original_readonly_obligation_logical_head(member) == CallSubjectHead(
        subject_id="obligation",
        revision=Present(head=member.record_id, fingerprint=member.fingerprint),
    )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("owner", "broker"),
        ("record_kind", "OTHER"),
        ("schema_id", "wrong.v1"),
        ("record_id", "wrong"),
        ("fingerprint", "b" * 64),
    ),
)
def test_original_obligation_decoder_rejects_wrong_physical_envelope(
    field: str, value: str
) -> None:
    member = make_original_readonly_obligation_member(_obligation()).model_copy(
        update={field: value}
    )
    with pytest.raises(RecoveryRecordIntegrityError):
        decode_original_readonly_obligation_member(member)


def test_original_obligation_decoder_rejects_parseable_noncanonical_bytes() -> None:
    member = make_original_readonly_obligation_member(_obligation())
    raw = member.canonical_record_bytes + b"\n"
    forged = member.model_copy(
        update={
            "canonical_record_bytes": raw,
            "fingerprint": hashlib.sha256(raw).hexdigest(),
            "record_id": ORIGINAL_READONLY_OBLIGATION_SCHEMA
            + ":"
            + hashlib.sha256(raw).hexdigest(),
        }
    )
    with pytest.raises(RecoveryRecordIntegrityError, match="canonical"):
        decode_original_readonly_obligation_member(forged)


def test_original_obligation_decoder_rejects_wrong_embedded_schema() -> None:
    member = make_original_readonly_obligation_member(_obligation())
    raw = member.canonical_record_bytes.replace(
        ORIGINAL_READONLY_OBLIGATION_SCHEMA.encode(), b"chiplog.loop.wrong.v1"
    )
    forged = member.model_copy(
        update={
            "canonical_record_bytes": raw,
            "fingerprint": hashlib.sha256(raw).hexdigest(),
            "record_id": ORIGINAL_READONLY_OBLIGATION_SCHEMA
            + ":"
            + hashlib.sha256(raw).hexdigest(),
        }
    )
    with pytest.raises(RecoveryRecordIntegrityError):
        decode_original_readonly_obligation_member(forged)


def test_selected_accepted_attempt_binds_current_pending_and_native_initialization() -> None:
    selected, observed = _scenario()
    accepted = validate_selected_readonly_accepted_attempt(selected, observed)
    assert accepted.attempt_id == "attempt-0"
    closed_counter = selected.counter.model_copy(
        update={
            "canonical_record_bytes": selected.counter.canonical_record_bytes.replace(
                b"false", b"true"
            ),
        }
    )
    with pytest.raises(RecoveryRecordIntegrityError):
        validate_selected_readonly_accepted_attempt(
            selected.model_copy(update={"counter": closed_counter}), observed
        )
    forged_initialized = observed.model_copy(update={"initialized_head": head("forged")})
    forged_fingerprint = readonly_lineage_manifest_fingerprint(forged_initialized)
    forged_initialized = forged_initialized.model_copy(
        update={"complete_manifest_fingerprint": forged_fingerprint}
    )
    with pytest.raises(RecoveryRecordIntegrityError, match="native"):
        validate_selected_readonly_accepted_attempt(
            selected,
            forged_initialized,
        )
    with pytest.raises(RecoveryRecordIntegrityError, match="manifest"):
        validate_selected_readonly_accepted_attempt(
            selected,
            observed.model_copy(update={"complete_manifest_fingerprint": "f" * 64}),
        )


def test_selected_accepted_attempt_rejects_coherent_final_proof_mutation() -> None:
    selected, observed = _scenario()
    final = observed.complete_ordered_attempts[-1]
    changed_proof = final.accepted.proof.model_copy(update={"proof_schema": "other-proof.v1"})
    changed_accepted = final.accepted.model_copy(update={"proof": changed_proof})
    changed_final = final.model_copy(update={"accepted": changed_accepted})
    changed = observed.model_copy(update={"complete_ordered_attempts": (changed_final,)})
    changed = changed.model_copy(
        update={"complete_manifest_fingerprint": readonly_lineage_manifest_fingerprint(changed)}
    )
    with pytest.raises(RecoveryRecordIntegrityError, match="native"):
        validate_selected_readonly_accepted_attempt(selected, changed)


def test_selected_accepted_attempt_rejects_non_native_historical_acceptance() -> None:
    selected, observed = _two_attempt_scenario()
    historical = observed.complete_ordered_attempts[0]
    changed_proof = historical.accepted.proof.model_copy(update={"proof_schema": "forged.v1"})
    changed_historical = historical.model_copy(
        update={"accepted": historical.accepted.model_copy(update={"proof": changed_proof})}
    )
    changed = observed.model_copy(
        update={
            "complete_ordered_attempts": (changed_historical, observed.complete_ordered_attempts[1])
        }
    )
    changed = changed.model_copy(
        update={"complete_manifest_fingerprint": readonly_lineage_manifest_fingerprint(changed)}
    )
    with pytest.raises(RecoveryRecordIntegrityError, match="native"):
        validate_selected_readonly_accepted_attempt(selected, changed)


def test_selected_accepted_attempt_rejects_replaced_member() -> None:
    selected, observed = _scenario()
    forged_accepted = selected.accepted.model_copy(update={"record_id": "forged"})
    with pytest.raises(RecoveryRecordIntegrityError):
        validate_selected_readonly_accepted_attempt(
            selected.model_copy(update={"accepted": forged_accepted}), observed
        )


def test_selected_accepted_attempt_reparses_constructed_wrapper() -> None:
    _, observed = _scenario()
    forged = SelectedReadOnlyAcceptedAttemptV1.model_construct(
        accepted="not-a-member",
        counter="not-a-member",
        selected_decision="not-a-head",
        pending="not-pending",
    )
    with pytest.warns(UserWarning), pytest.raises(RecoveryRecordIntegrityError, match="wrapper"):
        validate_selected_readonly_accepted_attempt(forged, observed)


def _broker_source(
    body: ReadOnlyAcceptedRecoveryEvidenceBodyV1 | ReadOnlyReducerSourceBodyV1,
    subject: str,
) -> RecoverySourceRecord:
    raw = body.canonical_bytes()
    fingerprint = hashlib.sha256(raw).hexdigest()
    physical = Present(head=body.schema_id + ":" + fingerprint, fingerprint=fingerprint)
    reference = CallSubjectHead(subject_id=subject, revision=physical)
    return RecoverySourceRecord(
        owner="broker",
        subject=reference,
        schema_id=body.schema_id,
        canonical_record_bytes=raw,
        selected_decision=head("broker-selection"),
        physical_record=reference,
    )


def test_staged_opening_and_semantic_reduction_are_exact_and_unresolved() -> None:
    selected, observed = _scenario()
    accepted = validate_selected_readonly_accepted_attempt(selected, observed)
    cut = ReadOnlyCurrentCut(
        tenant_id="tenant",
        database_id="database",
        tenant_commit_sequence=1,
        materialization_commitment="a" * 64,
        authority_registry=accepted.proof.registry,
        sources=(
            CallAuthorityObservation(
                source_id="source",
                family="EVIDENCE",
                source=head("authority"),
                generation="1",
                frontier="1",
                canonical_value_base64="YQ==",
                observed_at_ns=1,
                valid_until_ns=2,
            ),
        ),
        applicability=fence(),
    )
    evidence_body = ReadOnlyAcceptedRecoveryEvidenceBodyV1(
        tenant_id="tenant",
        database_id="database",
        original_call_id=accepted.original_call_id,
        lineage_id=accepted.lineage_id,
        attempt_id=accepted.attempt_id,
        ordinal=accepted.ordinal,
        accepted=_physical(selected.accepted, accepted.attempt_id),
        query_identity=accepted.proof.query_identity,
        snapshot=accepted.proof.snapshot,
        implementation=accepted.proof.implementation,
        proof=accepted.proof.no_mutation_proof,
        registry=accepted.proof.registry,
        disposition="RECOVERY_REQUIRED",
        reason_code="needed",
        canonical_diagnostic_bytes=b"needed",
    )
    evidence = SelectedReadOnlyAcceptedRecoveryEvidenceV1(
        source=_broker_source(evidence_body, accepted.attempt_id), body=evidence_body
    )
    reducer_body = ReadOnlyReducerSourceBodyV1(
        tenant_id="tenant",
        database_id="database",
        registry=accepted.proof.registry,
        implementation=accepted.proof.implementation,
        reducer_id=observed.lineage.reducer_id,
        reducer_version=observed.lineage.reducer_version,
        budget_version=observed.lineage.budget_version,
        retryable_error_classes=(),
        exhaustion_provenance="FROZEN_BUDGET_EXHAUSTED",
    )
    reducer = SelectedReadOnlyReducerSourceV1(
        source=_broker_source(reducer_body, reducer_body.reducer_id), body=reducer_body
    )
    spec = ReadOnlyOriginalOpeningSpecV1(
        obligation_id="obligation",
        obligation_stream_id="obligation-stream",
        closure_predicate_id="closure",
        closure_predicate_version="1",
        resolver_id="resolver",
        resolver_version="1",
        reducer_id=reducer_body.reducer_id,
        reducer_version=reducer_body.reducer_version,
        evidence_stream_id="evidence-stream",
        reduction_stream_id="reduction-stream",
        reduction_registry=reducer_body.registry,
    )
    opening = prepare_readonly_original_opening(spec, selected, observed, evidence, reducer, cut)
    outcome = make_readonly_recovery_required_outcome_member(opening, evidence)
    request = PrepareReadOnlyRecoverySemanticReductionV1(
        command_id="terminal/original-reduction",
        opening=opening,
        outcome=outcome,
        evidence=evidence,
        opening_spec=spec,
        cut=cut,
    )
    result = prepare_readonly_recovery_semantic_reduction(request)
    assert result.record.anchor.closure == Absent()
    assert result.record.anchor.recovered_outcome == Absent()
    assert validate_prepared_readonly_recovery_semantic_reduction(request, result) == result.record
    with pytest.raises(RecoveryRecordIntegrityError, match="fingerprint"):
        validate_prepared_readonly_recovery_semantic_reduction(
            request, result.model_copy(update={"physical_fingerprint": "f" * 64})
        )
    decoded_opening = decode_original_readonly_obligation_member(opening).record
    assert isinstance(decoded_opening, OriginalReadOnlyObligationRecordV1)
    changed_opening = decoded_opening.model_copy(update={"original_call_id": "other-call"})
    with pytest.raises(RecoveryRecordIntegrityError, match="call"):
        prepare_readonly_recovery_semantic_reduction(
            request.model_copy(
                update={"opening": make_original_readonly_obligation_member(changed_opening)}
            )
        )
    with pytest.raises(RecoveryRecordIntegrityError, match="spec reducer"):
        prepare_readonly_recovery_semantic_reduction(
            request.model_copy(
                update={"opening_spec": spec.model_copy(update={"reducer_id": "other"})}
            )
        )
    forged_evidence = evidence.model_copy(
        update={
            "source": evidence.source.model_copy(update={"physical_record": head("other-evidence")})
        }
    )
    with pytest.raises(RecoveryRecordIntegrityError, match="evidence"):
        prepare_readonly_recovery_semantic_reduction(
            request.model_copy(update={"evidence": forged_evidence})
        )
    decoded_outcome = decode_readonly_record_member(outcome).record
    assert isinstance(decoded_outcome, ReadOnlyAttemptOutcomeRecord)
    wrong_role2 = _member(
        "READONLY_ATTEMPT_OUTCOME",
        decoded_outcome.model_copy(update={"accepted": head("other-accepted")}),
    )
    with pytest.raises(RecoveryRecordIntegrityError, match="accepted"):
        prepare_readonly_recovery_semantic_reduction(
            request.model_copy(update={"outcome": wrong_role2})
        )
