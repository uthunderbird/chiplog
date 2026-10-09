"""Owner routes for accepted/open accounting and original recovery opening."""

from __future__ import annotations

import base64

from . import _execution_next_turn_process
from .execution_recovery_contracts import (
    PrepareAcceptedOpenAccounting,
    PrepareAcceptedOpenRecoveryV1,
)
from .execution_recovery_preparation import (
    prepare_accepted_open_accounting,
    prepare_accepted_open_recovery_v1,
)
from .recovery_wire_contracts import (
    EXECUTION_RECOVERY_REQUEST_SCHEMA,
    EXECUTION_RECOVERY_RESULT_SCHEMA,
    decode_execution_recovery_request,
)

ACCEPTED_OPEN_ACCOUNTING_OPERATION = "agent_loop.prepare_accepted_open_accounting.v1"
ACCEPTED_OPEN_RECOVERY_OPERATION = "agent_loop.prepare_accepted_open_recovery.v1"
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
            (
                ACCEPTED_OPEN_RECOVERY_OPERATION,
                "broker",
                "agent_loop",
                REQUEST_SCHEMA,
                RESULT_SCHEMA,
            ),
        )
    )
)


def dispatch(operation: str, payload: bytes) -> dict[str, object]:
    if operation not in (
        ACCEPTED_OPEN_ACCOUNTING_OPERATION,
        ACCEPTED_OPEN_RECOVERY_OPERATION,
    ):
        return _execution_next_turn_process.dispatch(operation, payload)
    try:
        decoded = decode_execution_recovery_request(REQUEST_SCHEMA, payload)
        request = decoded.value
        if operation == ACCEPTED_OPEN_ACCOUNTING_OPERATION:
            if not isinstance(request, PrepareAcceptedOpenAccounting):
                raise ValueError("accepted/open accounting operation requires accounting request")
            result = prepare_accepted_open_accounting(request)
        else:
            if not isinstance(request, PrepareAcceptedOpenRecoveryV1):
                raise ValueError("accepted/open recovery operation requires recovery request")
            result = prepare_accepted_open_recovery_v1(request)
        return {
            "payload": base64.b64encode(result.canonical_bytes()).decode(),
            "schema_id": RESULT_SCHEMA,
        }
    except (ValueError, TypeError, IndexError, KeyError) as error:
        return {"failure": "PROTOCOL_REJECTED", "reason": str(error)}
