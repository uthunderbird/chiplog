"""Isolated captured fan-out preparation; no storage or execution authority."""

import base64

from .fan_out_contracts import CapturedFanOutRequest
from .fan_out_preparation import prepare_captured_fan_out

ROUTES = (
    (
        "agent_loop.prepare_captured_fan_out",
        "broker",
        "agent_loop",
        "chiplog.call.captured-fanout-preparation.v1",
        "chiplog.call.captured-fanout-result.v1",
    ),
)


def dispatch(operation: str, payload: bytes) -> dict[str, object]:
    if operation != "agent_loop.prepare_captured_fan_out":
        return {"failure": "PROTOCOL_REJECTED", "reason": "unknown captured fan-out operation"}
    try:
        request = CapturedFanOutRequest.model_validate_json(payload)
        if request.canonical_bytes() != payload:
            raise ValueError("noncanonical captured fan-out request")
        result = prepare_captured_fan_out(request)
        return {
            "payload": base64.b64encode(result.canonical_bytes()).decode(),
            "schema_id": "chiplog.call.captured-fanout-result.v1",
        }
    except (ValueError, TypeError) as error:
        return {"failure": "PROTOCOL_REJECTED", "reason": str(error)}
