"""Pure verification of retained operator authorization for policy commands.

This verifies supplied immutable values only.  Resolving the current binding and
selecting the retained source remain responsibilities of the calling trust owner.
"""

from __future__ import annotations

import hashlib
from typing import Literal

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import Field

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead

from .cli_custody_contracts import CliCustodyDTO, Identity
from .operator_policy_authorization_contracts import (
    OperatorPolicyAuthorizationPayloadV1,
    RetainedOperatorPolicyAuthorizationSourceV1,
    SignedOperatorPolicyAuthorizationV1,
)
from .prepared_external_delivery_policy_contracts import (
    IssuePreparedExternalSelfDeliveryPolicyRequestV1,
    IssuePreparedExternalSelfDeliveryPolicyV1,
    RevokePreparedExternalSelfDeliveryPolicyRequestV1,
    RevokePreparedExternalSelfDeliveryPolicyV1,
)

OperatorPolicyOperation = Literal[
    "ISSUE_PREPARED_EXTERNAL_SELF_DELIVERY_POLICY",
    "REVOKE_PREPARED_EXTERNAL_SELF_DELIVERY_POLICY",
]

OperatorPolicyCommand = (
    IssuePreparedExternalSelfDeliveryPolicyV1 | RevokePreparedExternalSelfDeliveryPolicyV1
)
OperatorPolicyRequest = (
    IssuePreparedExternalSelfDeliveryPolicyRequestV1
    | RevokePreparedExternalSelfDeliveryPolicyRequestV1
)


class OperatorPolicyVerificationError(ValueError):
    """The supplied operator authorization does not verify for this command."""


class OperatorPolicyKeyBindingV1(CliCustodyDTO):
    """A caller-supplied binding whose provenance/currentness this module does not claim."""

    ref: ExactHead
    tenant_id: Identity
    database_id: Identity
    operator_key_id: Identity
    policy_id: Identity
    public_key: bytes = Field(min_length=32, max_length=32)
    status: Literal["ACTIVE", "REVOKED"]
    allowed_operations: tuple[OperatorPolicyOperation, ...]


def verify_operator_policy_command(
    command: OperatorPolicyCommand,
    *,
    retained_source: RetainedOperatorPolicyAuthorizationSourceV1,
    current_binding: OperatorPolicyKeyBindingV1 | None,
) -> None:
    """Raise ``OperatorPolicyVerificationError`` unless the exact command is authorized."""

    try:
        verified_command = _canonical_command(command)
        verified_source = _canonical_source(retained_source)
        verified_binding = _canonical_binding(current_binding)
        if verified_binding is None:
            raise OperatorPolicyVerificationError("operator key binding is absent")

        if verified_command.authenticated_operator_source != verified_source.ref:
            raise OperatorPolicyVerificationError("authenticated operator source does not match")

        signed = SignedOperatorPolicyAuthorizationV1.model_validate_json(
            verified_source.canonical_source_bytes
        )
        if signed.canonical_bytes() != verified_source.canonical_source_bytes:
            raise OperatorPolicyVerificationError("signed operator source is not canonical")
        payload = _canonical_payload(signed.payload)
        request = verified_command.request

        if payload.canonical_request_bytes != request.canonical_bytes():
            raise OperatorPolicyVerificationError("signed request bytes do not match command")
        if payload.request_sha256 != hashlib.sha256(payload.canonical_request_bytes).hexdigest():
            raise OperatorPolicyVerificationError("signed request digest does not match")
        if (
            payload.operation != request.operation
            or payload.tenant_id != request.tenant_id
            or payload.database_id != request.database_id
            or payload.policy_id != request.policy_id
            or payload.command_id != request.command_id
        ):
            raise OperatorPolicyVerificationError("signed request fields do not match command")
        if (
            verified_binding.status != "ACTIVE"
            or request.operation not in verified_binding.allowed_operations
            or verified_binding.tenant_id != request.tenant_id
            or verified_binding.database_id != request.database_id
            or verified_binding.policy_id != request.policy_id
            or verified_binding.operator_key_id != payload.operator_key_id
        ):
            raise OperatorPolicyVerificationError("operator key binding does not authorize command")

        Ed25519PublicKey.from_public_bytes(verified_binding.public_key).verify(
            signed.signature, payload.canonical_bytes()
        )
    except OperatorPolicyVerificationError:
        raise
    except Exception as error:
        raise OperatorPolicyVerificationError("operator policy verification failed") from error


def _canonical_command(command: OperatorPolicyCommand) -> OperatorPolicyCommand:
    if isinstance(command, IssuePreparedExternalSelfDeliveryPolicyV1):
        verified: OperatorPolicyCommand = (
            IssuePreparedExternalSelfDeliveryPolicyV1.model_validate_json(command.canonical_bytes())
        )
    elif isinstance(command, RevokePreparedExternalSelfDeliveryPolicyV1):
        verified = RevokePreparedExternalSelfDeliveryPolicyV1.model_validate_json(
            command.canonical_bytes()
        )
    else:
        raise OperatorPolicyVerificationError("unsupported operator policy command")
    if verified.canonical_bytes() != command.canonical_bytes():
        raise OperatorPolicyVerificationError("operator policy command is not canonical")
    return verified


def _canonical_source(
    retained_source: RetainedOperatorPolicyAuthorizationSourceV1,
) -> RetainedOperatorPolicyAuthorizationSourceV1:
    verified = RetainedOperatorPolicyAuthorizationSourceV1.model_validate_json(
        retained_source.canonical_bytes()
    )
    if verified.canonical_bytes() != retained_source.canonical_bytes():
        raise OperatorPolicyVerificationError("retained operator source wrapper is not canonical")
    return verified


def _canonical_binding(
    current_binding: OperatorPolicyKeyBindingV1 | None,
) -> OperatorPolicyKeyBindingV1 | None:
    if current_binding is None:
        return None
    verified = OperatorPolicyKeyBindingV1.model_validate_json(current_binding.canonical_bytes())
    if verified.canonical_bytes() != current_binding.canonical_bytes():
        raise OperatorPolicyVerificationError("operator key binding is not canonical")
    return verified


def _canonical_payload(
    payload: OperatorPolicyAuthorizationPayloadV1,
) -> OperatorPolicyAuthorizationPayloadV1:
    verified = OperatorPolicyAuthorizationPayloadV1.model_validate_json(payload.canonical_bytes())
    if verified.canonical_bytes() != payload.canonical_bytes():
        raise OperatorPolicyVerificationError("operator authorization payload is not canonical")
    return verified
