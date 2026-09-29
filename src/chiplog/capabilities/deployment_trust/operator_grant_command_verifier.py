"""Pure verification of retained operator authorization for V2 grant requests.

This verifies supplied immutable values only.  Resolving the current binding and
selecting the retained source remain responsibilities of the calling trust owner.
"""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import Field

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead

from .cli_custody_contracts import CliCustodyDTO, Identity
from .operator_grant_authorization_contracts import (
    OperatorGrantAuthorizationPayloadV2,
    RetainedOperatorGrantAuthorizationSourceV2,
    SignedOperatorGrantAuthorizationV2,
)
from .operator_policy_command_verifier import _verify_ed25519_signature
from .prepared_external_delivery_contracts import (
    IssuePreparedExternalDeliveryGrantRequestV2,
    RevokePreparedExternalDeliveryGrantRequestV2,
)

OperatorGrantOperation = Literal[
    "ISSUE_PREPARED_EXTERNAL_DELIVERY_GRANT",
    "REVOKE_PREPARED_EXTERNAL_DELIVERY_GRANT",
]

OperatorGrantRequest = (
    IssuePreparedExternalDeliveryGrantRequestV2 | RevokePreparedExternalDeliveryGrantRequestV2
)


class OperatorGrantVerificationError(ValueError):
    """The supplied operator authorization does not verify for this grant request."""


class OperatorGrantKeyBindingV2(CliCustodyDTO):
    """A caller-supplied binding whose provenance/currentness this module does not claim."""

    ref: ExactHead
    tenant_id: Identity
    database_id: Identity
    operator_key_id: Identity
    policy_id: Identity
    public_key: bytes = Field(min_length=32, max_length=32)
    status: Literal["ACTIVE", "REVOKED"]
    allowed_operations: tuple[OperatorGrantOperation, ...]


def verify_operator_grant_request(
    request: OperatorGrantRequest,
    *,
    retained_source: RetainedOperatorGrantAuthorizationSourceV2,
    current_binding: OperatorGrantKeyBindingV2 | None,
) -> None:
    """Raise ``OperatorGrantVerificationError`` unless the exact request is authorized."""
    try:
        verified_request = _canonical_request(request)
        verified_source = _canonical_source(retained_source)
        verified_binding = _canonical_binding(current_binding)
        if verified_binding is None:
            raise OperatorGrantVerificationError("operator grant key binding is absent")

        signed = SignedOperatorGrantAuthorizationV2.model_validate_json(
            verified_source.canonical_source_bytes
        )
        if signed.canonical_bytes() != verified_source.canonical_source_bytes:
            raise OperatorGrantVerificationError("signed operator grant source is not canonical")
        payload = _canonical_payload(signed.payload)

        if payload.canonical_request_bytes != verified_request.canonical_bytes():
            raise OperatorGrantVerificationError("signed request bytes do not match grant request")
        if payload.request_sha256 != hashlib.sha256(payload.canonical_request_bytes).hexdigest():
            raise OperatorGrantVerificationError("signed request digest does not match")
        if (
            payload.operation != verified_request.operation
            or payload.tenant_id != verified_request.tenant_id
            or payload.database_id != verified_request.database_id
            or payload.grant_id != verified_request.grant_id
            or payload.policy_id != verified_request.policy_id
            or payload.command_id != verified_request.command_id
        ):
            raise OperatorGrantVerificationError("signed request fields do not match grant request")
        if (
            verified_binding.status != "ACTIVE"
            or verified_request.operation not in verified_binding.allowed_operations
            or verified_binding.tenant_id != verified_request.tenant_id
            or verified_binding.database_id != verified_request.database_id
            or verified_binding.policy_id != verified_request.policy_id
            or verified_binding.operator_key_id != payload.operator_key_id
        ):
            raise OperatorGrantVerificationError(
                "operator grant key binding does not authorize request"
            )

        _verify_ed25519_signature(
            verified_binding.public_key, signed.signature, payload.canonical_bytes()
        )
    except OperatorGrantVerificationError:
        raise
    except Exception as error:
        raise OperatorGrantVerificationError("operator grant verification failed") from error


def _canonical_request(request: OperatorGrantRequest) -> OperatorGrantRequest:
    if isinstance(request, IssuePreparedExternalDeliveryGrantRequestV2):
        verified: OperatorGrantRequest = (
            IssuePreparedExternalDeliveryGrantRequestV2.model_validate_json(request.canonical_bytes())
        )
    elif isinstance(request, RevokePreparedExternalDeliveryGrantRequestV2):
        verified = RevokePreparedExternalDeliveryGrantRequestV2.model_validate_json(
            request.canonical_bytes()
        )
    else:
        raise OperatorGrantVerificationError("unsupported operator grant request")
    if verified.canonical_bytes() != request.canonical_bytes():
        raise OperatorGrantVerificationError("operator grant request is not canonical")
    return verified


def _canonical_source(
    retained_source: RetainedOperatorGrantAuthorizationSourceV2,
) -> RetainedOperatorGrantAuthorizationSourceV2:
    verified = RetainedOperatorGrantAuthorizationSourceV2.model_validate_json(
        retained_source.canonical_bytes()
    )
    if verified.canonical_bytes() != retained_source.canonical_bytes():
        raise OperatorGrantVerificationError(
            "retained operator grant source wrapper is not canonical"
        )
    return verified


def _canonical_binding(
    current_binding: OperatorGrantKeyBindingV2 | None,
) -> OperatorGrantKeyBindingV2 | None:
    if current_binding is None:
        return None
    verified = OperatorGrantKeyBindingV2.model_validate_json(current_binding.canonical_bytes())
    if verified.canonical_bytes() != current_binding.canonical_bytes():
        raise OperatorGrantVerificationError("operator grant key binding is not canonical")
    return verified


def _canonical_payload(
    payload: OperatorGrantAuthorizationPayloadV2,
) -> OperatorGrantAuthorizationPayloadV2:
    verified = OperatorGrantAuthorizationPayloadV2.model_validate_json(payload.canonical_bytes())
    if verified.canonical_bytes() != payload.canonical_bytes():
        raise OperatorGrantVerificationError(
            "operator grant authorization payload is not canonical"
        )
    return verified
