"""Real, canonical recovery-member fixtures shared by recovery codec consumers."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.execution_recovery_contracts import ExecutionSuccessorEdge
from chiplog.capabilities.agent_loop.execution_recovery_observations import (
    ContinuationReadyRecord,
    ExecutionSuspensionBaseline,
    ExecutionSuspensionPair,
    ExecutionTerminalManifest,
    SealedAccountingRecord,
)
from chiplog.capabilities.agent_loop.model_attempt_recovery_contracts import (
    LateExecutionResponseRecord,
    RetainLateExecutionResponse,
)
from chiplog.capabilities.agent_loop.original_recovery_contracts import (
    ConsumableReduction,
    LoopRecoveryStream,
    LoopSemanticReductionRecord,
    OriginalObligationClosureRecord,
    OriginalResolutionBasis,
    OriginalResolverBatchRecord,
    RecoveredCallOutcomeRecord,
    ResolvedReductionAnchor,
)
from chiplog.capabilities.agent_loop.readonly_execution_contracts import (
    ReadOnlyAttemptAcceptedRecord,
    ReadOnlyAttemptOutcomeRecord,
    ReadOnlyCallOutcomeRecord,
    ReadOnlyCounterRecord,
    ReadOnlySucceeded,
    RegisteredReadOnlyProof,
)
from chiplog.capabilities.agent_loop.recovery_contracts import (
    Absent,
    NonSchedulerFence,
    NotApplicable,
    OriginalObligationBinding,
    Present,
    RecoveryDTO,
)
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import (
    FrontierMember,
    FrozenRunBindings,
    ReadOnlyRetryLineage,
    RecoveryFrontier,
    RecoveryFrontierRegistry,
    RecoveryRegistryRow,
    ReferenceExternalObligation,
)
from chiplog.capabilities.agent_loop.recovery_record_contracts import (
    AGENT_LOOP_OWNER,
    RECOVERY_RECORD_ROWS,
    RecoveryRecordMember,
)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def present(name: str) -> Present:
    return Present(head="physical:" + name, fingerprint=digest(name))


def head(name: str) -> CallSubjectHead:
    return CallSubjectHead(subject_id=name, revision=present(name))


def member_for(kind: str, record: RecoveryDTO) -> RecoveryRecordMember:
    row = next(row for row in RECOVERY_RECORD_ROWS if row.record_kind == kind)
    raw = record.canonical_bytes()
    fingerprint = hashlib.sha256(raw).hexdigest()
    record_id = (
        str(getattr(record, row.identity_field))
        if row.identity_field is not None
        else row.schema_id + ":" + fingerprint
    )
    return RecoveryRecordMember(
        owner=AGENT_LOOP_OWNER,
        record_kind=row.record_kind,
        schema_id=row.schema_id,
        record_id=record_id,
        canonical_record_bytes=raw,
        fingerprint=fingerprint,
    )


def physical(member: RecoveryRecordMember, subject: str | None = None) -> CallSubjectHead:
    return CallSubjectHead(
        subject_id=subject or member.record_id,
        revision=Present(head=member.record_id, fingerprint=member.fingerprint),
    )


def obligation() -> OriginalObligationBinding:
    return OriginalObligationBinding(
        original_run_id="original-run",
        original_call_id="original-call",
        obligation_id="obligation",
        obligation_stream_id="obligation-stream",
        obligation_head="open-obligation",
        closure_predicate_id="closure-predicate",
        closure_predicate_version="1",
        resolver_id="resolver",
        resolver_version="1",
        reducer_id="reducer",
        reducer_version="1",
        evidence_stream_id="evidence-stream",
        evidence_head=Absent(),
    )


def fence() -> NonSchedulerFence:
    return NonSchedulerFence(
        lineage=NotApplicable(),
        physical_root=NotApplicable(),
        lease=NotApplicable(),
        clock_proof=NotApplicable(),
        run_id="active-run",
        run_head="active-run-head",
        worker_session_id="worker",
        runtime_generation="generation-1",
    )


def bindings() -> FrozenRunBindings:
    return FrozenRunBindings(
        objective="recover valid chain",
        requested_work="verify records",
        prompt_artifact=present("prompt"),
        ordered_tool_specs=(present("tool"),),
        generated_schema=present("schema"),
        semantic_bindings=(),
        recipient_effect_bindings=(),
        authority_scope=present("scope"),
        authority_mandate_heads=(),
        policy=present("policy"),
        no_retry_boundaries=(),
    )


def readonly_proof() -> RegisteredReadOnlyProof:
    return RegisteredReadOnlyProof(
        registry=head("registry"),
        tool_schema=head("tool-schema"),
        tool_policy=head("tool-policy"),
        implementation=head("implementation"),
        no_mutation_proof=head("no-mutation"),
        proof_schema="proof.v1",
        canonical_proof_bytes=b"proof",
        query_identity=head("query"),
        canonical_query_bytes=b"query",
        snapshot=head("snapshot"),
        snapshot_frontier=4,
        snapshot_contract=head("snapshot-contract"),
    )


@dataclass(frozen=True)
class RecoveryRecords:
    members: dict[str, RecoveryRecordMember]
    suspended_run: CallSubjectHead
    selected_decision: CallSubjectHead
    original: OriginalObligationBinding
    terminal: CallSubjectHead


def records() -> RecoveryRecords:
    """Build all sixteen rows in physical dependency order, using production DTOs."""
    original = obligation()
    accounting = SealedAccountingRecord(
        accounting_id="accounting",
        source_cut_fingerprint=digest("cut"),
        sealed_response=head("response"),
        sealed_manifest_fingerprint=digest("seal"),
        registry=head("registry"),
        frontier=head("frontier"),
        complete_ordered_calls=(),
    )
    accounting_member = member_for("SEALED_ACCOUNTING", accounting)
    accounting_second = accounting.model_copy(
        update={"accounting_id": "accounting-second", "sealed_response": head("response-second")}
    )
    accounting_second_member = member_for("SEALED_ACCOUNTING", accounting_second)
    continuation = ContinuationReadyRecord(
        readiness_id="continuation",
        source_cut_fingerprint=digest("cut"),
        accounting=accounting,
        complete_original_closures=(),
        complete_current_reductions=(),
    )
    baseline = ExecutionSuspensionBaseline(
        baseline_id="baseline",
        suspension_command_id="suspend",
        run_id="suspended-run",
        predecessor_run=head("predecessor"),
        source_cut_fingerprint=digest("cut"),
        frontier=_frontier(),
        bindings=bindings(),
        original_obligations=(original,),
        activation_blocking_predicates=(present("blocked"),),
    )
    baseline_member = member_for("SUSPENSION_BASELINE", baseline)
    suspended_run = head("suspended-run")
    pair = ExecutionSuspensionPair(
        suspension_command_id="suspend",
        source_cut_fingerprint=digest("cut"),
        predecessor_run=head("predecessor"),
        baseline=physical(baseline_member, "baseline"),
        suspended_run=suspended_run,
    )
    terminal_manifest = ExecutionTerminalManifest(
        manifest_id="terminal-manifest",
        command_id="terminal-command",
        prior_run=head("prior-run"),
        target="CANCELLED",
        source_cut_fingerprint=digest("cut"),
        complete_accounting=(accounting, accounting_second),
        complete_open_original_obligations=(original,),
    )
    basis = OriginalResolutionBasis(
        basis_id="basis",
        source_request_fingerprint=digest("request"),
        original=original,
        original_terminal=head("original-terminal"),
        selected_witness=head("witness"),
        complete_evidence_manifest=(head("witness"),),
        resolver_policy=head("resolver-policy"),
        reducer_policy=head("reducer-policy"),
        reduction_id="reduction",
        accepted_semantic_class="confirmed",
    )
    basis_member = member_for("ORIGINAL_RESOLUTION_BASIS", basis)
    closure = OriginalObligationClosureRecord(
        closure_id="closure",
        basis=physical(basis_member, "basis"),
        original=original,
        prior_obligation=head("prior-obligation"),
        accepted_witness=head("witness"),
        closure_predicate=head("closure-predicate"),
    )
    closure_member = member_for("ORIGINAL_OBLIGATION_CLOSURE", closure)
    outcome = RecoveredCallOutcomeRecord(
        outcome_id="outcome",
        basis=physical(basis_member, "basis"),
        original=original,
        closure=physical(closure_member, "closure"),
        accepted_witness=head("witness"),
        result_schema="result.v1",
        canonical_result_bytes=b"result",
        accepted_semantic_class="confirmed",
    )
    outcome_member = member_for("RECOVERED_CALL_OUTCOME", outcome)
    batch = OriginalResolverBatchRecord(
        batch_id="batch",
        command_id="resolve",
        basis=physical(basis_member, "basis"),
        closure=physical(closure_member, "closure"),
        recovered_outcome=physical(outcome_member, "outcome"),
        source_cut_fingerprint=digest("cut"),
    )
    batch_member = member_for("ORIGINAL_RESOLVER_BATCH", batch)
    reduction = LoopSemanticReductionRecord(
        stream=LoopRecoveryStream(
            stream_id="evidence-stream",
            registry=head("registry"),
            subject=head("stream-subject"),
            schema_id="stream.v1",
        ),
        reduction_id="reduction",
        reducer_id="reducer",
        reducer_version="1",
        predecessor=Absent(),
        complete_ordered_evidence=(head("witness"),),
        anchor=ResolvedReductionAnchor(
            original=original,
            accepted_witness=head("witness"),
            recovered_outcome=physical(outcome_member, "outcome"),
            closure=physical(closure_member, "closure"),
            resolver_batch=physical(batch_member, "batch"),
            accepted_semantic_class="confirmed",
        ),
        disposition=ConsumableReduction(semantic_class="confirmed"),
    )
    open_counter = ReadOnlyCounterRecord(
        original_call_id="readonly-call",
        lineage_id="readonly-lineage",
        predecessor=head("counter-root"),
        attempts_consumed=1,
        closed=False,
    )
    open_counter_member = member_for("READONLY_COUNTER", open_counter)
    accepted = ReadOnlyAttemptAcceptedRecord(
        original_call_id="readonly-call",
        lineage_id="readonly-lineage",
        attempt_id="attempt-1",
        ordinal=0,
        initialized=head("initialized"),
        predecessor_attempt=Absent(),
        active_run=head("active-run"),
        proof=readonly_proof(),
        previous_counter=head("counter-root"),
        next_counter=physical(open_counter_member, "open-counter"),
        fence=fence(),
    )
    accepted_member = member_for("READONLY_ATTEMPT_ACCEPTED", accepted)
    readonly_outcome = ReadOnlyAttemptOutcomeRecord(
        original_call_id="readonly-call",
        lineage_id="readonly-lineage",
        attempt_id="attempt-1",
        ordinal=0,
        accepted=physical(accepted_member, "accepted"),
        source_evidence=head("readonly-evidence"),
        disposition=ReadOnlySucceeded(
            result=head("result"), result_schema="result.v1", canonical_result_bytes=b"success"
        ),
    )
    readonly_outcome_member = member_for("READONLY_ATTEMPT_OUTCOME", readonly_outcome)
    closed_counter = ReadOnlyCounterRecord(
        original_call_id="readonly-call",
        lineage_id="readonly-lineage",
        predecessor=physical(open_counter_member, "open-counter"),
        attempts_consumed=1,
        closed=True,
    )
    closed_counter_member = member_for("READONLY_COUNTER", closed_counter)
    terminal = head("readonly-terminal")
    call_outcome = ReadOnlyCallOutcomeRecord(
        original_call_id="readonly-call",
        lineage=ReadOnlyRetryLineage(
            lineage_id="readonly-lineage",
            original_call_id="readonly-call",
            max_attempts=2,
            budget_version="budget-1",
            reducer_id="reducer",
            reducer_version="1",
        ),
        complete_ordered_outcomes=(physical(readonly_outcome_member, "readonly-outcome"),),
        complete_manifest_fingerprint=digest("readonly-manifest"),
        selected_attempt_outcome=physical(readonly_outcome_member, "readonly-outcome"),
        disposition="SUCCEEDED",
        provenance="ATTEMPT_RESULT",
        result_or_obligation=head("result"),
        terminal_disposition=terminal,
        closed_counter=physical(closed_counter_member, "closed-counter"),
    )
    edge = ExecutionSuccessorEdge(
        command_id="successor",
        original_pair=head("original-pair"),
        predecessor_before=head("predecessor"),
        predecessor_superseded=head("superseded"),
        successor_created=head("successor"),
        source_cut_fingerprint=digest("cut"),
        disposition_version="1",
        changed_binding_manifest=(head("binding"),),
        complete_initialization=(head("initialization"),),
        original_obligations=(
            ReferenceExternalObligation(original=original, observation_frontier=1),
        ),
        inherited_no_retry_boundaries=(head("no-retry"),),
        inherited_pending_branches=(head("pending"),),
        observation_frontier=1,
        fence=fence(),
    )
    late = LateExecutionResponseRecord(
        evidence_id="late-evidence",
        request=RetainLateExecutionResponse(
            command_id="late",
            original_run=head("original-run"),
            original_attempt=head("attempt"),
            original_manifest=head("manifest"),
            lineage_id="lineage",
            generation=1,
            receipt_token=head("receipt"),
            selected_custody=head("custody"),
            source_authentication=head("source-auth"),
            raw_response=b"late",
            transport_receipt=b"receipt",
        ),
    )
    values = {
        "SEALED_ACCOUNTING": accounting_member,
        "SEALED_ACCOUNTING_SECOND": accounting_second_member,
        "CONTINUATION_READY": member_for("CONTINUATION_READY", continuation),
        "SUSPENSION_BASELINE": baseline_member,
        "SUSPENSION_PAIR": member_for("SUSPENSION_PAIR", pair),
        "TERMINAL_MANIFEST": member_for("TERMINAL_MANIFEST", terminal_manifest),
        "SUCCESSOR_EDGE": member_for("SUCCESSOR_EDGE", edge),
        "ORIGINAL_RESOLUTION_BASIS": basis_member,
        "ORIGINAL_OBLIGATION_CLOSURE": closure_member,
        "RECOVERED_CALL_OUTCOME": outcome_member,
        "ORIGINAL_RESOLVER_BATCH": batch_member,
        "LOOP_SEMANTIC_REDUCTION": member_for("LOOP_SEMANTIC_REDUCTION", reduction),
        "READONLY_ATTEMPT_ACCEPTED": accepted_member,
        "READONLY_ATTEMPT_OUTCOME": readonly_outcome_member,
        "READONLY_COUNTER_OPEN": open_counter_member,
        "READONLY_COUNTER_CLOSED": closed_counter_member,
        "READONLY_CALL_OUTCOME": member_for("READONLY_CALL_OUTCOME", call_outcome),
        "LATE_EXECUTION_RESPONSE": member_for("LATE_EXECUTION_RESPONSE", late),
    }
    return RecoveryRecords(values, suspended_run, head("selected-decision"), original, terminal)


def records_with_closed_acceptance_counter() -> RecoveryRecords:
    """Rebuild the read-only suffix around a canonically closed acceptance counter."""
    fixture = records()
    members = dict(fixture.members)
    open_counter = fixture.members["READONLY_COUNTER_OPEN"]
    accepted = fixture.members["READONLY_ATTEMPT_ACCEPTED"]
    outcome = fixture.members["READONLY_ATTEMPT_OUTCOME"]
    closed_counter = fixture.members["READONLY_COUNTER_CLOSED"]
    call_outcome = fixture.members["READONLY_CALL_OUTCOME"]

    closed_acceptance_counter = ReadOnlyCounterRecord.model_validate_json(
        open_counter.canonical_record_bytes
    ).model_copy(update={"closed": True})
    closed_acceptance_member = member_for("READONLY_COUNTER", closed_acceptance_counter)
    rebuilt_accepted = ReadOnlyAttemptAcceptedRecord.model_validate_json(
        accepted.canonical_record_bytes
    ).model_copy(update={"next_counter": physical(closed_acceptance_member, "open-counter")})
    rebuilt_accepted_member = member_for("READONLY_ATTEMPT_ACCEPTED", rebuilt_accepted)
    rebuilt_outcome = ReadOnlyAttemptOutcomeRecord.model_validate_json(
        outcome.canonical_record_bytes
    ).model_copy(update={"accepted": physical(rebuilt_accepted_member, "accepted")})
    rebuilt_outcome_member = member_for("READONLY_ATTEMPT_OUTCOME", rebuilt_outcome)
    rebuilt_closed_counter = ReadOnlyCounterRecord.model_validate_json(
        closed_counter.canonical_record_bytes
    ).model_copy(update={"predecessor": physical(closed_acceptance_member, "open-counter")})
    rebuilt_closed_counter_member = member_for("READONLY_COUNTER", rebuilt_closed_counter)
    rebuilt_call_outcome = ReadOnlyCallOutcomeRecord.model_validate_json(
        call_outcome.canonical_record_bytes
    ).model_copy(
        update={
            "complete_ordered_outcomes": (physical(rebuilt_outcome_member, "readonly-outcome"),),
            "selected_attempt_outcome": physical(rebuilt_outcome_member, "readonly-outcome"),
            "closed_counter": physical(rebuilt_closed_counter_member, "closed-counter"),
        }
    )
    members.update(
        {
            "READONLY_COUNTER_OPEN": closed_acceptance_member,
            "READONLY_ATTEMPT_ACCEPTED": rebuilt_accepted_member,
            "READONLY_ATTEMPT_OUTCOME": rebuilt_outcome_member,
            "READONLY_COUNTER_CLOSED": rebuilt_closed_counter_member,
            "READONLY_CALL_OUTCOME": member_for("READONLY_CALL_OUTCOME", rebuilt_call_outcome),
        }
    )
    return RecoveryRecords(
        members=members,
        suspended_run=fixture.suspended_run,
        selected_decision=fixture.selected_decision,
        original=fixture.original,
        terminal=fixture.terminal,
    )


def _frontier() -> RecoveryFrontier:
    return RecoveryFrontier(
        tenant_id="tenant",
        run_id="suspended-run",
        tenant_commit_sequence=1,
        registry=RecoveryFrontierRegistry(
            registry_id="registry",
            version="1",
            fingerprint=digest("registry"),
            ordered_rows=(
                RecoveryRegistryRow(
                    family="RUN",
                    ordinal=0,
                    subject_extractor_id="run",
                    cardinality_rule="one",
                    terminal_conflict_rule="reject",
                    serialization_rule="canonical",
                    canonicalization_version="1",
                ),
            ),
        ),
        ordered_members=(
            FrontierMember(
                family="RUN",
                subject="suspended-run",
                branch="SUSPENDED",
                ordered_heads=(present("suspended-run"),),
                fingerprint=digest("member"),
            ),
        ),
        ordered_calls=(),
        canonicalization_version="chiplog.recovery.frontier.v1",
        fingerprint=digest("frontier"),
    )
