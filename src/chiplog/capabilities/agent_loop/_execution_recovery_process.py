"""Versioned owner process route for bounded recovery accounting/readiness."""

from __future__ import annotations

import base64

from . import _native_one_call_accounting_v2_process
from .execution_recovery_contracts import (
    PrepareExecutionAccounting,
    PrepareExecutionContinuation,
)
from .execution_recovery_preparation import (
    prepare_execution_accounting,
    prepare_execution_continuation,
)
from .recovery_wire_contracts import (
    EXECUTION_RECOVERY_REQUEST_SCHEMA,
    EXECUTION_RECOVERY_RESULT_SCHEMA,
    decode_execution_recovery_request,
)

ACCOUNTING_OPERATION = "agent_loop.prepare_execution_accounting.v1"
CONTINUATION_OPERATION = "agent_loop.prepare_execution_continuation.v1"
REQUEST_SCHEMA = EXECUTION_RECOVERY_REQUEST_SCHEMA
RESULT_SCHEMA = EXECUTION_RECOVERY_RESULT_SCHEMA
ROUTES = tuple(
    sorted(
        (
            *_native_one_call_accounting_v2_process.ROUTES,
            (ACCOUNTING_OPERATION, "broker", "agent_loop", REQUEST_SCHEMA, RESULT_SCHEMA),
            (CONTINUATION_OPERATION, "broker", "agent_loop", REQUEST_SCHEMA, RESULT_SCHEMA),
        )
    )
)


def dispatch(operation: str, payload: bytes) -> dict[str, object]:
    if operation not in {ACCOUNTING_OPERATION, CONTINUATION_OPERATION}:
        return _native_one_call_accounting_v2_process.dispatch(operation, payload)
    try:
        decoded = decode_execution_recovery_request(REQUEST_SCHEMA, payload)
        request = decoded.value
        if operation == ACCOUNTING_OPERATION:
            if not isinstance(request, PrepareExecutionAccounting):
                raise ValueError("accounting operation requires accounting request")
            result = prepare_execution_accounting(request)
        else:
            if not isinstance(request, PrepareExecutionContinuation):
                raise ValueError("continuation operation requires continuation request")
            result = prepare_execution_continuation(request)
        return {
            "payload": base64.b64encode(result.canonical_bytes()).decode(),
            "schema_id": RESULT_SCHEMA,
        }
    except (ValueError, TypeError) as error:
        return {"failure": "PROTOCOL_REJECTED", "reason": str(error)}
