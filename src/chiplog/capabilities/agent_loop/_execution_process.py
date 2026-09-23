"""Executable preparation router; legacy routes retain their registered handlers."""

import base64

from . import _r14_fanout_process
from .execution_fan_out_contracts import ExecutionCapturedFanOutRequest
from .execution_fan_out_preparation import prepare_execution_captured_fan_out

OPERATION = "agent_loop.prepare_execution_captured_fan_out"
REQUEST_SCHEMA = "chiplog.call.execution-captured-fanout-preparation.v2"
RESULT_SCHEMA = "chiplog.call.execution-captured-fanout-result.v2"
ROUTES = tuple(
    sorted(
        (
            *_r14_fanout_process.ROUTES,
            (OPERATION, "broker", "agent_loop", REQUEST_SCHEMA, RESULT_SCHEMA),
        )
    )
)


def dispatch(operation: str, payload: bytes) -> dict[str, object]:
    if operation != OPERATION:
        return _r14_fanout_process.dispatch(operation, payload)
    try:
        request = ExecutionCapturedFanOutRequest.model_validate_json(payload)
        if request.canonical_bytes() != payload:
            raise ValueError("noncanonical executable captured fan-out request")
        result = prepare_execution_captured_fan_out(request)
        return {
            "payload": base64.b64encode(result.canonical_bytes()).decode(),
            "schema_id": RESULT_SCHEMA,
        }
    except (ValueError, TypeError) as error:
        return {"failure": "PROTOCOL_REJECTED", "reason": str(error)}
