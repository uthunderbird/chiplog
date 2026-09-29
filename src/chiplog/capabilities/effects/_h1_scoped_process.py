"""Process-local H1 scoped producer route; mounting is owned by composition."""

from __future__ import annotations

import base64

from . import _h1_local_process
from .h1_scoped_preparation import prepare_h1_scoped_delivery
from .h1_scoped_preparation_contracts import H1ScopedDeliveryOwnerCallV1

ROUTE = (
    "effects.prepare_h1_scoped_delivery",
    "broker",
    "effects",
    "chiplog.effects.h1-scoped-delivery-owner-call.v1",
    "chiplog.effects.prepared-h1-scoped-delivery.v1",
)
ROUTES = tuple(sorted((*_h1_local_process.ROUTES, ROUTE)))


def dispatch(operation: str, payload: bytes) -> dict[str, object]:
    """Decode only the canonical authenticated envelope and return canonical bytes."""
    if operation != ROUTE[0]:
        return _h1_local_process.dispatch(operation, payload)
    try:
        call = H1ScopedDeliveryOwnerCallV1.model_validate_json(payload)
        if call.canonical_bytes() != payload:
            raise ValueError("noncanonical H1 scoped owner call")
        result = prepare_h1_scoped_delivery(call)
        return {
            "payload": base64.b64encode(result.canonical_bytes()).decode(),
            "schema_id": "chiplog.effects.prepared-h1-scoped-delivery.v1",
        }
    except (TypeError, ValueError, IndexError) as error:
        return {"failure": "PROTOCOL_REJECTED", "reason": str(error)}


__all__ = ["ROUTE", "ROUTES", "dispatch"]
