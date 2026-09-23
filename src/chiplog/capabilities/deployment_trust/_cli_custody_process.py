"""Isolated CLI custody owner route; legacy candidate profile is unchanged."""

import base64

from . import _r17_process
from .cli_custody_validation import CliCustodyOwnerCall, evaluate_cli_custody

ROUTE = (
    "deployment_trust.authenticate_cli_custody",
    "broker",
    "deployment_trust",
    "chiplog.cli.custody-owner-call.v1",
    "chiplog.cli.custody-decision.v1",
)
ROUTES = tuple(sorted((*_r17_process.ROUTES, ROUTE)))


def dispatch(operation: str, payload: bytes) -> dict[str, object]:
    if operation != ROUTE[0]:
        return _r17_process.dispatch(operation, payload)
    try:
        if len(payload) > 262144:
            raise ValueError("custody owner request exceeds bound")
        call = CliCustodyOwnerCall.model_validate_json(payload)
        if call.canonical_bytes() != payload:
            raise ValueError("noncanonical custody owner call")
        result = evaluate_cli_custody(call).canonical_bytes()
        if len(result) > 65536:
            raise ValueError("custody owner result exceeds bound")
        return {"payload": base64.b64encode(result).decode(), "schema_id": ROUTE[4]}
    except (ValueError, TypeError, KeyError) as error:
        return {"failure": "PROTOCOL_REJECTED", "reason": str(error)}
