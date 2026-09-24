"""Consumer checks for the closed Phase-C recovery exchange wire surface."""

import hashlib
import json
from collections.abc import Callable
from typing import Literal

import pytest
from tests.support.execution_fan_out import fixture as captured_fixture
from tests.support.successor_records import non_scheduler_successor
from tests.support.successor_records import pending as readonly_pending

import chiplog.capabilities.agent_loop.recovery_wire_contracts as wire
from chiplog.capabilities.agent_loop import execution_recovery_contracts as execution
from chiplog.capabilities.agent_loop import execution_recovery_observations as observations
from chiplog.capabilities.agent_loop import model_attempt_recovery_contracts as model
from chiplog.capabilities.agent_loop import original_recovery_contracts as original
from chiplog.capabilities.agent_loop import post_terminal_contracts as work
from chiplog.capabilities.agent_loop import readonly_execution_contracts as readonly
from chiplog.capabilities.agent_loop import recovery_contracts as recovery
from chiplog.capabilities.agent_loop.call_acceptance_contracts import (
    CallPreparationRejected,
    CallSubjectHead,
    InitializedCallRecord,
    InitializedReadOnlyLineage,
    SealedCallInput,
)
from chiplog.capabilities.agent_loop.execution_recovery_contracts import (
    ExecutionRecoveryClassified,
    PrepareExecutionResume,
)
from chiplog.capabilities.agent_loop.execution_recovery_observations import RecoverySourceRecord
from chiplog.capabilities.agent_loop.post_terminal_contracts import WorkPreparationRejected
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import (
    ReadOnlyAcceptedCall,
    ReadOnlyAttemptMember,
    ReadOnlyRetryLineage,
    SuccessResult,
    TerminalCallFrontier,
)
from chiplog.capabilities.agent_loop.scheduler_leases import IssuedLeaseObservation
from chiplog.capabilities.agent_loop.scheduler_rollover import IssuedRolloverObservation


def _present(name: str) -> recovery.Present:
    return recovery.Present(head=name, fingerprint="a" * 64)


def _head(name: str) -> CallSubjectHead:
    return CallSubjectHead(subject_id=name, revision=_present(name))


def _work_view() -> work.PostTerminalWorkView:
    original = recovery.OriginalObligationBinding(
        original_run_id="run",
        original_call_id="call",
        obligation_id="obligation",
        obligation_stream_id="original-stream",
        obligation_head="obligation-head",
        closure_predicate_id="all-original-children",
        closure_predicate_version="1",
        resolver_id="original-resolver",
        resolver_version="1",
        reducer_id="original-reducer",
        reducer_version="1",
        evidence_stream_id="original-evidence",
        evidence_head=recovery.Absent(),
    )
    return work.PostTerminalWorkView(
        subject=recovery.WorkSubjectBinding(
            work_id="work",
            work_subject_head="subject-head",
            work_subject_fingerprint="b" * 64,
            terminal_manifest_head="terminal",
            terminal_manifest_member_fingerprint="c" * 64,
            original_obligation=original,
        ),
        work_epoch=recovery.WorkEpochBinding(
            selector_id="selector",
            selector_head="selector-head",
            selector_version=0,
            current_epoch_id="epoch",
            current_epoch_head="epoch-head",
        ),
        work_state_head=_present("state"),
        lease=work.UnleasedWork(lease_head="genesis"),
        predecessor_rollover=recovery.Absent(),
    )


def _lease(generation: int) -> recovery.LeaseBinding:
    return recovery.LeaseBinding(
        lease_head="lease-head",
        holder_id="holder",
        holder_session_id="session",
        lease_id="lease",
        generation=generation,
        trusted_expiry=100,
        clock_contract_version="1",
    )


def _clock_proof() -> recovery.TrustedClockProofRef:
    return recovery.TrustedClockProofRef(
        proof_id="proof",
        proof_fingerprint="a" * 64,
        proof_version="1",
        clock_contract_version="1",
        fence_fingerprint="b" * 64,
        command_id="command",
        command_payload_fingerprint="c" * 64,
        submission_id="submission",
    )


def _original_binding() -> recovery.OriginalObligationBinding:
    return recovery.OriginalObligationBinding(
        original_run_id="original-run",
        original_call_id="original-call",
        obligation_id="obligation",
        obligation_stream_id="original-obligation-stream",
        obligation_head="open-head",
        closure_predicate_id="closure",
        closure_predicate_version="1",
        resolver_id="resolver",
        resolver_version="1",
        reducer_id="reducer",
        reducer_version="1",
        evidence_stream_id="original-evidence",
        evidence_head=recovery.Absent(),
    )


