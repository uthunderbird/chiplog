"""Small exact-byte helpers for read-only assembly tests."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from chiplog.capabilities.agent_loop.call_acceptance_contracts import (
    CallSubjectHead,
    InitializedCallRecord,
    InitializedReadOnlyLineage,
    SealedCallInput,
)
from chiplog.capabilities.agent_loop.call_acceptance_preparation import (
    call_record_reference,
    call_subject_id,
)
from chiplog.capabilities.agent_loop.execution_recovery_observations import RecoverySourceRecord
from chiplog.capabilities.agent_loop.readonly_assembly_contracts import (
    ReadOnlyOutcomeAssemblyV1,
    SameRunReadOnlyAttemptAssemblyV1,
    SelectedReadOnlyCounterV1,
)
from chiplog.capabilities.agent_loop.readonly_execution_contracts import (
    ObservedReadOnlyAttempt,
    PreparedReadOnlyAttempt,
    PreparedReadOnlyOutcome,
    PrepareReadOnlyAttempt,
    PrepareReadOnlyOutcome,
    ReadOnlyAttemptAcceptedRecord,
    ReadOnlyAttemptOutcomeRecord,
    ReadOnlyCounterRecord,
    ReadOnlyCurrentCut,
    ReadOnlyLineageSnapshot,
    ReadOnlySucceeded,
    RegisteredReadOnlyProof,
    SameRunReadOnlyAttempt,
)
from chiplog.capabilities.agent_loop.readonly_history_tool_contracts import ReadOnlyDTO
from chiplog.capabilities.agent_loop.readonly_record_contracts import READONLY_RECORD_ROWS
from chiplog.capabilities.agent_loop.readonly_source_contracts import (
    ReadOnlyAttemptEvidenceBodyV1,
    SelectedReadOnlyAttemptEvidenceV1,
    readonly_lineage_manifest_fingerprint,
    readonly_physical_batch_fingerprint,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Absent, Present
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import ReadOnlyRetryLineage
from chiplog.capabilities.agent_loop.recovery_record_contracts import RecoveryRecordMember
from tests.support.execution_fan_out import fixture as execution_fixture


def readonly_member(kind: str, record: ReadOnlyDTO) -> RecoveryRecordMember:
    """Create one registered content-addressed row from original canonical bytes."""
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


def physical(member: RecoveryRecordMember, subject_id: str) -> CallSubjectHead:
    return CallSubjectHead(
        subject_id=subject_id,
        revision=Present(head=member.record_id, fingerprint=member.fingerprint),
    )


def _head(name: str) -> CallSubjectHead:
    return CallSubjectHead(
        subject_id=name,
        revision=Present(
            head="physical:" + name, fingerprint=hashlib.sha256(name.encode()).hexdigest()
        ),
    )


def _proof() -> RegisteredReadOnlyProof:
    return RegisteredReadOnlyProof(
        registry=_head("registry"),
        tool_schema=_head("tool-schema"),
        tool_policy=_head("policy"),
        implementation=_head("implementation"),
        no_mutation_proof=_head("proof"),
        proof_schema="proof.v1",
        canonical_proof_bytes=b"proof",
        query_identity=_head("query"),
        canonical_query_bytes=b"query",
        snapshot=_head("snapshot"),
        snapshot_frontier=1,
        snapshot_contract=_head("snapshot-contract"),
    )


@dataclass(frozen=True)
class SameRunFixture:
    attempt: SameRunReadOnlyAttemptAssemblyV1
    outcome: ReadOnlyOutcomeAssemblyV1


async def same_run_first_attempt_fixture() -> SameRunFixture:
    """Build one fully native first attempt and its subsequent selected Outcome."""
    captured = await execution_fixture()
    run = captured.captured_run
    original = captured.request.ordered_calls[0].original
    original_call_id = call_subject_id(original)
    lineage = ReadOnlyRetryLineage(
        lineage_id="readonly-lineage",
        original_call_id=original_call_id,
        max_attempts=2,
        budget_version="budget.v1",
        reducer_id="reducer",
        reducer_version="1",
    )
    values = captured.request.ordered_calls[0].model_dump()
    values.update(
        classification="READ_ONLY", retry_lineage=InitializedReadOnlyLineage(lineage=lineage)
    )
    initialized = InitializedCallRecord(
        original_call_id=original_call_id,
        call=SealedCallInput.model_validate(values),
        predecessor=Absent(),
    )
    initialized_head = call_record_reference(original_call_id, initialized)
    old_counter = ReadOnlyCounterRecord(
        original_call_id=original_call_id,
        lineage_id=lineage.lineage_id,
        predecessor=_head("counter-root"),
        attempts_consumed=0,
        closed=False,
    )
    old_counter_member = readonly_member("READONLY_COUNTER", old_counter)
    shared_counter = physical(old_counter_member, "stable-counter-subject")
    cut = ReadOnlyCurrentCut(
        tenant_id=run.tenant,
        database_id="database",
        tenant_commit_sequence=1,
        materialization_commitment="a" * 64,
        authority_registry=_head("authority"),
        sources=captured.request.cut.sources,
        applicability=captured.request.cut.fence,
    )
    snapshot = ReadOnlyLineageSnapshot(
        initialized_head=initialized_head,
        initialized=initialized,
        lineage=lineage,
        complete_ordered_attempts=(),
        shared_counter=shared_counter,
        attempts_consumed=0,
        call_outcome=Absent(),
        terminal=Absent(),
        pending=Absent(),
        complete_manifest_fingerprint="0" * 64,
    )
    snapshot = snapshot.model_copy(
        update={"complete_manifest_fingerprint": readonly_lineage_manifest_fingerprint(snapshot)}
    )
    proof = _proof()
    request = PrepareReadOnlyAttempt(
        command_id="attempt",
        run=run,
        observed=snapshot,
        proof=proof,
        route=SameRunReadOnlyAttempt(pending=Absent()),
        cut=cut,
        fence=captured.request.cut.fence,
    )
    new_counter = ReadOnlyCounterRecord(
        original_call_id=original_call_id,
        lineage_id=lineage.lineage_id,
        predecessor=shared_counter,
        attempts_consumed=1,
        closed=False,
    )
    new_counter_member = readonly_member("READONLY_COUNTER", new_counter)
    accepted = ReadOnlyAttemptAcceptedRecord(
        original_call_id=original_call_id,
        lineage_id=lineage.lineage_id,
        attempt_id="attempt-0",
        ordinal=0,
        initialized=initialized_head,
        predecessor_attempt=Absent(),
        active_run=CallSubjectHead(
            subject_id=run.run_id, revision=Present(head=run.head, fingerprint=run.digest())
        ),
        proof=proof,
        previous_counter=shared_counter,
        next_counter=physical(new_counter_member, shared_counter.subject_id),
        fence=captured.request.cut.fence,
    )
    accepted_member = readonly_member("READONLY_ATTEMPT_ACCEPTED", accepted)
    members = (new_counter_member, accepted_member)
    result = PreparedReadOnlyAttempt(
        source_request_fingerprint=hashlib.sha256(request.canonical_bytes()).hexdigest(),
        acceptance=accepted,
        counter=new_counter,
        pending_transition=None,
        complete_batch_fingerprint=readonly_physical_batch_fingerprint(members),
    )
    attempt = SameRunReadOnlyAttemptAssemblyV1(
        request=request,
        selected_counter=SelectedReadOnlyCounterV1(
            member=old_counter_member, selected_decision=_head("counter-selection")
        ),
        result=result,
        members=members,
    )
    observed = snapshot.model_copy(
        update={
            "complete_ordered_attempts": (
                ObservedReadOnlyAttempt(
                    accepted_head=physical(accepted_member, accepted.attempt_id),
                    accepted=accepted,
                    outcome=Absent(),
                ),
            ),
            "shared_counter": physical(new_counter_member, shared_counter.subject_id),
            "attempts_consumed": 1,
        }
    )
    observed = observed.model_copy(
        update={"complete_manifest_fingerprint": readonly_lineage_manifest_fingerprint(observed)}
    )
    body = ReadOnlyAttemptEvidenceBodyV1(
        tenant_id=cut.tenant_id,
        database_id=cut.database_id,
        original_call_id=original_call_id,
        lineage_id=lineage.lineage_id,
        attempt_id=accepted.attempt_id,
        ordinal=accepted.ordinal,
        query_identity=proof.query_identity,
        snapshot=proof.snapshot,
        implementation=proof.implementation,
        proof=proof.no_mutation_proof,
        disposition=ReadOnlySucceeded(
            result=_head("result"), result_schema="result.v1", canonical_result_bytes=b"result"
        ),
    )
    raw = body.canonical_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    evidence_ref = CallSubjectHead(
        subject_id=body.attempt_id,
        revision=Present(head=body.schema_id + ":" + digest, fingerprint=digest),
    )
    source = RecoverySourceRecord(
        owner="broker",
        subject=evidence_ref,
        schema_id=body.schema_id,
        canonical_record_bytes=raw,
        selected_decision=_head("evidence-selection"),
        physical_record=evidence_ref,
    )
    outcome_request = PrepareReadOnlyOutcome(
        command_id="outcome",
        observed=observed,
        accepted_attempt=physical(accepted_member, accepted.attempt_id),
        source_evidence=evidence_ref,
        evidence_schema=body.schema_id,
        canonical_evidence_bytes=raw,
        cut=cut,
    )
    outcome = ReadOnlyAttemptOutcomeRecord(
        original_call_id=original_call_id,
        lineage_id=lineage.lineage_id,
        attempt_id=accepted.attempt_id,
        ordinal=accepted.ordinal,
        accepted=outcome_request.accepted_attempt,
        source_evidence=evidence_ref,
        disposition=body.disposition,
    )
    outcome_member = readonly_member("READONLY_ATTEMPT_OUTCOME", outcome)
    outcome_result = PreparedReadOnlyOutcome(
        source_request_fingerprint=hashlib.sha256(outcome_request.canonical_bytes()).hexdigest(),
        outcome=outcome,
        complete_batch_fingerprint=readonly_physical_batch_fingerprint((outcome_member,)),
    )
    return SameRunFixture(
        attempt=attempt,
        outcome=ReadOnlyOutcomeAssemblyV1(
            request=outcome_request,
            result=outcome_result,
            accepted=accepted_member,
            accepted_selected_decision=_head("accepted-selection"),
            evidence=SelectedReadOnlyAttemptEvidenceV1(source=source, body=body),
            members=(outcome_member,),
        ),
    )
