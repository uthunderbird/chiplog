"""Acceptance checks for the pure V2 operator-grant request verifier."""

import hashlib
from collections.abc import Callable
from typing import Literal

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead
from chiplog.capabilities.deployment_trust.operator_grant_authorization_contracts import (
    OperatorGrantAuthorizationPayloadV2,
    RetainedOperatorGrantAuthorizationSourceV2,
    SignedOperatorGrantAuthorizationV2,
    operator_grant_source_content_head_v2,
)
from chiplog.capabilities.deployment_trust.operator_grant_command_verifier import (
    OperatorGrantKeyBindingV2,
    OperatorGrantRequest,
    OperatorGrantVerificationError,
    verify_operator_grant_request,
)
from chiplog.capabilities.deployment_trust.operator_policy_authorization_contracts import (
    RetainedOperatorPolicyAuthorizationSourceV1,
)
from tests.capabilities.deployment_trust.test_prepared_external_delivery_grant_v2_contracts import (
    issue_request,
    revoke_request,
)

ISSUE: Literal["ISSUE_PREPARED_EXTERNAL_DELIVERY_GRANT"] = (
    "ISSUE_PREPARED_EXTERNAL_DELIVERY_GRANT"
)
REVOKE: Literal["REVOKE_PREPARED_EXTERNAL_DELIVERY_GRANT"] = (
    "REVOKE_PREPARED_EXTERNAL_DELIVERY_GRANT"
)


def _source_ref(raw: bytes) -> ExactHead:
    return ExactHead(
        identity="grant-operator-source",
        head=operator_grant_source_content_head_v2(raw),
        fingerprint=hashlib.sha256(raw).hexdigest(),
    )


def _signed_source(
    request: OperatorGrantRequest, key: Ed25519PrivateKey
) -> RetainedOperatorGrantAuthorizationSourceV2:
    request_bytes = request.canonical_bytes()
    payload = OperatorGrantAuthorizationPayloadV2(
        algorithm="Ed25519",
        source_id="grant-operator-source",
        operator_key_id="operator-key",
        tenant_id=request.tenant_id,
        database_id=request.database_id,
        grant_id=request.grant_id,
        policy_id=request.policy_id,
        command_id=request.command_id,
        operation=request.operation,
        request_sha256=hashlib.sha256(request_bytes).hexdigest(),
        canonical_request_bytes=request_bytes,
    )
    signed = SignedOperatorGrantAuthorizationV2(
        payload=payload, signature=key.sign(payload.canonical_bytes())
    )
    raw = signed.canonical_bytes()
    return RetainedOperatorGrantAuthorizationSourceV2(
        ref=_source_ref(raw), canonical_source_bytes=raw
    )


def _binding(key: Ed25519PrivateKey, **changes: object) -> OperatorGrantKeyBindingV2:
    binding = OperatorGrantKeyBindingV2(
        ref=ExactHead(identity="operator-key", head="binding", fingerprint="0" * 64),
        tenant_id="tenant",
        database_id="database",
        operator_key_id="operator-key",
        policy_id="policy",
        public_key=key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        ),
        status="ACTIVE",
        allowed_operations=(ISSUE, REVOKE),
    )
    return binding.model_copy(update=changes)


@pytest.mark.parametrize("make_request", (issue_request, revoke_request))
def test_verifies_real_ed25519_signed_issue_and_revoke(
    make_request: Callable[[], OperatorGrantRequest],
) -> None:
    key = Ed25519PrivateKey.generate()
    request = make_request()
    verify_operator_grant_request(
        request, retained_source=_signed_source(request, key), current_binding=_binding(key)
    )


def test_rejects_invalid_signature_request_bytes_source_ref_scope_permission_and_v1_source(
) -> None:
    key = Ed25519PrivateKey.generate()
    request = issue_request()
    retained = _signed_source(request, key)
    signed = SignedOperatorGrantAuthorizationV2.model_validate_json(retained.canonical_source_bytes)
    forged = signed.model_copy(
        update={"signature": bytes([signed.signature[0] ^ 1]) + signed.signature[1:]}
    )
    forged_raw = forged.canonical_bytes()
    forged_retained = RetainedOperatorGrantAuthorizationSourceV2(
        ref=_source_ref(forged_raw), canonical_source_bytes=forged_raw
    )
    invalid_source = RetainedOperatorGrantAuthorizationSourceV2.model_construct(
        ref=_source_ref(b"{}"), canonical_source_bytes=b"{}"
    )
    wrong_ref = RetainedOperatorGrantAuthorizationSourceV2.model_construct(
        ref=ExactHead(
            identity=retained.ref.identity,
            head="wrong-head",
            fingerprint=retained.ref.fingerprint,
        ),
        canonical_source_bytes=retained.canonical_source_bytes,
    )
    v1_source = RetainedOperatorPolicyAuthorizationSourceV1.model_construct(
        ref=retained.ref, canonical_source_bytes=b"{}"
    )
    cases: tuple[tuple[object, object, OperatorGrantKeyBindingV2], ...] = (
        (request, forged_retained, _binding(key)),
        (request, invalid_source, _binding(key)),
        (request, wrong_ref, _binding(key)),
        (request.model_copy(update={"command_id": "other-command"}), retained, _binding(key)),
        (request, retained, _binding(key, tenant_id="other-tenant")),
        (request, retained, _binding(key, policy_id="other-policy")),
        (request, retained, _binding(key, allowed_operations=())),
        (request, retained, _binding(key, status="REVOKED")),
        (request, v1_source, _binding(key)),
    )
    for supplied_request, source, binding in cases:
        with pytest.raises(OperatorGrantVerificationError):
            verify_operator_grant_request(
                supplied_request,  # type: ignore[arg-type]
                retained_source=source,  # type: ignore[arg-type]
                current_binding=binding,
            )


def test_rejects_model_constructed_malformed_binding_with_the_one_typed_denial() -> None:
    key = Ed25519PrivateKey.generate()
    request = issue_request()
    malformed = _binding(key).model_copy(update={"public_key": b"too short"})
    with pytest.raises(OperatorGrantVerificationError):
        verify_operator_grant_request(
            request, retained_source=_signed_source(request, key), current_binding=malformed
        )