def _source(name: str) -> RecoverySourceRecord:
    return RecoverySourceRecord(
        owner="agent_loop",
        subject=_head(name),
        schema_id=name + ".v1",
        canonical_record_bytes=b"\xff" + name.encode(),
        selected_decision=_head("selected-" + name),
        physical_record=_head("physical-" + name),
    )


def _stream(name: str) -> original.LoopRecoveryStream:
    return original.LoopRecoveryStream(
        stream_id=name, registry=_head("registry"), subject=_head(name), schema_id="stream.v1"
    )


def _authority() -> original.IndependentResolverAuthority:
    return original.IndependentResolverAuthority(
        registered_authority=_source("authority"), authenticated_invocation=_source("invocation")
    )


@pytest.mark.parametrize(
    ("schema", "decoder"),
    [
        (wire.EXECUTION_RECOVERY_RESULT_SCHEMA, wire.decode_execution_recovery_result),
        (wire.ORIGINAL_RECOVERY_RESULT_SCHEMA, wire.decode_original_recovery_result),
        (wire.READONLY_PREPARATION_RESULT_SCHEMA, wire.decode_readonly_preparation_result),
        (wire.MODEL_RECOVERY_RESULT_SCHEMA, wire.decode_model_recovery_result),
    ],
)
def test_call_rejections_roundtrip_exact_bytes(
    schema: str, decoder: Callable[[str, bytes], object]
) -> None:
    result = CallPreparationRejected(command_id="command", code="HOLD", reason="waiting")
    decoded = decoder(schema, result.canonical_bytes())
    assert isinstance(decoded, wire.DecodedRecoveryWire)
    assert decoded.canonical_bytes == result.canonical_bytes()
    assert decoded.value == result


def test_work_rejection_roundtrips_exact_bytes() -> None:
    result = WorkPreparationRejected(code="FAULT", reason="waiting")
    decoded = wire.decode_post_terminal_preparation_result(
        wire.POST_TERMINAL_PREPARATION_RESULT_SCHEMA, result.canonical_bytes()
    )
    assert decoded.canonical_bytes == result.canonical_bytes()
    assert decoded.value == result


async def test_all_post_terminal_request_kinds_bind_typed_successes() -> None:
    run = (await non_scheduler_successor()).request.run
    view = _work_view()
    proof = _clock_proof()
    identity = work.WorkCommandIdentity(tenant_id="tenant", command_id="command")
    authority = recovery.RolloverAuthorityRef(
        proof_id="rollover",
        proof_fingerprint="d" * 64,
        authority_head="authority",
        command_id="command",
        command_payload_fingerprint="e" * 64,
        predecessor_rollover=recovery.Absent(),
    )
    requests: tuple[work.PostTerminalWorkRequest, ...] = (
        work.PrepareTerminalWork(
            identity=identity,
            terminal_run=run,
            original_terminalization_request=b"\xff\x00terminal",
            terminal_manifest=_head("terminal"),
            ordered_open_obligations=(),
        ),
        work.PrepareWorkLease(
            identity=identity,
            operation="CLAIM",
            expected=view,
            current_original_obligation=_present("obligation"),
            current_original_evidence=recovery.Absent(),
            proposed_holder_id="holder",
            proposed_holder_session_id="session",
            proposed_lease_id="lease",
            proposed_generation=1,
            proposed_expiry=100,
            clock_proof=proof,
            issued=IssuedLeaseObservation(
                proof=proof,
                now=10,
                max_lease_duration=100,
                holder_id="holder",
                holder_session_id="session",
                authority_epoch="authority",
                used_lease_ids=(),
            ),
            submission_id="submission",
        ),
        work.PrepareWorkRollover(
            identity=identity,
            expected=view,
            fence=recovery.WorkEpochRolloverFence(
                subject=view.subject,
                work_epoch=view.work_epoch,
                exhaustion=recovery.ExhaustionBinding(
                    hold_head="hold",
                    exhausted_command_id="takeover",
                    lease=_lease(2**64 - 1),
                    authority_epoch="authority",
                ),
                authority=authority,
            ),
            current_original_obligation=_present("obligation"),
            current_original_evidence=_present("evidence"),
            issued=IssuedRolloverObservation(
                authority=authority,
                snapshot_fingerprint="f" * 64,
                submission_id="submission",
                authority_epoch="authority",
                used_epoch_ids=("epoch",),
                used_lease_heads=("genesis",),
            ),
        ),
        work.PrepareWorkClose(
            identity=identity,
            expected=view,
            exact_obligation_terminal_head=_present("closed"),
            original_resolver_batch=_present("resolver-batch"),
        ),
    )
    for request_value in requests:
        request_raw = request_value.canonical_bytes()
        request = wire.decode_post_terminal_preparation_request(
            wire.POST_TERMINAL_PREPARATION_REQUEST_SCHEMA, request_raw
        )
        result_value = work.PreparedPostTerminalWork(
            source_request_fingerprint=hashlib.sha256(request_raw).hexdigest(),
            ordered_work=(),
            complete_records=(),
            complete_commitment="a" * 64,
        )
        result = wire.decode_post_terminal_preparation_result(
            wire.POST_TERMINAL_PREPARATION_RESULT_SCHEMA, result_value.canonical_bytes()
        )
        wire.validate_post_terminal_preparation_exchange(request, result)


