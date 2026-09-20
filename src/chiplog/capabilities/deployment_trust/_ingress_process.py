"""Schema-only candidate normalization; no credentials, replay or writer."""

import base64

from .ingress_contracts import TelegramNormalizedCandidate, TelegramNormalizeRequest

ROUTES = (
    (
        "deployment_trust.normalize_telegram_candidate",
        "broker",
        "deployment_trust",
        "chiplog.telegram.normalize.v1",
        "chiplog.telegram.normalized-candidate.v1",
    ),
)


def dispatch(operation: str, payload: bytes) -> dict[str, object]:
    try:
        if operation != ROUTES[0][0]:
            raise ValueError("unknown inert candidate operation")
        request = TelegramNormalizeRequest.model_validate_json(payload)
        if request.canonical_bytes() != payload:
            raise ValueError("noncanonical inert candidate request")
        candidate = TelegramNormalizedCandidate(
            candidate=request.candidate,
            parsed_update_identity=request.parsed_update_identity,
            candidate_fields=request.candidate_fields,
        )
        return {
            "payload": base64.b64encode(candidate.canonical_bytes()).decode(),
            "schema_id": "chiplog.telegram.normalized-candidate.v1",
        }
    except (ValueError, TypeError) as error:
        return {"failure": "PROTOCOL_REJECTED", "reason": str(error)}
