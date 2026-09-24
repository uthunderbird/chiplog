"""H0 inbox-initialization router layered over the unchanged lifecycle process."""

import base64

from pydantic import TypeAdapter

from . import _execution_lifecycle_process
from .execution_inbox_initialization import prepare_inbox_execution
from .execution_initialization_contracts import ExecutionInitializationRequest

OPERATION = "agent_loop.initialize_inbox.v1"
REQUEST_SCHEMA = "chiplog.execution.inbox-initialization.v1"
RESULT_SCHEMA = "chiplog.execution.inbox-initialization-result.v1"
ROUTES = tuple(
    sorted(
        (
            *_execution_lifecycle_process.ROUTES,
            (OPERATION, "broker", "agent_loop", REQUEST_SCHEMA, RESULT_SCHEMA),
        )
    )
)


def dispatch(operation: str, payload: bytes) -> dict[str, object]:
    if operation != OPERATION:
        return _execution_lifecycle_process.dispatch(operation, payload)
    try:
        adapter: TypeAdapter[ExecutionInitializationRequest] = TypeAdapter(
            ExecutionInitializationRequest
        )
        request = adapter.validate_json(payload)
        if request.canonical_bytes() != payload:
            raise ValueError("noncanonical execution inbox initialization request")
        result = prepare_inbox_execution(request)
        return {
            "payload": base64.b64encode(result.canonical_bytes()).decode(),
            "schema_id": RESULT_SCHEMA,
        }
    except (ValueError, TypeError) as error:
        return {"failure": "PROTOCOL_REJECTED", "reason": str(error)}
