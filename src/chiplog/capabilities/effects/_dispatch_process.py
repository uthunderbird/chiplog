"""Explicit profile-10 dispatch preparation, preserving the profile-7 routes."""

import base64

from . import _r16_process
from .dispatch_v2 import (
    DispatchPreparationV2,
    PrecursorPreparationV2,
    evaluate_precursor,
    prepare_dispatch,
)
from .dispatch_v2_contracts import DispatchBoundaryFailureV2
from .domain import EffectRuleViolation

ROUTES = tuple(
    sorted(
        (
            *_r16_process.ROUTES,
            (
                "effects.evaluate_dispatch_mandate_v2",
                "broker",
                "effects",
                "chiplog.effects.precursor-preparation.v2",
                "chiplog.effects.dispatch-precursor-result.v2",
            ),
            (
                "effects.prepare_dispatch_v2",
                "broker",
                "effects",
                "chiplog.effects.dispatch-preparation.v2",
                "chiplog.effects.dispatch-record.v2",
            ),
        )
    )
)


def dispatch(operation: str, payload: bytes) -> dict[str, object]:
    if operation not in {"effects.evaluate_dispatch_mandate_v2", "effects.prepare_dispatch_v2"}:
        return _r16_process.dispatch(operation, payload)
    try:
        if operation == "effects.evaluate_dispatch_mandate_v2":
            precursor = PrecursorPreparationV2.model_validate_json(payload)
            if precursor.canonical_bytes() != payload:
                raise ValueError("noncanonical precursor request")
            result = evaluate_precursor(precursor.request, precursor.mandate).canonical_bytes()
            schema = "chiplog.effects.dispatch-precursor-result.v2"
        else:
            request = DispatchPreparationV2.model_validate_json(payload)
            if request.canonical_bytes() != payload:
                raise ValueError("noncanonical dispatch request")
            result = prepare_dispatch(request).canonical_bytes()
            schema = "chiplog.effects.dispatch-record.v2"
        return {"payload": base64.b64encode(result).decode(), "schema_id": schema}
    except EffectRuleViolation as error:
        failure = DispatchBoundaryFailureV2(
            schema_id="chiplog.effects.dispatch-boundary-failure.v2",
            kind="DISPATCH_VERSION_HOLD" if error.code == "DISPATCH_VERSION_HOLD" else "DENIED",
            source_role=None,
            reason=str(error),
        )
        return {
            "payload": base64.b64encode(failure.canonical_bytes()).decode(),
            "schema_id": "chiplog.effects.dispatch-boundary-failure.v2",
        }
    except (ValueError, TypeError, KeyError) as error:
        return {"failure": "PROTOCOL_REJECTED", "reason": str(error)}