async def test_all_model_recovery_request_kinds_bind_typed_successes() -> None:
    captured = await captured_fixture()
    replacement = model.ReplaceExecutionModelAttempt(
        command_id="replace",
        run=captured.captured_run,
        selected_attempt=_head("attempt"),
        expected_selector=0,
        no_exposure=model.RegisteredModelNoExposure(
            registry=_head("registry"),
            proof=_head("proof"),
            original_run=_head("run"),
            original_attempt=_head("attempt"),
            lineage_id="lineage",
            selector_generation=0,
            immutable_request=_head("request"),
            visibility_manifest=_head("manifest"),
            provider_contract="hermetic-model.v1",
            recipient="hermetic-model",
            observed_emission_head=_head("not-emitted"),
            source_schema="pre-emission-cas.v1",
            canonical_source_bytes=b"\xffsource",
        ),
        fence=captured.request.cut.fence,
    )
    late = model.RetainLateExecutionResponse(
        command_id="late",
        original_run=_head("run"),
        original_attempt=_head("old-attempt"),
        original_manifest=_head("manifest"),
        lineage_id="lineage",
        generation=0,
        receipt_token=_head("receipt"),
        selected_custody=_head("custody"),
        source_authentication=_head("source"),
        raw_response=b"\xff\x00late",
        transport_receipt=b"\x80receipt",
    )
    for request_value, result_value in (
        (
            replacement,
            lambda fingerprint: model.PreparedModelAttemptReplacement(
                source_request_fingerprint=fingerprint,
                original_attempt=_head("original"),
                superseded_attempt=_head("superseded"),
                replacement_attempt=_head("replacement"),
                run=captured.captured_run,
                proposal_fingerprint="b" * 64,
            ),
        ),
        (
            late,
            lambda fingerprint: model.PreparedLateExecutionResponse(
                source_request_fingerprint=fingerprint,
                record=model.LateExecutionResponseRecord(evidence_id="evidence", request=late),
                proposal_fingerprint="b" * 64,
            ),
        ),
    ):
        raw = request_value.canonical_bytes()
        request = wire.decode_model_recovery_request(wire.MODEL_RECOVERY_REQUEST_SCHEMA, raw)
        result = wire.decode_model_recovery_result(
            wire.MODEL_RECOVERY_RESULT_SCHEMA,
            result_value(hashlib.sha256(raw).hexdigest()).canonical_bytes(),
        )
        wire.validate_model_recovery_exchange(request, result)


