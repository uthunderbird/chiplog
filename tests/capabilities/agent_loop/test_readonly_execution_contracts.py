"""Public wire consumers; construction is not positive proof of read-only execution."""

import hashlib
import json

import pytest
from pydantic import TypeAdapter, ValidationError
from tests.support.execution_fan_out import fixture
from tests.support.fan_out_shapes import head

from chiplog.capabilities.agent_loop import readonly_execution_contracts as ro
from chiplog.capabilities.agent_loop.call_acceptance_contracts import (
    InitializedCallRecord,
    InitializedReadOnlyLineage,
    SealedCallInput,
)
from chiplog.capabilities.agent_loop.execution_contracts import (
    ExecutionContinue,
    ExecutionRunRecord,
)
from chiplog.capabilities.agent_loop.recovery_contracts import (
    Absent,
    NotApplicable,
    OriginalObligationBinding,
)
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import (
    PendingCallFrontier,
    ReadOnlyAcceptedCall,
    ReadOnlyAttemptMember,
    ReadOnlyRetryLineage,
    SuccessResult,
    TerminalCallFrontier,
)


def proof() -> ro.RegisteredReadOnlyProof:
    return ro.RegisteredReadOnlyProof(
        registry=head("registry"),
        tool_schema=head("tool"),
        tool_policy=head("policy"),
        implementation=head("implementation"),
        no_mutation_proof=head("proof"),
        proof_schema="no-mutation.v1",
        canonical_proof_bytes=b"\xff\x00proof",
        query_identity=head("query"),
        canonical_query_bytes=b"\x80\x00query",
        snapshot=head("snapshot"),
        snapshot_frontier=7,
        snapshot_contract=head("snapshot-contract"),
    )


async def attempt() -> ro.PrepareReadOnlyAttempt:
    captured = await fixture()
    lineage = ReadOnlyRetryLineage(
        lineage_id="original-lineage",
        original_call_id="original-call",
        max_attempts=2,
        budget_version="budget.v1",
        reducer_id="reducer",
        reducer_version="1",
    )
    values = captured.request.ordered_calls[0].model_dump()
    values.update(
        classification="READ_ONLY",
        retry_lineage=InitializedReadOnlyLineage(lineage=lineage),
    )
    initialized = InitializedCallRecord(
        original_call_id=lineage.original_call_id,
        call=SealedCallInput.model_validate(values),
        predecessor=Absent(),
    )
    return ro.PrepareReadOnlyAttempt(
        command_id="accept-read",
        run=captured.captured_run,
        observed=ro.ReadOnlyLineageSnapshot(
            initialized_head=head("initialized"),
            initialized=initialized,
            lineage=lineage,
            complete_ordered_attempts=(),
            shared_counter=head("original-counter"),
            attempts_consumed=0,
            call_outcome=Absent(),
            terminal=Absent(),
            pending=Absent(),
            complete_manifest_fingerprint="a" * 64,
        ),
        proof=proof(),
        route=ro.SameRunReadOnlyAttempt(pending=Absent()),
        cut=ro.ReadOnlyCurrentCut(
            tenant_id="tenant",
            database_id="database",
            tenant_commit_sequence=7,
            materialization_commitment="a" * 64,
            authority_registry=head("registry"),
            sources=captured.request.cut.sources,
            applicability=captured.request.cut.fence,
        ),
        fence=captured.request.cut.fence,
    )


async def accepted() -> ro.ReadOnlyAttemptAcceptedRecord:
    request = await attempt()
    return ro.ReadOnlyAttemptAcceptedRecord(
        original_call_id=request.observed.lineage.original_call_id,
        lineage_id=request.observed.lineage.lineage_id,
        attempt_id="attempt:0",
        ordinal=0,
        initialized=request.observed.initialized_head,
        predecessor_attempt=Absent(),
        active_run=head("original-run"),
        proof=request.proof,
        previous_counter=request.observed.shared_counter,
        next_counter=head("counter:1"),
        fence=request.fence,
    )


