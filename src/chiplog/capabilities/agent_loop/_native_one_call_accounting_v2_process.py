"""Registered V24 owner route; V23 remains in its original process."""

from __future__ import annotations

import base64

from . import _native_one_call_accounting_process
from .native_one_call_accounting_contracts import (
    NATIVE_ONE_CALL_ACCOUNTING_V2_REQUEST_SCHEMA,
    NATIVE_ONE_CALL_ACCOUNTING_V2_RESULT_SCHEMA,
    PrepareNativeOneCallCancelledAccountingV2,
)
from .native_one_call_accounting_preparation import prepare_native_one_call_cancelled_accounting_v2

OPERATION = "agent_loop.prepare_native_one_call_cancelled_accounting.v2"
REQUEST_SCHEMA = NATIVE_ONE_CALL_ACCOUNTING_V2_REQUEST_SCHEMA
RESULT_SCHEMA = NATIVE_ONE_CALL_ACCOUNTING_V2_RESULT_SCHEMA
ROUTES = tuple(
    sorted(
        (
            *_native_one_call_accounting_process.ROUTES,
            (OPERATION, "broker", "agent_loop", REQUEST_SCHEMA, RESULT_SCHEMA),
        )
    )
)


def dispatch(operation: str, payload: bytes) -> dict[str, object]:
    if operation != OPERATION:
        return _native_one_call_accounting_process.dispatch(operation, payload)
    try:
        request = PrepareNativeOneCallCancelledAccountingV2.model_validate_json(payload)
        if request.canonical_bytes() != payload:
            raise ValueError("noncanonical V24 native one-call accounting request")
        result = prepare_native_one_call_cancelled_accounting_v2(request)
        return {
            "payload": base64.b64encode(result.canonical_bytes()).decode(),
            "schema_id": RESULT_SCHEMA,
        }
    except (ValueError, TypeError) as error:
        return {"failure": "PROTOCOL_REJECTED", "reason": str(error)}