def test_all_original_recovery_request_kinds_bind_typed_successes() -> None:
    binding = _original_binding()
    resolution = original.PrepareOriginalCallResolution(
        command_id="resolve",
        cut=original.OriginalResolverCut(
            tenant_id="tenant",
            database_id="database",
            tenant_commit_sequence=1,
            materialization_commitment="a" * 64,
            original=binding,
            obligation_stream=_stream(binding.obligation_stream_id),
            current_obligation=_source("obligation"),
            call_terminal=_source("terminal"),
            complete_ordered_evidence=(_source("witness"),),
            complete_selected_foreign_reductions=(),
            expected_closure=recovery.Absent(),
            expected_recovered_outcome=recovery.Absent(),
            resolver_policy=_source("resolver-policy"),
            reducer_policy=_source("reducer-policy"),
            authority=_authority(),
        ),
    )
    reduction = original.PrepareLoopSemanticReduction(
        command_id="reduce",
        tenant_id="tenant",
        database_id="database",
        tenant_commit_sequence=1,
        materialization_commitment="a" * 64,
        stream=_stream(binding.evidence_stream_id),
        expected=recovery.Absent(),
        original=binding,
        complete_ordered_evidence=(_source("witness"),),
        complete_anchor_sources=(),
        reducer_policy=_source("reducer-policy"),
        authority=_authority(),
    )
    raw = resolution.canonical_bytes()
    request = wire.decode_original_recovery_request(wire.ORIGINAL_RECOVERY_REQUEST_SCHEMA, raw)
    fingerprint = hashlib.sha256(raw).hexdigest()
    basis = original.OriginalResolutionBasis(
        basis_id="basis",
        source_request_fingerprint=fingerprint,
        original=binding,
        original_terminal=resolution.cut.call_terminal.subject,
        selected_witness=_head("witness"),
        complete_evidence_manifest=(_head("witness"),),
        resolver_policy=_head("resolver-policy"),
        reducer_policy=_head("reducer-policy"),
        reduction_id="reduction",
        accepted_semantic_class="confirmed",
    )
    basis_head = _head("basis")
    closure = original.OriginalObligationClosureRecord(
        closure_id="closure",
        basis=basis_head,
        original=binding,
        prior_obligation=_head("prior"),
        accepted_witness=_head("witness"),
        closure_predicate=_head("predicate"),
    )
    outcome = original.RecoveredCallOutcomeRecord(
        outcome_id="outcome",
        basis=basis_head,
        original=binding,
        closure=_head("closure"),
        accepted_witness=_head("witness"),
        result_schema="result.v1",
        canonical_result_bytes=b"\xffresult",
        accepted_semantic_class="confirmed",
    )
    result_value = original.PreparedOriginalCallResolution(
        source_request_fingerprint=fingerprint,
        basis=basis,
        closure=closure,
        recovered_outcome=outcome,
        batch=original.OriginalResolverBatchRecord(
            batch_id="batch",
            command_id=resolution.command_id,
            basis=basis_head,
            closure=_head("closure"),
            recovered_outcome=_head("outcome"),
            source_cut_fingerprint=resolution.cut.digest(),
        ),
        complete_batch_fingerprint="b" * 64,
    )
    result = wire.decode_original_recovery_result(
        wire.ORIGINAL_RECOVERY_RESULT_SCHEMA, result_value.canonical_bytes()
    )
    wire.validate_original_recovery_exchange(request, result)
    reduction_raw = reduction.canonical_bytes()
    reduction_request = wire.decode_original_recovery_request(
        wire.ORIGINAL_RECOVERY_REQUEST_SCHEMA, reduction_raw
    )
    record = original.LoopSemanticReductionRecord(
        stream=reduction.stream,
        reduction_id="reduction",
        reducer_id="reducer",
        reducer_version="1",
        predecessor=recovery.Absent(),
        complete_ordered_evidence=(),
        anchor=original.UnresolvedReductionAnchor(
            original=binding, closure=recovery.Absent(), recovered_outcome=recovery.Absent()
        ),
        disposition=original.HeldReduction(
            reason="INCOMPLETE", conflicting_or_unclassified_evidence=()
        ),
    )
    reduction_result = original.PreparedLoopSemanticReduction(
        source_request_fingerprint=hashlib.sha256(reduction_raw).hexdigest(),
        record=record,
        complete_batch_fingerprint="b" * 64,
    )
    decoded_reduction = wire.decode_original_recovery_result(
        wire.ORIGINAL_RECOVERY_RESULT_SCHEMA, reduction_result.canonical_bytes()
    )
    wire.validate_original_recovery_exchange(reduction_request, decoded_reduction)


