"""Isolated owner route for native-v2 H1 completion preparation."""

from __future__ import annotations

import base64

from . import _execution_h0_process
from .execution_completion_contracts import PrepareExecutionCompletion
from .execution_completion_preparation import prepare_execution_completion

OPERATION = "agent_loop.prepare_completion"
REQUEST_SCHEMA = "chiplog.agent-loop.prepare-execution-completion.v1"
RESULT_SCHEMA = "chiplog.agent-loop.prepared-execution-completion-result.v1"
ROUTES = tuple(
    sorted(
        (
            *_execution_h0_process.ROUTES,
            (OPERATION, "broker", "agent_loop", REQUEST_SCHEMA, RESULT_SCHEMA),
        )
    )
)


def dispatch(operation: str, payload: bytes) -> dict[str, object]:
    if operation != OPERATION:
        return _execution_h0_process.dispatch(operation, payload)
    try:
        request = PrepareExecutionCompletion.model_validate_json(payload)
        if request.canonical_bytes() != payload:
            raise ValueError("noncanonical execution completion request")
        result = prepare_execution_completion(request)
        return {
            "payload": base64.b64encode(result.canonical_bytes()).decode(),
            "schema_id": RESULT_SCHEMA,
        }
    except (ValueError, TypeError) as error:
        return {"failure": "PROTOCOL_REJECTED", "reason": str(error)}
