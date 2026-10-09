"""V30 owner partition for first accepted-call direct terminal accounting."""

from __future__ import annotations

import base64

from . import _execution_accepted_open_process
from .direct_terminal_preparation import prepare_accepted_direct_terminal_v1
from .direct_terminal_wire_contracts import REQUEST_SCHEMA, RESULT_SCHEMA, decode_request

OPERATION = "agent_loop.prepare_accepted_direct_terminal.v1"
ROUTES = tuple(
    sorted(
        (
            *_execution_accepted_open_process.ROUTES,
            (OPERATION, "broker", "agent_loop", REQUEST_SCHEMA, RESULT_SCHEMA),
        )
    )
)


def dispatch(operation: str, payload: bytes) -> dict[str, object]:
    if operation != OPERATION:
        return _execution_accepted_open_process.dispatch(operation, payload)
    try:
        request = decode_request(REQUEST_SCHEMA, payload)
        result = prepare_accepted_direct_terminal_v1(request)
        return {
            "payload": base64.b64encode(result.canonical_bytes()).decode(),
            "schema_id": RESULT_SCHEMA,
        }
    except (ValueError, TypeError, IndexError, KeyError) as error:
        return {"failure": "PROTOCOL_REJECTED", "reason": str(error)}