async def test_remaining_execution_recovery_request_kinds_bind_typed_successes() -> None:
    fixture = await non_scheduler_successor()
    base = fixture.request
    accounting = execution.PrepareExecutionAccounting(command_id="accounting", cut=base.cut)
    continuation = execution.PrepareExecutionContinuation(
        command_id="continuation", cut=base.cut, complete_accounting=()
    )
    suspension = execution.PrepareExecutionSuspension(
        command_id="suspend",
        run=base.run,
        cut=base.cut,
        activation_blocking_predicates=(),
        fence=base.fence,
    )
    resume = PrepareExecutionResume(
        command_id="resume",
        run=base.run,
        original_suspension=base.original_suspension,
        cut=base.cut,
        disposition_version=base.disposition_version,
        activation_payload=b"\xffresume",
        fence=base.fence,
    )
    ready = observations.ContinuationReadyRecord(
        readiness_id="ready",
        source_cut_fingerprint=base.cut.digest(),
        accounting=observations.SealedAccountingRecord(
            accounting_id="accounting",
            source_cut_fingerprint=base.cut.digest(),
            sealed_response=_head("response"),
            sealed_manifest_fingerprint="a" * 64,
            registry=_head("registry"),
            frontier=_head("frontier"),
            complete_ordered_calls=(),
        ),
        complete_original_closures=(),
        complete_current_reductions=(),
    )
    next_turn = execution.PrepareNextExecutionTurn(
        command_id="next",
        run=base.run,
        cut=base.cut,
        immediately_preceding_response=_head("response"),
        expected_current_turn=_head("turn"),
        next_ordinal=1,
        continuation=ready,
        fence=base.fence,
    )
    abort = execution.PrepareAbortCancelExecution(
        command_id="abort",
        run=base.run,
        target="CANCELLED",
        authenticated_cause=base.cut.complete_sources[0],
        cut=base.cut,
        complete_accounting=(),
        fence=base.fence,
    )
    for request_value, make_result in (
        (
            accounting,
            lambda fingerprint: execution.PreparedExecutionAccounting(
                source_request_fingerprint=fingerprint, complete_accounting=()
            ),
        ),
        (
            continuation,
            lambda fingerprint: execution.PreparedExecutionContinuation(
                source_request_fingerprint=fingerprint, complete_continuations=()
            ),
        ),
        (
            suspension,
            lambda fingerprint: execution.PreparedExecutionSuspension(
                source_request_fingerprint=fingerprint,
                baseline=base.original_suspension.baseline,
                run=base.run,
                pair=base.original_suspension.pair,
                complete_batch_fingerprint="a" * 64,
            ),
        ),
        (
            resume,
            lambda fingerprint: execution.PreparedExecutionResume(
                source_request_fingerprint=fingerprint,
                original_pair=base.original_suspension.selected_pair,
                run=base.run,
                complete_batch_fingerprint="a" * 64,
            ),
        ),
        (
            next_turn,
            lambda fingerprint: execution.PreparedNextExecutionTurn(
                source_request_fingerprint=fingerprint,
                run=base.run,
                continuation=ready,
                complete_batch_fingerprint="a" * 64,
            ),
        ),
        (
            abort,
            lambda fingerprint: execution.PreparedExecutionTerminal(
                source_request_fingerprint=fingerprint,
                manifest=observations.ExecutionTerminalManifest(
                    manifest_id="manifest",
                    command_id="abort",
                    prior_run=base.cut.current_run,
                    target="CANCELLED",
                    source_cut_fingerprint=base.cut.digest(),
                    complete_accounting=(),
                    complete_open_original_obligations=(),
                ),
                run=base.run,
                work=work.PreparedPostTerminalWork(
                    source_request_fingerprint="a" * 64,
                    ordered_work=(),
                    complete_records=(),
                    complete_commitment="a" * 64,
                ),
                complete_batch_fingerprint="a" * 64,
            ),
        ),
    ):
        raw = request_value.canonical_bytes()
        request = wire.decode_execution_recovery_request(
            wire.EXECUTION_RECOVERY_REQUEST_SCHEMA, raw
        )
        result = wire.decode_execution_recovery_result(
            wire.EXECUTION_RECOVERY_RESULT_SCHEMA,
            make_result(hashlib.sha256(raw).hexdigest()).canonical_bytes(),
        )
        wire.validate_execution_recovery_exchange(request, result)


