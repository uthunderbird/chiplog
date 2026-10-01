"""Versioned effects-owner route for pure scoped dispatch preparation."""

from __future__ import annotations

import base64

from pydantic import TypeAdapter

from . import _h1_scoped_process
from .scoped_dispatch_contracts import ScopedDispatchRequest, ScopedDispatchResult
from .scoped_dispatch_preparation import prepare_scoped_dispatch

ROUTE = (
    "effects.prepare_scoped_dispatch_v3",
    "broker",
    "effects",
    "chiplog.effects.scoped-dispatch-request.v3",
    "chiplog.effects.scoped-dispatch-result.v3",
)
ROUTES = tuple(sorted((*_h1_scoped_process.ROUTES, ROUTE)))

_REQUEST_ADAPTER: TypeAdapter[ScopedDispatchRequest] = TypeAdapter(ScopedDispatchRequest)
_RESULT_ADAPTER: TypeAdapter[ScopedDispatchResult] = TypeAdapter(ScopedDispatchResult)


def dispatch(operation: str, payload: bytes) -> dict[str, object]:
    """Accept exact canonical scoped requests and return one fixed result schema."""
    if operation != ROUTE[0]:
        return _h1_scoped_process.dispatch(operation, payload)
    try:
        request = _REQUEST_ADAPTER.validate_json(payload, strict=True)
        if request.canonical_bytes() != payload:
            raise ValueError("noncanonical scoped dispatch request")
        result = prepare_scoped_dispatch(request)
        result_bytes = result.canonical_bytes()
        decoded = _RESULT_ADAPTER.validate_json(result_bytes, strict=True)
        if decoded.canonical_bytes() != result_bytes:
            raise ValueError("noncanonical scoped dispatch result")
        return {
            "payload": base64.b64encode(result_bytes).decode(),
            "schema_id": ROUTE[4],
        }
    except (TypeError, ValueError, IndexError) as error:
        return {"failure": "PROTOCOL_REJECTED", "reason": str(error)}


__all__ = ["ROUTE", "ROUTES", "dispatch"]