async def pending() -> PendingCallFrontier:
    request = await attempt()
    return PendingCallFrontier(
        original_call_id="original-call",
        response_id="original-response",
        pending=head("pending").revision,
        terminal=Absent(),
        call_outcome=Absent(),
        initialized=head("initialized").revision,
        lineage=request.observed.lineage,
        ordered_attempts=(
            ReadOnlyAttemptMember(
                ordinal=0,
                attempt_id="attempt:0",
                initialized=head("initialized").revision,
                accepted=head("accepted").revision,
                outcome=head("retryable-failure").revision,
                result_or_obligation=head("result").revision,
                predecessor=Absent(),
            ),
        ),
        last_retryable_failure=head("retryable-failure").revision,
        counter=head("counter:1").revision,
        attempts_consumed=1,
        next_ordinal=1,
        readonly_proof=head("proof").revision,
        snapshot=head("snapshot").revision,
        execution_binding=head("execution").revision,
        crossed_binding_heads=(head("changed-policy").revision,),
        closure_registry_id="closure",
        closure_registry_version="1",
    )


async def test_first_acceptance_preserves_original_lineage_and_binary_proof() -> None:
    request = await attempt()
    adapter: TypeAdapter[ro.ReadOnlyPreparationRequest] = TypeAdapter(ro.ReadOnlyPreparationRequest)
    restored = adapter.validate_json(request.canonical_bytes())
    assert restored == request
    assert isinstance(restored, ro.PrepareReadOnlyAttempt)
    assert restored.proof.canonical_proof_bytes == b"\xff\x00proof"
    assert restored.proof.canonical_query_bytes == b"\x80\x00query"
    lineage = request.observed.initialized.call.retry_lineage
    assert isinstance(lineage, InitializedReadOnlyLineage)
    assert restored.observed.lineage == lineage.lineage
    record = await accepted()
    assert ro.ReadOnlyAttemptAcceptedRecord.model_validate_json(record.canonical_bytes()) == record
    with pytest.raises(ValidationError):
        ReadOnlyAcceptedCall.model_validate_json(record.canonical_bytes())
    wire = record.model_dump()
    wire["external_effect_intent"] = head("effect")
    with pytest.raises(ValidationError):
        ro.ReadOnlyAttemptAcceptedRecord.model_validate(wire)


@pytest.mark.parametrize(
    "field",
    [
        "registry",
        "implementation",
        "no_mutation_proof",
        "canonical_proof_bytes",
        "canonical_query_bytes",
        "snapshot",
        "snapshot_frontier",
        "snapshot_contract",
    ],
)
def test_positive_proof_cannot_be_replaced_by_readonly_flag(field: str) -> None:
    wire = proof().model_dump()
    del wire[field]
    wire["readonly"] = True
    with pytest.raises(ValidationError):
        ro.RegisteredReadOnlyProof.model_validate(wire)


def test_attempt_outcomes_are_closed_and_recovery_preserves_original_obligation() -> None:
    obligation = OriginalObligationBinding(
        original_run_id="original-run",
        original_call_id="original-call",
        obligation_id="obligation",
        obligation_stream_id="original-stream",
        obligation_head="original-head",
        closure_predicate_id="closure",
        closure_predicate_version="1",
        resolver_id="resolver",
        resolver_version="1",
        reducer_id="reducer",
        reducer_version="1",
        evidence_stream_id="original-evidence",
        evidence_head=Absent(),
    )
    outcomes: tuple[ro.ReadOnlyAttemptDisposition, ...] = (
        ro.ReadOnlySucceeded(
            result=head("result"), result_schema="result.v1", canonical_result_bytes=b"\xffsuccess"
        ),
        ro.ReadOnlyRetryableFailure(
            result=head("result"),
            error_class="temporary",
            reducer_rule=head("retry-rule"),
            result_schema="error.v1",
            canonical_result_bytes=b"\xfferror",
        ),
        ro.ReadOnlyFinalFailure(
            result=head("result"),
            error_class="permanent",
            reducer_rule=head("final-rule"),
            result_schema="error.v1",
            canonical_result_bytes=b"\xfferror",
        ),
        ro.ReadOnlyUnknown(
            result=head("result"),
            no_retry_boundary=head("no-retry"),
            result_schema="unknown.v1",
            canonical_result_bytes=b"\xffunknown",
        ),
        ro.ReadOnlyRecoveryRequired(obligation=obligation, result=Absent()),
    )
    adapter: TypeAdapter[ro.ReadOnlyAttemptDisposition] = TypeAdapter(ro.ReadOnlyAttemptDisposition)
    for outcome in outcomes:
        assert adapter.validate_json(outcome.canonical_bytes()) == outcome
        wire = outcome.model_dump()
        wire["safe_to_retry"] = True
        with pytest.raises(ValidationError):
            adapter.validate_python(wire)
    with pytest.raises(ValidationError):
        adapter.validate_python({"kind": "FAILED_DEFINITE", "result": head("result")})


