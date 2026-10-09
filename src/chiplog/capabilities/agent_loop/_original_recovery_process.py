"""Owner process route for the original-loop recovery family."""

from __future__ import annotations

import base64

from . import _execution_direct_terminal_process
from .original_recovery_contracts import AdoptOriginalResolverAuthorityPolicyV1
from .original_resolver_policy_preparation import (
    prepare_original_resolver_authority_policy_v1,
)
from .recovery_wire_contracts import (
    ORIGINAL_RECOVERY_REQUEST_SCHEMA,
    ORIGINAL_RECOVERY_RESULT_SCHEMA,
    decode_original_recovery_request,
)

ADOPT_POLICY_OPERATION = "agent_loop.adopt_original_resolver_authority_policy.v1"
REQUEST_SCHEMA = ORIGINAL_RECOVERY_REQUEST_SCHEMA
RESULT_SCHEMA = ORIGINAL_RECOVERY_RESULT_SCHEMA
ROUTES = tuple(
    sorted(
        (
            *_execution_direct_terminal_process.ROUTES,
            (ADOPT_POLICY_OPERATION, "broker", "agent_loop", REQUEST_SCHEMA, RESULT_SCHEMA),
        )
    )
)


def dispatch(operation: str, payload: bytes) -> dict[str, object]:
    if operation != ADOPT_POLICY_OPERATION:
        return _execution_direct_terminal_process.dispatch(operation, payload)
    try:
        decoded = decode_original_recovery_request(REQUEST_SCHEMA, payload)
        request = decoded.value
        if not isinstance(request, AdoptOriginalResolverAuthorityPolicyV1):
            raise ValueError("policy adoption operation requires the exact adoption request")
        result = prepare_original_resolver_authority_policy_v1(request)
        return {
            "payload": base64.b64encode(result.canonical_bytes()).decode(),
            "schema_id": RESULT_SCHEMA,
        }
    except (TypeError, ValueError) as error:
        return {"failure": "PROTOCOL_REJECTED", "reason": str(error)}
