"""Owner process route for the original-loop recovery family."""

from __future__ import annotations

import base64

from . import _execution_direct_terminal_process, _execution_recovery_process
from .native_sealed_accounting_v3_contracts import (
    NATIVE_SEALED_ACCOUNTING_V3_REQUEST_SCHEMA,
    NATIVE_SEALED_ACCOUNTING_V3_RESULT_SCHEMA,
    PrepareNativeSealedAccountingV3,
)
from .native_sealed_accounting_v3_preparation import prepare_native_sealed_accounting_v3
from .original_call_resolution_preparation import prepare_original_call_resolution
from .original_recovery_contracts import (
    AdoptOriginalResolverAuthorityPolicyV1,
    PrepareLoopSemanticReduction,
    PrepareOriginalCallResolution,
    PrepareOriginalResolverInvocationV1,
)
from .original_resolver_invocation_preparation import (
    prepare_original_resolver_invocation_v1,
)
from .original_resolver_policy_preparation import (
    prepare_original_resolver_authority_policy_v1,
)
from .original_semantic_reduction_preparation import prepare_loop_semantic_reduction
from .recovery_wire_contracts import (
    ORIGINAL_RECOVERY_REQUEST_SCHEMA,
    ORIGINAL_RECOVERY_RESULT_SCHEMA,
    decode_original_recovery_request,
)

ADOPT_POLICY_OPERATION = "agent_loop.adopt_original_resolver_authority_policy.v1"
PREPARE_INVOCATION_OPERATION = "agent_loop.prepare_original_resolver_invocation.v1"
PREPARE_RESOLUTION_OPERATION = "agent_loop.prepare_original_call_resolution.v1"
PREPARE_SEMANTIC_REDUCTION_OPERATION = "agent_loop.prepare_loop_semantic_reduction.v1"
PREPARE_SUSPENSION_OPERATION = "agent_loop.prepare_execution_suspension.v1"
PREPARE_RESUME_OPERATION = "agent_loop.prepare_execution_resume.v1"
PREPARE_SUCCESSOR_OPERATION = "agent_loop.prepare_execution_successor.v1"
PREPARE_SEALED_ACCOUNTING_V3_OPERATION = "agent_loop.prepare_native_sealed_accounting.v3"
REQUEST_SCHEMA = ORIGINAL_RECOVERY_REQUEST_SCHEMA
RESULT_SCHEMA = ORIGINAL_RECOVERY_RESULT_SCHEMA
ROUTES = tuple(
    sorted(
        (
            *_execution_direct_terminal_process.ROUTES,
            (ADOPT_POLICY_OPERATION, "broker", "agent_loop", REQUEST_SCHEMA, RESULT_SCHEMA),
            (PREPARE_INVOCATION_OPERATION, "broker", "agent_loop", REQUEST_SCHEMA, RESULT_SCHEMA),
            (PREPARE_RESOLUTION_OPERATION, "broker", "agent_loop", REQUEST_SCHEMA, RESULT_SCHEMA),
            (
                PREPARE_SEMANTIC_REDUCTION_OPERATION,
                "broker",
                "agent_loop",
                REQUEST_SCHEMA,
                RESULT_SCHEMA,
            ),
            (
                PREPARE_SEALED_ACCOUNTING_V3_OPERATION,
                "broker",
                "agent_loop",
                NATIVE_SEALED_ACCOUNTING_V3_REQUEST_SCHEMA,
                NATIVE_SEALED_ACCOUNTING_V3_RESULT_SCHEMA,
            ),
        )
    )
)


def dispatch(operation: str, payload: bytes) -> dict[str, object]:
    if operation in (
        PREPARE_SUSPENSION_OPERATION,
        PREPARE_RESUME_OPERATION,
        PREPARE_SUCCESSOR_OPERATION,
    ):
        return _execution_recovery_process.dispatch(operation, payload)
    if operation == PREPARE_SEALED_ACCOUNTING_V3_OPERATION:
        try:
            request = PrepareNativeSealedAccountingV3.model_validate_json(payload)
            if request.canonical_bytes() != payload:
                raise ValueError("noncanonical native sealed accounting V3 request")
            result = prepare_native_sealed_accounting_v3(request)
            return {
                "payload": base64.b64encode(result.canonical_bytes()).decode(),
                "schema_id": NATIVE_SEALED_ACCOUNTING_V3_RESULT_SCHEMA,
            }
        except (TypeError, ValueError) as error:
            return {"failure": "PROTOCOL_REJECTED", "reason": str(error)}
    if operation not in (
        ADOPT_POLICY_OPERATION,
        PREPARE_INVOCATION_OPERATION,
        PREPARE_RESOLUTION_OPERATION,
        PREPARE_SEMANTIC_REDUCTION_OPERATION,
    ):
        return _execution_direct_terminal_process.dispatch(operation, payload)
    try:
        decoded = decode_original_recovery_request(REQUEST_SCHEMA, payload)
        request = decoded.value
        if operation == ADOPT_POLICY_OPERATION:
            if not isinstance(request, AdoptOriginalResolverAuthorityPolicyV1):
                raise ValueError("policy adoption operation requires the exact adoption request")
            result_payload = prepare_original_resolver_authority_policy_v1(
                request
            ).canonical_bytes()
        elif operation == PREPARE_INVOCATION_OPERATION:
            if not isinstance(request, PrepareOriginalResolverInvocationV1):
                raise ValueError("invocation operation requires the exact invocation request")
            result_payload = prepare_original_resolver_invocation_v1(request).canonical_bytes()
        elif operation == PREPARE_RESOLUTION_OPERATION:
            if not isinstance(request, PrepareOriginalCallResolution):
                raise ValueError("resolution operation requires the exact resolution request")
            result_payload = prepare_original_call_resolution(request).canonical_bytes()
        else:
            if not isinstance(request, PrepareLoopSemanticReduction):
                raise ValueError(
                    "semantic reduction operation requires the exact preparation request"
                )
            result_payload = prepare_loop_semantic_reduction(request).canonical_bytes()
        return {
            "payload": base64.b64encode(result_payload).decode(),
            "schema_id": RESULT_SCHEMA,
        }
    except (TypeError, ValueError) as error:
        return {"failure": "PROTOCOL_REJECTED", "reason": str(error)}
