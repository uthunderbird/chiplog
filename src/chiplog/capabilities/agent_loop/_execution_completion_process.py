"""Isolated owner route for native-v2 H1 completion preparation."""

from __future__ import annotations

import base64

from . import _execution_h0_process
from .execution_completion_contracts import PrepareExecutionCompletion
from .execution_completion_preparation import (
    prepare_execution_completion,
    prepare_first_path_execution_completion,
)
from .execution_first_path_completion_contracts import (
    FIRST_PATH_COMPLETION_SCHEMA,
    PrepareExecutionCompletionFirstPathV2,
    decode_completion_request,
)

OPERATION = "agent_loop.prepare_completion"
FIRST_PATH_OPERATION = "agent_loop.prepare_first_path_completion"
REQUEST_SCHEMA = "chiplog.agent-loop.prepare-execution-completion.v1"
FIRST_PATH_REQUEST_SCHEMA = FIRST_PATH_COMPLETION_SCHEMA
RESULT_SCHEMA = "chiplog.agent-loop.prepared-execution-completion-result.v1"
ROUTES = tuple(
    sorted(
        (
            *_execution_h0_process.ROUTES,
            (OPERATION, "broker", "agent_loop", REQUEST_SCHEMA, RESULT_SCHEMA),
            (
                FIRST_PATH_OPERATION,
                "broker",
                "agent_loop",
                FIRST_PATH_REQUEST_SCHEMA,
                RESULT_SCHEMA,
            ),
        )
    )
)


def dispatch(operation: str, payload: bytes) -> dict[str, object]:
    if operation not in {OPERATION, FIRST_PATH_OPERATION}:
        return _execution_h0_process.dispatch(operation, payload)
    try:
        request = decode_completion_request(payload)
        if operation == OPERATION:
            if not isinstance(request, PrepareExecutionCompletion):
                raise ValueError("legacy completion operation requires a v1 request")
            result = prepare_execution_completion(request)
        else:
            if not isinstance(request, PrepareExecutionCompletionFirstPathV2):
                raise ValueError("first-path completion operation requires a v2 request")
            result = prepare_first_path_execution_completion(request)
        return {
            "payload": base64.b64encode(result.canonical_bytes()).decode(),
            "schema_id": RESULT_SCHEMA,
        }
    except (ValueError, TypeError) as error:
        return {"failure": "PROTOCOL_REJECTED", "reason": str(error)}
