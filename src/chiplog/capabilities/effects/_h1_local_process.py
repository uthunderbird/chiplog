"""H1 effects aggregate owner route: legacy preparation plus local receipt only."""

from __future__ import annotations

import base64

from . import _dispatch_process
from .h1_local_preparation import prepare_h1_local_commentary
from .h1_local_preparation_contracts import H1LocalCommentaryOwnerCallV1

ROUTES = tuple(
    sorted(
        (
            *_dispatch_process.ROUTES,
            (
                "effects.prepare_h1_local_commentary",
                "broker",
                "effects",
                "chiplog.effects.h1-local-commentary-owner-call.v1",
                "chiplog.effects.prepared-h1-local-commentary.v1",
            ),
        )
    )
)


def dispatch(operation: str, payload: bytes) -> dict[str, object]:
    if operation != "effects.prepare_h1_local_commentary":
        return _dispatch_process.dispatch(operation, payload)
    try:
        call = H1LocalCommentaryOwnerCallV1.model_validate_json(payload)
        if call.canonical_bytes() != payload:
            raise ValueError("noncanonical H1 local owner call")
        result = prepare_h1_local_commentary(call)
        return {
            "payload": base64.b64encode(result.canonical_bytes()).decode(),
            "schema_id": "chiplog.effects.prepared-h1-local-commentary.v1",
        }
    except (TypeError, ValueError, IndexError) as error:
        return {"failure": "PROTOCOL_REJECTED", "reason": str(error)}