async def test_successor_pending_route_retains_exact_original_heads_without_new_budget() -> None:
    branch = await pending()
    route = ro.SuccessorReadOnlyAttempt(
        exact_pending=branch,
        predecessor_run=head("original-run"),
        successor_initialization=head("successor-init"),
        inherited_pending_reference=head("inherited"),
    )
    restored = ro.SuccessorReadOnlyAttempt.model_validate_json(route.canonical_bytes())
    assert restored.exact_pending == branch
    assert restored.exact_pending.counter == head("counter:1").revision
    with pytest.raises(ValidationError):
        ro.SameRunReadOnlyAttempt.model_validate_json(route.canonical_bytes())
    wire = route.model_dump()
    wire["new_lineage"] = "replacement"
    with pytest.raises(ValidationError):
        ro.SuccessorReadOnlyAttempt.model_validate(wire)
    transition = ro.ReadOnlyPendingTransition(
        previous=branch, closure="NEXT_ATTEMPT_ACCEPTED", replacement=branch
    )
    # Identical replacement is intentionally only representable; owner rejects it at runtime.
    assert (
        ro.ReadOnlyPendingTransition.model_validate_json(transition.canonical_bytes()) == transition
    )
    wire_transition = transition.model_dump()
    del wire_transition["replacement"]
    with pytest.raises(ValidationError):
        ro.ReadOnlyPendingTransition.model_validate(wire_transition)


async def test_all_preparation_requests_retain_complete_lineage_and_cut() -> None:
    first = await attempt()
    requests: tuple[ro.ReadOnlyPreparationRequest, ...] = (
        first,
        ro.PrepareReadOnlyOutcome(
            command_id="outcome",
            observed=first.observed,
            accepted_attempt=head("accepted"),
            source_evidence=head("source"),
            evidence_schema="source.v1",
            canonical_evidence_bytes=b"\xffsource",
            cut=first.cut,
        ),
        ro.PrepareReadOnlyPending(
            command_id="pending",
            run=first.run,
            observed=first.observed,
            current_proof=first.proof,
            crossed_binding_heads=(head("changed"),),
            closure_registry=head("closure"),
            cut=first.cut,
            fence=first.fence,
        ),
        ro.PrepareReadOnlyReduction(
            command_id="reduce",
            observed=first.observed,
            reducer=head("reducer"),
            reducer_schema="reducer.v1",
            canonical_reducer_bytes=b"\xffreducer",
            cut=first.cut,
        ),
    )
    adapter: TypeAdapter[ro.ReadOnlyPreparationRequest] = TypeAdapter(ro.ReadOnlyPreparationRequest)
    for request in requests:
        assert adapter.validate_json(request.canonical_bytes()) == request
        wire = request.model_dump()
        del wire["observed"]
        with pytest.raises(ValidationError):
            adapter.validate_python(wire)