async def test_readonly_attempt_outcome_and_pending_bind_typed_successes() -> None:
    captured = await captured_fixture()
    lineage = ReadOnlyRetryLineage(
        lineage_id="lineage",
        original_call_id="call",
        max_attempts=2,
        budget_version="1",
        reducer_id="reducer",
        reducer_version="1",
    )
    call = captured.request.ordered_calls[0].model_dump()
    call.update(
        classification="READ_ONLY", retry_lineage=InitializedReadOnlyLineage(lineage=lineage)
    )
    initialized = InitializedCallRecord(
        original_call_id="call",
        call=SealedCallInput.model_validate(call),
        predecessor=recovery.Absent(),
    )
    proof = readonly.RegisteredReadOnlyProof(
        registry=_head("registry"),
        tool_schema=_head("tool"),
        tool_policy=_head("policy"),
        implementation=_head("implementation"),
        no_mutation_proof=_head("proof"),
        proof_schema="proof.v1",
        canonical_proof_bytes=b"\xffproof",
        query_identity=_head("query"),
        canonical_query_bytes=b"\x80query",
        snapshot=_head("snapshot"),
        snapshot_frontier=1,
        snapshot_contract=_head("contract"),
    )
    observed = readonly.ReadOnlyLineageSnapshot(
        initialized_head=_head("initialized"),
        initialized=initialized,
        lineage=lineage,
        complete_ordered_attempts=(),
        shared_counter=_head("counter"),
        attempts_consumed=0,
        call_outcome=recovery.Absent(),
        terminal=recovery.Absent(),
        pending=recovery.Absent(),
        complete_manifest_fingerprint="a" * 64,
    )
    cut = readonly.ReadOnlyCurrentCut(
        tenant_id="tenant",
        database_id="database",
        tenant_commit_sequence=1,
        materialization_commitment="a" * 64,
        authority_registry=_head("registry"),
        sources=captured.request.cut.sources,
        applicability=captured.request.cut.fence,
    )
    attempt = readonly.PrepareReadOnlyAttempt(
        command_id="attempt",
        run=captured.captured_run,
        observed=observed,
        proof=proof,
        route=readonly.SameRunReadOnlyAttempt(pending=recovery.Absent()),
        cut=cut,
        fence=captured.request.cut.fence,
    )
    outcome = readonly.PrepareReadOnlyOutcome(
        command_id="outcome",
        observed=observed,
        accepted_attempt=_head("accepted"),
        source_evidence=_head("source"),
        evidence_schema="source.v1",
        canonical_evidence_bytes=b"\xffoutcome",
        cut=cut,
    )
    pending = readonly.PrepareReadOnlyPending(
        command_id="pending",
        run=captured.captured_run,
        observed=observed,
        current_proof=proof,
        crossed_binding_heads=(_head("changed"),),
        closure_registry=_head("closure"),
        cut=cut,
        fence=captured.request.cut.fence,
    )
    accepted = readonly.ReadOnlyAttemptAcceptedRecord(
        original_call_id="call",
        lineage_id="lineage",
        attempt_id="attempt",
        ordinal=0,
        initialized=_head("initialized"),
        predecessor_attempt=recovery.Absent(),
        active_run=_head("run"),
        proof=proof,
        previous_counter=_head("counter"),
        next_counter=_head("next-counter"),
        fence=captured.request.cut.fence,
    )
    attempt_result = readonly.PreparedReadOnlyAttempt(
        source_request_fingerprint="a" * 64,
        acceptance=accepted,
        counter=readonly.ReadOnlyCounterRecord(
            original_call_id="call",
            lineage_id="lineage",
            predecessor=_head("counter"),
            attempts_consumed=1,
            closed=False,
        ),
        pending_transition=None,
        complete_batch_fingerprint="a" * 64,
    )
    outcome_result = readonly.PreparedReadOnlyOutcome(
        source_request_fingerprint="a" * 64,
        outcome=readonly.ReadOnlyAttemptOutcomeRecord(
            original_call_id="call",
            lineage_id="lineage",
            attempt_id="attempt",
            ordinal=0,
            accepted=_head("accepted"),
            source_evidence=_head("source"),
            disposition=readonly.ReadOnlySucceeded(
                result=_head("result"),
                result_schema="result.v1",
                canonical_result_bytes=b"\xffresult",
            ),
        ),
        complete_batch_fingerprint="a" * 64,
    )
    pending_result = readonly.PreparedReadOnlyPending(
        source_request_fingerprint="a" * 64,
        pending=readonly_pending(),
        complete_batch_fingerprint="a" * 64,
    )
    reduction = readonly.PrepareReadOnlyReduction(
        command_id="reduction",
        observed=observed,
        reducer=_head("reducer"),
        reducer_schema="reducer.v1",
        canonical_reducer_bytes=b"\xffreducer",
        cut=cut,
    )
    terminal = TerminalCallFrontier(
        original_call_id="call",
        response_id="response",
        acceptance=ReadOnlyAcceptedCall(
            initialized=_present("initialized"),
            accepted=_present("accepted"),
            lineage_id="lineage",
            ordinal=0,
            readonly_proof=_present("proof"),
            snapshot_binding=_present("snapshot"),
            lineage=lineage,
            complete_ordered_attempts=(
                ReadOnlyAttemptMember(
                    ordinal=0,
                    attempt_id="attempt",
                    initialized=_present("initialized"),
                    accepted=_present("accepted"),
                    outcome=_present("outcome"),
                    result_or_obligation=_present("result"),
                    predecessor=recovery.Absent(),
                ),
            ),
            shared_counter=_present("counter"),
            attempts_consumed=1,
            call_level_outcome=_present("call-outcome"),
            complete_lineage_reducer_batch=_present("batch"),
        ),
        disposition=SuccessResult(
            terminal=_present("terminal"),
            result=_present("result"),
            recovered_outcome=recovery.NotApplicable(),
        ),
    )
    reduction_result = readonly.PreparedReadOnlyReduction(
        source_request_fingerprint="a" * 64,
        call_outcome=readonly.ReadOnlyCallOutcomeRecord(
            original_call_id="call",
            lineage=lineage,
            complete_ordered_outcomes=(_head("outcome"),),
            complete_manifest_fingerprint="a" * 64,
            selected_attempt_outcome=_head("outcome"),
            disposition="SUCCEEDED",
            provenance="ATTEMPT_RESULT",
            result_or_obligation=_head("result"),
            terminal_disposition=_head("terminal"),
            closed_counter=_head("counter"),
        ),
        terminal=terminal,
        counter=readonly.ReadOnlyCounterRecord(
            original_call_id="call",
            lineage_id="lineage",
            predecessor=_head("counter"),
            attempts_consumed=1,
            closed=True,
        ),
        pending_transition=None,
        complete_batch_fingerprint="a" * 64,
    )
    for request_value, result_value in (
        (attempt, attempt_result),
        (outcome, outcome_result),
        (pending, pending_result),
        (reduction, reduction_result),
    ):
        raw = request_value.canonical_bytes()
        request = wire.decode_readonly_preparation_request(
            wire.READONLY_PREPARATION_REQUEST_SCHEMA, raw
        )
        bound = result_value.model_copy(
            update={"source_request_fingerprint": hashlib.sha256(raw).hexdigest()}
        )
        result = wire.decode_readonly_preparation_result(
            wire.READONLY_PREPARATION_RESULT_SCHEMA, bound.canonical_bytes()
        )
        wire.validate_readonly_preparation_exchange(request, result)


