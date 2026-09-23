"""Closed isolated call preparation routes; no mutation or raw execution."""

import base64

from .call_acceptance_contracts import AcceptConsequentialCallRequest, CancelBeforeAcceptRequest
from .call_acceptance_preparation import (
    prepare_consequential_acceptance,
    prepare_pre_accept_cancellation,
)

ROUTES = (
    (
        "agent_loop.prepare_consequential_acceptance",
        "broker",
        "agent_loop",
        "chiplog.call.acceptance-preparation.v1",
        "chiplog.call.preparation-result.v1",
    ),
    (
        "agent_loop.prepare_pre_accept_cancellation",
        "broker",
        "agent_loop",
        "chiplog.call.cancellation-preparation.v1",
        "chiplog.call.preparation-result.v1",
    ),
)


def dispatch(operation: str, payload: bytes) -> dict[str, object]:
    try:
        if operation == "agent_loop.prepare_consequential_acceptance":
            acceptance = AcceptConsequentialCallRequest.model_validate_json(payload)
            if acceptance.canonical_bytes() != payload:
                raise ValueError("noncanonical acceptance preparation request")
            result = prepare_consequential_acceptance(acceptance).canonical_bytes()
        elif operation == "agent_loop.prepare_pre_accept_cancellation":
            cancellation = CancelBeforeAcceptRequest.model_validate_json(payload)
            if cancellation.canonical_bytes() != payload:
                raise ValueError("noncanonical cancellation preparation request")
            result = prepare_pre_accept_cancellation(cancellation).canonical_bytes()
        else:
            return {"failure": "PROTOCOL_REJECTED", "reason": "unknown call preparation operation"}
        return {
            "payload": base64.b64encode(result).decode(),
            "schema_id": "chiplog.call.preparation-result.v1",
        }
    except (ValueError, TypeError) as error:
        return {"failure": "PROTOCOL_REJECTED", "reason": str(error)}
