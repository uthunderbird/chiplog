"""Closed owner delivery routes; observations confer no broker authority."""

import base64

from .delivery_preparation import (
    DELIVERY_TOOLS,
    DeliveryPrepareRequest,
    DeliveryValidateRequest,
    complete_delivery,
    delivery_response_adapter,
)
from .domain import validate_record

# Resolve the registered schema's lazy library imports before raw authority is denied.
delivery_response_adapter(DELIVERY_TOOLS).json_schema()

ROUTES = (
    (
        "agent_loop.prepare_delivery_completion",
        "broker",
        "agent_loop",
        "chiplog.delivery.prepare-completion.v1",
        "chiplog.agent-loop.record.v1",
    ),
    (
        "agent_loop.validate_delivery_completion",
        "broker",
        "agent_loop",
        "chiplog.delivery.validate-completion.v1",
        "chiplog.agent-loop.record.v1",
    ),
)


def dispatch(operation: str, payload: bytes) -> dict[str, object]:
    try:
        if operation == ROUTES[0][0]:
            request = DeliveryPrepareRequest.model_validate_json(payload)
            if request.canonical_bytes() != payload:
                raise ValueError("noncanonical delivery preparation request")
            proposed = complete_delivery(request.previous, request.observation)
        elif operation == ROUTES[1][0]:
            validation = DeliveryValidateRequest.model_validate_json(payload)
            if validation.canonical_bytes() != payload:
                raise ValueError("noncanonical delivery validation request")
            if validation.proposed.accepted_delivery_binding == "LEGACY_R13":
                raise ValueError("delivery route requires expanded acceptance reference")
            validate_record(validation.previous, validation.proposed, validation.observation)
            proposed = validation.proposed
        else:
            raise ValueError("unknown delivery owner operation")
        return {
            "payload": base64.b64encode(proposed.canonical_bytes()).decode(),
            "schema_id": "chiplog.agent-loop.record.v1",
        }
    except (ValueError, TypeError, IndexError) as error:
        return {"failure": "PROTOCOL_REJECTED", "reason": str(error)}