@pytest.mark.parametrize("row", wire.RECOVERY_WIRE_DECODER_ROWS)
def test_each_family_rejects_wrong_schema_noncanonical_and_unknown_body(
    row: wire.RecoveryWireDecoderRow,
) -> None:
    with pytest.raises(wire.RecoveryWireIntegrityError):
        row.request_decoder(row.result_schema, b"{}")
    with pytest.raises(wire.RecoveryWireIntegrityError):
        row.result_decoder(row.result_schema, b'{"kind":"UNKNOWN"}')


def test_result_decoder_rejects_noncanonical_key_order_and_extra_field() -> None:
    result = CallPreparationRejected(command_id="command", code="HOLD", reason="waiting")
    payload = result.model_dump(mode="json")
    reordered = json.dumps(payload, separators=(",", ":")).encode()
    assert reordered != result.canonical_bytes()
    with pytest.raises(wire.RecoveryWireIntegrityError, match="noncanonical"):
        wire.decode_execution_recovery_result(wire.EXECUTION_RECOVERY_RESULT_SCHEMA, reordered)
    payload["unexpected"] = "field"
    with pytest.raises(wire.RecoveryWireIntegrityError):
        wire.decode_execution_recovery_result(
            wire.EXECUTION_RECOVERY_RESULT_SCHEMA,
            json.dumps(payload, separators=(",", ":")).encode(),
        )


async def test_successor_request_and_success_result_bind_exact_wire_bytes() -> None:
    fixture = await non_scheduler_successor()
    request_raw = fixture.request.canonical_bytes()
    request = wire.decode_execution_recovery_request(
        wire.EXECUTION_RECOVERY_REQUEST_SCHEMA, request_raw
    )
    result_value = fixture.result.model_copy(
        update={"source_request_fingerprint": hashlib.sha256(request_raw).hexdigest()}
    )
    result = wire.decode_execution_recovery_result(
        wire.EXECUTION_RECOVERY_RESULT_SCHEMA, result_value.canonical_bytes()
    )
    wire.validate_execution_recovery_exchange(request, result)
    assert result.value == result_value


@pytest.mark.parametrize(
    "disposition", ["SAME_RUN", "SUCCESSOR_REQUIRED", "RECOVERY_HOLD", "TERMINAL_RECOVERY_FAULT"]
)
async def test_successor_all_classification_dispositions_bind_exact_wire_bytes(
    disposition: Literal[
        "SAME_RUN", "SUCCESSOR_REQUIRED", "RECOVERY_HOLD", "TERMINAL_RECOVERY_FAULT"
    ],
) -> None:
    fixture = await non_scheduler_successor()
    request_raw = fixture.request.canonical_bytes()
    request = wire.decode_execution_recovery_request(
        wire.EXECUTION_RECOVERY_REQUEST_SCHEMA, request_raw
    )
    classified = ExecutionRecoveryClassified(
        source_request_fingerprint=hashlib.sha256(request_raw).hexdigest(),
        disposition=disposition,
        reason="proof-observation",
    )
    result = wire.decode_execution_recovery_result(
        wire.EXECUTION_RECOVERY_RESULT_SCHEMA, classified.canonical_bytes()
    )
    wire.validate_execution_recovery_exchange(request, result)


