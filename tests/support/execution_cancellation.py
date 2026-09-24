"""Inert retention fixture: transport shape only, no authenticated issuance."""

from chiplog.capabilities.agent_loop import call_acceptance_contracts as call
from chiplog.capabilities.agent_loop.recovery_contracts import Absent
from chiplog.composition.r14_cancellation_contracts import (
    CancelCallSubmission,
    HermeticCancellationPolicy,
    RetainedCancellationAct,
    RetainedCancellationTrust,
)
from chiplog.composition.r14_execution_cancellation_contracts import (
    RetainedExecutionCancellationPreparation,
)
from chiplog.platform.broker import BrokerSession, CallBudget, PublicPortCall, PublicPortSuccess
from tests.support.execution_fan_out import fixture as execution_fixture
from tests.support.fan_out_shapes import head


async def retained_shape() -> RetainedExecutionCancellationPreparation:
    captured = await execution_fixture()
    sealed = captured.request.ordered_calls[-1]
    initialized = call.InitializedCallRecord(
        original_call_id="call:shape", call=sealed, predecessor=Absent()
    )
    submission = CancelCallSubmission(
        act_id="act:shape",
        original_call_id=initialized.original_call_id,
        initialized=head(initialized.original_call_id),
        current_run=captured.request.cut.current_run,
    )
    broker = BrokerSession(
        tenant_id="tenant",
        broker_epoch=1,
        generation_id="generation",
        owner_id="broker",
        session_id="broker-session",
    )
    owner = broker.model_copy(update={"owner_id": "agent_loop", "session_id": "owner-session"})
    request = call.CancelBeforeAcceptRequest(
        command_id="cancel:shape",
        original_call_id=initialized.original_call_id,
        original=sealed.original,
        initialized=submission.initialized,
        initialized_record=initialized,
        cancellation_act=head("act:shape"),
        cut=captured.request.cut,
    )
    terminal = call.CancelledBeforeAcceptRecord(
        terminal_id="terminal:shape",
        source_command_id=request.command_id,
        original_call_id=request.original_call_id,
        original=request.original,
        initialized=request.initialized,
        cancellation_act=request.cancellation_act,
        cut=request.cut,
        not_executed_result_id="result:shape",
    )
    result = call.NotExecutedCallResultRecord(
        result_id=terminal.not_executed_result_id,
        original_call_id=request.original_call_id,
        initialized=request.initialized,
        terminal=head(terminal.terminal_id),
        outcome="NOT_EXECUTED",
    )
    proposal = call.PreparedPreAcceptCancellation(
        source_request_fingerprint=request.digest(),
        terminal=terminal,
        result=result,
        complete_ordered_record_manifest=(head(terminal.terminal_id), head(result.result_id)),
        proposal_fingerprint="a" * 64,
    )
    sent = PublicPortCall(
        operation_id="agent_loop.prepare_pre_accept_cancellation",
        request_id="shape-request",
        caller=broker,
        callee=owner,
        schema_id="chiplog.call.cancellation-preparation.v1",
        canonical_payload=request.canonical_bytes(),
        budget=CallBudget(
            remaining_calls=1, remaining_depth=1, absolute_deadline_ns=10, policy_version=1
        ),
    )
    returned = PublicPortSuccess(
        request_id=sent.request_id,
        responder=owner,
        schema_id="shape-result",
        canonical_payload=proposal.canonical_bytes(),
    )
    trust = RetainedCancellationTrust(
        snapshot_bytes=b"\xff\x00snapshot",
        journal_head="journal:shape",
        bundle_path="shape/path",
        sources=(("source", 1, 2),),
        request=sent,
        response=returned,
        authenticated_reference_bytes=b"\xfe\x00reference",
    )
    act = RetainedCancellationAct(
        submission=submission,
        policy=HermeticCancellationPolicy(),
        authenticated_reference_bytes=trust.authenticated_reference_bytes,
        trust_evidence_fingerprint=trust.digest(),
    )
    return RetainedExecutionCancellationPreparation(
        act=act,
        trust=trust,
        request=request,
        proposal=proposal,
        run_predecessor=captured.captured_run,
        expected_snapshot_fingerprint="b" * 64,
        owner_request=sent,
        owner_response=returned,
    )
