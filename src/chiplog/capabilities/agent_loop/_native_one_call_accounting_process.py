"""Registered owner route for bounded native one-call accounting."""

from __future__ import annotations

import base64

from . import _execution_completion_process
from .native_one_call_accounting_contracts import (
    NATIVE_ONE_CALL_ACCOUNTING_REQUEST_SCHEMA,
    NATIVE_ONE_CALL_ACCOUNTING_RESULT_SCHEMA,
    PrepareNativeOneCallCancelledAccounting,
)
from .native_one_call_accounting_preparation import prepare_native_one_call_cancelled_accounting

OPERATION = "agent_loop.prepare_native_one_call_cancelled_accounting"
REQUEST_SCHEMA = NATIVE_ONE_CALL_ACCOUNTING_REQUEST_SCHEMA
RESULT_SCHEMA = NATIVE_ONE_CALL_ACCOUNTING_RESULT_SCHEMA
ROUTES = tuple(
    sorted(
        (
            *_execution_completion_process.ROUTES,
            (OPERATION, "broker", "agent_loop", REQUEST_SCHEMA, RESULT_SCHEMA),
        )
    )
)


def dispatch(operation: str, payload: bytes) -> dict[str, object]:
    if operation != OPERATION:
        return _execution_completion_process.dispatch(operation, payload)
    try:
        request = PrepareNativeOneCallCancelledAccounting.model_validate_json(payload)
        if request.canonical_bytes() != payload:
            raise ValueError("noncanonical native one-call accounting request")
        result = prepare_native_one_call_cancelled_accounting(request)
        return {
            "payload": base64.b64encode(result.canonical_bytes()).decode(),
            "schema_id": RESULT_SCHEMA,
        }
    except (ValueError, TypeError) as error:
        return {"failure": "PROTOCOL_REJECTED", "reason": str(error)}
