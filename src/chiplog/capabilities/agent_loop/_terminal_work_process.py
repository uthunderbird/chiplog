"""Unmounted H1 terminal-work owner route; it has no publication authority."""

from __future__ import annotations

import base64

from .post_terminal_contracts import PrepareTerminalWork
from .terminal_work_preparation import prepare_h1_terminal_work

OPERATION = "agent_loop.prepare_terminal_work"
REQUEST_SCHEMA = "chiplog.agent-loop.prepare-terminal-work.v1"
RESULT_SCHEMA = "chiplog.agent-loop.prepared-post-terminal-work-result.v1"
ROUTES = ((OPERATION, "broker", "agent_loop", REQUEST_SCHEMA, RESULT_SCHEMA),)


def dispatch(operation: str, payload: bytes) -> dict[str, object]:
    if operation != OPERATION:
        return {"failure": "PROTOCOL_REJECTED", "reason": "unknown terminal work operation"}
    try:
        request = PrepareTerminalWork.model_validate_json(payload)
        if request.canonical_bytes() != payload:
            raise ValueError("noncanonical terminal work request")
        result = prepare_h1_terminal_work(request)
        return {
            "payload": base64.b64encode(result.canonical_bytes()).decode(),
            "schema_id": RESULT_SCHEMA,
        }
    except (ValueError, TypeError) as error:
        return {"failure": "PROTOCOL_REJECTED", "reason": str(error)}
