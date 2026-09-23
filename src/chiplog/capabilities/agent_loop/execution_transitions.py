"""Closed execution command interpreter; all returned Run bytes remain inert."""

from pydantic import TypeAdapter

from .call_acceptance_preparation import _failure, _proposal_digest
from .execution_attempts import capture_execution_response, emit_execution_attempt
from .execution_lifecycle import (
    accumulate_execution_visibility,
    activate_execution_run,
    create_ingress_execution_run,
    start_initial_execution_turn,
)
from .execution_preparation import prepare_execution_request
from .execution_transition_contracts import (
    AccumulateExecutionVisibility,
    ActivateExecutionRun,
    CaptureExecutionResponse,
    CreateExecutionRun,
    EmitExecutionAttempt,
    ExecutionTransitionProposal,
    ExecutionTransitionRequest,
    ExecutionTransitionResult,
    PrepareExecutionRequest,
    StartInitialExecutionTurn,
)


def prepare_execution_transition(request: ExecutionTransitionRequest) -> ExecutionTransitionResult:
    try:
        request = TypeAdapter(ExecutionTransitionRequest).validate_json(request.canonical_bytes())
        if isinstance(request, CreateExecutionRun):
            run = create_ingress_execution_run(
                tenant=request.tenant,
                principal=request.principal,
                run_id=request.run_id,
                prompt=request.prompt,
                policy=request.policy,
                origin=request.origin,
                contour_head=request.contour_head,
                policy_head=request.policy_head,
                worker_session=request.worker_session,
            )
        elif isinstance(request, ActivateExecutionRun):
            run = activate_execution_run(request.run)
        elif isinstance(request, StartInitialExecutionTurn):
            run = start_initial_execution_turn(request.run)
        elif isinstance(request, AccumulateExecutionVisibility):
            run = accumulate_execution_visibility(request.run, request.members)
        elif isinstance(request, PrepareExecutionRequest):
            run = prepare_execution_request(request.run, request.manifest)
        elif isinstance(request, EmitExecutionAttempt):
            run = emit_execution_attempt(request.run)
        else:
            assert isinstance(request, CaptureExecutionResponse)
            run = capture_execution_response(request.run, request.raw, request.receipt)
        result = ExecutionTransitionProposal(
            source_request_fingerprint=request.digest(), run=run, proposal_fingerprint="0" * 64
        )
        return result.model_copy(update={"proposal_fingerprint": _proposal_digest(result)})
    except (ValueError, TypeError, IndexError, KeyError) as error:
        return _failure(getattr(request, "command_id", None), error)
