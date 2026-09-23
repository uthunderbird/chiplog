"""Versioned execution lifecycle router; existing fanout routes are unchanged."""

import base64

from pydantic import TypeAdapter

from . import _execution_process
from .execution_transition_contracts import ExecutionTransitionRequest
from .execution_transitions import prepare_execution_transition

OPERATION = "agent_loop.prepare_execution_transition"
REQUEST_SCHEMA = "chiplog.execution.transition-request.v2"
RESULT_SCHEMA = "chiplog.execution.transition-result.v2"
ROUTES = tuple(
    sorted(
        (
            *_execution_process.ROUTES,
            (OPERATION, "broker", "agent_loop", REQUEST_SCHEMA, RESULT_SCHEMA),
        )
    )
)


def dispatch(operation: str, payload: bytes) -> dict[str, object]:
    if operation != OPERATION:
        return _execution_process.dispatch(operation, payload)
    try:
        adapter: TypeAdapter[ExecutionTransitionRequest] = TypeAdapter(ExecutionTransitionRequest)
        request = adapter.validate_json(payload)
        if request.canonical_bytes() != payload:
            raise ValueError("noncanonical execution transition request")
        result = prepare_execution_transition(request)
        return {
            "payload": base64.b64encode(result.canonical_bytes()).decode(),
            "schema_id": RESULT_SCHEMA,
        }
    except (ValueError, TypeError) as error:
        return {"failure": "PROTOCOL_REJECTED", "reason": str(error)}
