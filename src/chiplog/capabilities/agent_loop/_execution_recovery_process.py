"""Versioned owner process route for bounded recovery accounting/readiness."""

from __future__ import annotations

import base64

from . import _native_one_call_accounting_v2_process
from .call_acceptance_contracts import CallPreparationRejected
from .execution_recovery_contracts import (
    ExecutionRecoveryResult,
    PreparedExecutionSuspension,
    PrepareExecutionAccounting,
    PrepareExecutionContinuation,
    PrepareExecutionResume,
    PrepareExecutionSuccessor,
    PrepareExecutionSuccessorStart,
    PrepareExecutionSuspension,
)
from .execution_recovery_preparation import (
    CURRENT_ACTOR_RESUME_DISPOSITION,
    classify_execution_resume_rejection,
    prepare_execution_accounting,
    prepare_execution_continuation,
    prepare_execution_resume,
    prepare_execution_successor,
    prepare_execution_successor_start,
    prepare_execution_suspension,
)
from .recovery_wire_contracts import (
    EXECUTION_RECOVERY_REQUEST_SCHEMA,
    EXECUTION_RECOVERY_RESULT_SCHEMA,
    decode_execution_recovery_request,
)

ACCOUNTING_OPERATION = "agent_loop.prepare_execution_accounting.v1"
CONTINUATION_OPERATION = "agent_loop.prepare_execution_continuation.v1"
SUSPENSION_OPERATION = "agent_loop.prepare_execution_suspension.v1"
RESUME_OPERATION = "agent_loop.prepare_execution_resume.v1"
SUSPENSION_OBLIGATION_OPERATION = "agent_loop.prepare_execution_suspension_with_obligation.v1"
RESUME_OBLIGATION_OPERATION = "agent_loop.prepare_execution_resume_with_obligation.v1"
RESUME_CURRENT_ACTOR_OPERATION = "agent_loop.prepare_execution_resume_current_actor.v2"
SUCCESSOR_OPERATION = "agent_loop.prepare_execution_successor.v1"
REQUEST_SCHEMA = EXECUTION_RECOVERY_REQUEST_SCHEMA
RESULT_SCHEMA = EXECUTION_RECOVERY_RESULT_SCHEMA
ROUTES = tuple(
    sorted(
        (
            *_native_one_call_accounting_v2_process.ROUTES,
            (ACCOUNTING_OPERATION, "broker", "agent_loop", REQUEST_SCHEMA, RESULT_SCHEMA),
            (CONTINUATION_OPERATION, "broker", "agent_loop", REQUEST_SCHEMA, RESULT_SCHEMA),
            (RESUME_OPERATION, "broker", "agent_loop", REQUEST_SCHEMA, RESULT_SCHEMA),
            (RESUME_CURRENT_ACTOR_OPERATION, "broker", "agent_loop", REQUEST_SCHEMA, RESULT_SCHEMA),
            (SUSPENSION_OPERATION, "broker", "agent_loop", REQUEST_SCHEMA, RESULT_SCHEMA),
            (SUCCESSOR_OPERATION, "broker", "agent_loop", REQUEST_SCHEMA, RESULT_SCHEMA),
            (
                RESUME_OBLIGATION_OPERATION,
                "broker",
                "agent_loop",
                REQUEST_SCHEMA,
                RESULT_SCHEMA,
            ),
            (
                SUSPENSION_OBLIGATION_OPERATION,
                "broker",
                "agent_loop",
                REQUEST_SCHEMA,
                RESULT_SCHEMA,
            ),
        )
    )
)


def dispatch(operation: str, payload: bytes) -> dict[str, object]:
    if operation not in {
        ACCOUNTING_OPERATION,
        CONTINUATION_OPERATION,
        RESUME_OPERATION,
        SUSPENSION_OPERATION,
        RESUME_OBLIGATION_OPERATION,
        RESUME_CURRENT_ACTOR_OPERATION,
        SUSPENSION_OBLIGATION_OPERATION,
        SUCCESSOR_OPERATION,
    }:
        return _native_one_call_accounting_v2_process.dispatch(operation, payload)
    try:
        decoded = decode_execution_recovery_request(REQUEST_SCHEMA, payload)
        request = decoded.value
        result: ExecutionRecoveryResult
        if operation == ACCOUNTING_OPERATION:
            if not isinstance(request, PrepareExecutionAccounting):
                raise ValueError("accounting operation requires accounting request")
            result = prepare_execution_accounting(request)
        elif operation == CONTINUATION_OPERATION:
            if not isinstance(request, PrepareExecutionContinuation):
                raise ValueError("continuation operation requires continuation request")
            result = prepare_execution_continuation(request)
        elif operation in (
            RESUME_OPERATION,
            RESUME_OBLIGATION_OPERATION,
            RESUME_CURRENT_ACTOR_OPERATION,
        ):
            if not isinstance(request, PrepareExecutionResume):
                raise ValueError("resume operation requires resume request")
            expected_version = (
                CURRENT_ACTOR_RESUME_DISPOSITION
                if operation == RESUME_CURRENT_ACTOR_OPERATION
                else "r14-same-run-equality.v1"
            )
            if request.disposition_version != expected_version:
                raise ValueError("resume operation and disposition version differ")
            if (
                operation == RESUME_OPERATION
                and request.original_suspension.baseline.original_obligations
            ):
                raise ValueError("legacy resume route cannot authorize original obligations")
            result = prepare_execution_resume(request)
            if isinstance(result, CallPreparationRejected):
                result = classify_execution_resume_rejection(request, result)
        elif operation == SUCCESSOR_OPERATION:
            if isinstance(request, PrepareExecutionSuccessor):
                result = prepare_execution_successor(request)
            elif isinstance(request, PrepareExecutionSuccessorStart):
                result = prepare_execution_successor_start(request)
            else:
                raise ValueError("successor operation requires a successor request")
        else:
            if not isinstance(request, PrepareExecutionSuspension):
                raise ValueError("suspension operation requires suspension request")
            result = prepare_execution_suspension(request)
            if (
                operation == SUSPENSION_OPERATION
                and isinstance(result, PreparedExecutionSuspension)
                and result.baseline.original_obligations
            ):
                raise ValueError("legacy suspension route cannot authorize original obligations")
        return {
            "payload": base64.b64encode(result.canonical_bytes()).decode(),
            "schema_id": RESULT_SCHEMA,
        }
    except (ValueError, TypeError) as error:
        return {"failure": "PROTOCOL_REJECTED", "reason": str(error)}
