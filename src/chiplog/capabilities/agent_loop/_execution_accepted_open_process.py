"""Additive V28 owner route for accepted/open accounting preparation."""

from __future__ import annotations

import base64

from . import _execution_next_turn_process
from .execution_recovery_contracts import PrepareAcceptedOpenAccounting
from .execution_recovery_preparation import prepare_accepted_open_accounting
from .recovery_wire_contracts import (
    EXECUTION_RECOVERY_REQUEST_SCHEMA,
    EXECUTION_RECOVERY_RESULT_SCHEMA,
    decode_execution_recovery_request,
)

ACCEPTED_OPEN_ACCOUNTING_OPERATION = "agent_loop.prepare_accepted_open_accounting.v1"
REQUEST_SCHEMA = EXECUTION_RECOVERY_REQUEST_SCHEMA
RESULT_SCHEMA = EXECUTION_RECOVERY_RESULT_SCHEMA
ROUTES = tuple(
    sorted(
        (
            *_execution_next_turn_process.ROUTES,
            (
                ACCEPTED_OPEN_ACCOUNTING_OPERATION,
                "broker",
                "agent_loop",
                REQUEST_SCHEMA,
                RESULT_SCHEMA,
            ),
        )
    )
)


def dispatch(operation: str, payload: bytes) -> dict[str, object]:
    if operation != ACCEPTED_OPEN_ACCOUNTING_OPERATION:
        return _execution_next_turn_process.dispatch(operation, payload)
    try:
        decoded = decode_execution_recovery_request(REQUEST_SCHEMA, payload)
        request = decoded.value
        if not isinstance(request, PrepareAcceptedOpenAccounting):
            raise ValueError("accepted/open accounting operation requires accepted/open request")
        result = prepare_accepted_open_accounting(request)
        return {
            "payload": base64.b64encode(result.canonical_bytes()).decode(),
            "schema_id": RESULT_SCHEMA,
        }
    except (ValueError, TypeError, IndexError, KeyError) as error:
        return {"failure": "PROTOCOL_REJECTED", "reason": str(error)}
