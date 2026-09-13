"""Isolated agent-loop owner. Receives inert bytes and has no raw authority."""

import base64

from .contracts import TransitionRequest
from .domain import validate_record

ROUTES = (
    (
        "agent_loop.validate_transition",
        "broker",
        "agent_loop",
        "chiplog.agent-loop.transition.v1",
        "chiplog.agent-loop.record.v1",
    ),
)


def dispatch(operation: str, payload: bytes) -> dict[str, object]:
    if operation != "agent_loop.validate_transition":
        return {"failure": "PROTOCOL_REJECTED", "reason": "unknown loop owner operation"}
    try:
        command = TransitionRequest.model_validate_json(payload)
        if command.canonical_bytes() != payload:
            raise ValueError("noncanonical transition command")
        validate_record(command.previous, command.proposed)
        return {
            "payload": base64.b64encode(command.proposed.canonical_bytes()).decode(),
            "schema_id": "chiplog.agent-loop.record.v1",
        }
    except (ValueError, TypeError, IndexError) as error:
        return {"failure": "PROTOCOL_REJECTED", "reason": str(error)}