@pytest.mark.parametrize("limit", [False, 0, -1, 2**64, "1"])
def test_history_query_requires_strict_positive_bound(limit: object) -> None:
    with pytest.raises(ValidationError):
        ro.ConversationHistoryQuery.model_validate({"limit": limit, "after_cursor": None})


async def test_reducer_proposal_keeps_call_outcome_terminal_and_closed_counter_together() -> None:
    branch = await pending()
    counter = ro.ReadOnlyCounterRecord(
        original_call_id=branch.original_call_id,
        lineage_id=branch.lineage.lineage_id,
        predecessor=head("counter:1"),
        attempts_consumed=1,
        closed=True,
    )
    acceptance = ReadOnlyAcceptedCall(
        initialized=branch.initialized,
        accepted=branch.ordered_attempts[0].accepted,
        lineage_id=branch.lineage.lineage_id,
        ordinal=0,
        readonly_proof=branch.readonly_proof,
        snapshot_binding=branch.snapshot,
        lineage=branch.lineage,
        complete_ordered_attempts=branch.ordered_attempts,
        shared_counter=head("closed-counter").revision,
        attempts_consumed=1,
        call_level_outcome=head("call-outcome").revision,
        complete_lineage_reducer_batch=head("reducer-batch").revision,
    )
    terminal = TerminalCallFrontier(
        original_call_id=branch.original_call_id,
        response_id=branch.response_id,
        acceptance=acceptance,
        disposition=SuccessResult(
            terminal=head("terminal").revision,
            result=head("result").revision,
            recovered_outcome=NotApplicable(),
        ),
    )
    # This fixture tests retention, not eligibility: runtime must reject reducing
    # a retryable failure to success, even though the complete proposal is representable.
    outcome = ro.ReadOnlyCallOutcomeRecord(
        original_call_id=branch.original_call_id,
        lineage=branch.lineage,
        complete_ordered_outcomes=(head("retryable-failure"),),
        complete_manifest_fingerprint="a" * 64,
        selected_attempt_outcome=head("retryable-failure"),
        disposition="SUCCEEDED",
        provenance="ATTEMPT_RESULT",
        result_or_obligation=head("result"),
        terminal_disposition=head("terminal"),
        closed_counter=head("closed-counter"),
    )
    proposal = ro.PreparedReadOnlyReduction(
        source_request_fingerprint="b" * 64,
        call_outcome=outcome,
        terminal=terminal,
        counter=counter,
        pending_transition=ro.ReadOnlyPendingTransition(
            previous=branch, closure="CALL_REDUCED_TERMINAL", replacement=terminal
        ),
        complete_batch_fingerprint="c" * 64,
    )
    adapter: TypeAdapter[ro.ReadOnlyPreparationResult] = TypeAdapter(ro.ReadOnlyPreparationResult)
    assert adapter.validate_json(proposal.canonical_bytes()) == proposal
    for omitted in ("call_outcome", "terminal", "counter", "pending_transition"):
        wire = proposal.model_dump()
        del wire[omitted]
        with pytest.raises(ValidationError):
            adapter.validate_python(wire)
    with pytest.raises(ValidationError):
        ro.PreparedReadOnlyOutcome.model_validate_json(proposal.canonical_bytes())


def test_history_wire_is_separate_from_frozen_execution_v2() -> None:
    call = ro.ReadOnlyHistoryToolCall(
        call_id="read",
        tool="read_conversation_history",
        arguments=ro.ConversationHistoryQuery(limit=20, after_cursor=None),
    )
    assert ro.ReadOnlyHistoryToolCall.model_validate_json(call.canonical_bytes()) == call
    with pytest.raises(ValidationError):
        ExecutionContinue.model_validate({"kind": "Continue", "tool_calls": (call.model_dump(),)})
    schema = json.dumps(
        ExecutionRunRecord.model_json_schema(), sort_keys=True, separators=(",", ":")
    ).encode()
    assert (
        hashlib.sha256(schema).hexdigest()
        == "b2ffa539bf5392ad3d0a203c91f6253e0760855d95313a3c54b7b889a2309333"
    )
