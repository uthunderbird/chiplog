"""Versioned isolated denial preparation alongside the unchanged legacy owner route."""

import base64

from . import _process
from .contracts import EffectDenied
from .denial import prepare_denial
from .denial_contracts import DenialPreparationRequest
from .domain import EffectRuleViolation

ROUTES = tuple(
    sorted(
        (
            *_process.ROUTES,
            (
                "effects.prepare_denial",
                "broker",
                "effects",
                "chiplog.effects.before-send-preparation.v2",
                "chiplog.effects.before-send-prepared-publication.v2",
            ),
        )
    )
)


def dispatch(operation: str, payload: bytes) -> dict[str, object]:
    if operation != "effects.prepare_denial":
        return _process.dispatch(operation, payload)
    try:
        request = DenialPreparationRequest.model_validate_json(payload)
        if request.canonical_bytes() != payload:
            raise ValueError("noncanonical denying preparation request")
        try:
            result = prepare_denial(request).canonical_bytes()
        except EffectRuleViolation as error:
            result = EffectDenied.model_validate(
                {
                    "disposition": error.code,
                    "command_id": request.command.identity.command_id,
                    "reason": str(error),
                }
            ).canonical_bytes()
        return {
            "payload": base64.b64encode(result).decode(),
            "schema_id": "chiplog.effects.before-send-prepared-publication.v2",
        }
    except (ValueError, TypeError, IndexError) as error:
        return {"failure": "PROTOCOL_REJECTED", "reason": str(error)}
