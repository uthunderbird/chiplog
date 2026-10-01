"""Process-local V2 H1 producer route; V2 input never falls through to V1."""

from __future__ import annotations

import base64

from . import _scoped_dispatch_process
from .h1_scoped_preparation import prepare_h1_scoped_delivery_v2
from .h1_scoped_preparation_v2_contracts import H1ScopedDeliveryOwnerCallV2

ROUTE = (
    "effects.prepare_h1_scoped_delivery_v2",
    "broker",
    "effects",
    "chiplog.effects.h1-scoped-delivery-owner-call.v2",
    "chiplog.effects.prepared-h1-scoped-delivery.v2",
)
ROUTES = tuple(sorted((*_scoped_dispatch_process.ROUTES, ROUTE)))


def dispatch(operation: str, payload: bytes) -> dict[str, object]:
    if operation != ROUTE[0]:
        return _scoped_dispatch_process.dispatch(operation, payload)
    try:
        call = H1ScopedDeliveryOwnerCallV2.model_validate_json(payload)
        if call.canonical_bytes() != payload:
            raise ValueError("noncanonical H1 V2 scoped owner call")
        result = prepare_h1_scoped_delivery_v2(call)
        return {
            "payload": base64.b64encode(result.canonical_bytes()).decode(),
            "schema_id": ROUTE[4],
        }
    except (TypeError, ValueError, IndexError) as error:
        return {"failure": "PROTOCOL_REJECTED", "reason": str(error)}