@pytest.mark.parametrize(
    "disposition", ["SAME_RUN", "SUCCESSOR_REQUIRED", "RECOVERY_HOLD", "TERMINAL_RECOVERY_FAULT"]
)
async def test_resume_all_classification_dispositions_bind_exact_wire_bytes(
    disposition: Literal[
        "SAME_RUN", "SUCCESSOR_REQUIRED", "RECOVERY_HOLD", "TERMINAL_RECOVERY_FAULT"
    ],
) -> None:
    fixture = await non_scheduler_successor()
    successor_request = fixture.request
    resume = PrepareExecutionResume(
        command_id="resume",
        run=successor_request.run,
        original_suspension=successor_request.original_suspension,
        cut=successor_request.cut,
        disposition_version=successor_request.disposition_version,
        activation_payload=b"\xffresume-payload",
        fence=successor_request.fence,
    )
    request_raw = resume.canonical_bytes()
    request = wire.decode_execution_recovery_request(
        wire.EXECUTION_RECOVERY_REQUEST_SCHEMA, request_raw
    )
    classified = ExecutionRecoveryClassified(
        source_request_fingerprint=hashlib.sha256(request_raw).hexdigest(),
        disposition=disposition,
        reason="proof-observation",
    )
    result = wire.decode_execution_recovery_result(
        wire.EXECUTION_RECOVERY_RESULT_SCHEMA, classified.canonical_bytes()
    )
    wire.validate_execution_recovery_exchange(request, result)


async def test_execution_exchange_rejects_wrong_pair_fingerprint_reject_and_forged_carrier() -> (
    None
):
    fixture = await non_scheduler_successor()
    raw = fixture.request.canonical_bytes()
    request = wire.decode_execution_recovery_request(wire.EXECUTION_RECOVERY_REQUEST_SCHEMA, raw)
    fingerprint = hashlib.sha256(raw).hexdigest()
    wrong_kind = execution.PreparedExecutionResume(
        source_request_fingerprint=fingerprint,
        original_pair=fixture.request.original_suspension.selected_pair,
        run=fixture.request.run,
        complete_batch_fingerprint="a" * 64,
    )
    wrong_kind_wire = wire.decode_execution_recovery_result(
        wire.EXECUTION_RECOVERY_RESULT_SCHEMA, wrong_kind.canonical_bytes()
    )
    with pytest.raises(wire.RecoveryWireIntegrityError, match="incompatible"):
        wire.validate_execution_recovery_exchange(request, wrong_kind_wire)
    bad_fingerprint = fixture.result.model_copy(update={"source_request_fingerprint": "0" * 64})
    bad_fingerprint_wire = wire.decode_execution_recovery_result(
        wire.EXECUTION_RECOVERY_RESULT_SCHEMA, bad_fingerprint.canonical_bytes()
    )
    with pytest.raises(wire.RecoveryWireIntegrityError, match="fingerprint"):
        wire.validate_execution_recovery_exchange(request, bad_fingerprint_wire)
    rejected = wire.decode_execution_recovery_result(
        wire.EXECUTION_RECOVERY_RESULT_SCHEMA,
        CallPreparationRejected(
            command_id="other", code="HOLD", reason="waiting"
        ).canonical_bytes(),
    )
    with pytest.raises(wire.RecoveryWireIntegrityError, match="command_id"):
        wire.validate_execution_recovery_exchange(request, rejected)
    forged = wire.DecodedRecoveryWire(
        schema_id=wrong_kind_wire.schema_id,
        canonical_bytes=wrong_kind_wire.canonical_bytes,
        value=fixture.result,
    )
    with pytest.raises(wire.RecoveryWireIntegrityError, match="carrier"):
        wire.validate_execution_recovery_exchange(request, forged)


async def test_execution_classification_rejects_accounting_request() -> None:
    base = await non_scheduler_successor()
    accounting = execution.PrepareExecutionAccounting(command_id="account", cut=base.request.cut)
    raw = accounting.canonical_bytes()
    request = wire.decode_execution_recovery_request(wire.EXECUTION_RECOVERY_REQUEST_SCHEMA, raw)
    classified = ExecutionRecoveryClassified(
        source_request_fingerprint=hashlib.sha256(raw).hexdigest(),
        disposition="RECOVERY_HOLD",
        reason="observation",
    )
    result = wire.decode_execution_recovery_result(
        wire.EXECUTION_RECOVERY_RESULT_SCHEMA, classified.canonical_bytes()
    )
    with pytest.raises(wire.RecoveryWireIntegrityError, match="classification"):
        wire.validate_execution_recovery_exchange(request, result)
