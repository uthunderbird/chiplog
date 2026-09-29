"""Inert V2 offline authorization wires for prepared-external-delivery grants.

These are deliberately separate from policy authorization V1: an authentic
policy command is not an authorization to issue or revoke a delivery grant.  A
future deployment-trust owner resolves the retained source and its pinned key,
checks the signature, operation permission, physical selection and CAS under its
authority gate.  DTO validation only binds bytes and declared scope.
"""

import hashlib
from typing import Literal, Self

from pydantic import Field, model_validator

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead

from .cli_custody_contracts import CliCustodyDTO, Digest, Identity
from .prepared_external_delivery_contracts import (
    IssuePreparedExternalDeliveryGrantRequestV2,
    RevokePreparedExternalDeliveryGrantRequestV2,
)


class OperatorGrantAuthorizationPayloadV2(CliCustodyDTO):
    """Ed25519 payload over one exact V2 grant request's canonical bytes."""

    schema_id: Literal["chiplog.deployment-trust.operator-grant-authorization.v2"] = (
        "chiplog.deployment-trust.operator-grant-authorization.v2"
    )
    algorithm: Literal["Ed25519"]
    source_id: Identity
    operator_key_id: Identity
    tenant_id: Identity
    database_id: Identity
    grant_id: Identity
    policy_id: Identity
    command_id: Identity
    operation: Literal[
        "ISSUE_PREPARED_EXTERNAL_DELIVERY_GRANT",
        "REVOKE_PREPARED_EXTERNAL_DELIVERY_GRANT",
    ]
    request_sha256: Digest
    canonical_request_bytes: bytes = Field(min_length=1)

    @model_validator(mode="after")
    def exact_request(self) -> Self:
        request: (
            IssuePreparedExternalDeliveryGrantRequestV2
            | RevokePreparedExternalDeliveryGrantRequestV2
        )
        if self.operation == "ISSUE_PREPARED_EXTERNAL_DELIVERY_GRANT":
            request = IssuePreparedExternalDeliveryGrantRequestV2.model_validate_json(
                self.canonical_request_bytes
            )
        else:
            request = RevokePreparedExternalDeliveryGrantRequestV2.model_validate_json(
                self.canonical_request_bytes
            )
        if request.canonical_bytes() != self.canonical_request_bytes:
            raise ValueError("operator grant request bytes must be canonical")
        if hashlib.sha256(self.canonical_request_bytes).hexdigest() != self.request_sha256:
            raise ValueError("operator grant request SHA256 mismatch")
        for name in (
            "operation",
            "tenant_id",
            "database_id",
            "grant_id",
            "policy_id",
            "command_id",
        ):
            if getattr(request, name) != getattr(self, name):
                raise ValueError(f"operator grant authorization request {name} mismatch")
        return self


class SignedOperatorGrantAuthorizationV2(CliCustodyDTO):
    """Unverified Ed25519 signature over ``payload.canonical_bytes()`` only."""

    schema_id: Literal["chiplog.deployment-trust.signed-operator-grant-authorization.v2"] = (
        "chiplog.deployment-trust.signed-operator-grant-authorization.v2"
    )
    payload: OperatorGrantAuthorizationPayloadV2
    signature: bytes = Field(min_length=64, max_length=64)


def operator_grant_source_content_head_v2(canonical_source_bytes: bytes) -> str:
    """Return the V2 source content revision; it is not authority evidence."""
    return hashlib.sha256(
        b"chiplog.deployment-trust.operator-grant-source-content-head.v2\x00"
        + canonical_source_bytes
    ).hexdigest()


class RetainedOperatorGrantAuthorizationSourceV2(CliCustodyDTO):
    """Canonical signed-source preimage, separate from V1 policy source domain."""

    ref: ExactHead
    canonical_source_bytes: bytes = Field(min_length=1)

    @model_validator(mode="after")
    def exact_source(self) -> Self:
        source = SignedOperatorGrantAuthorizationV2.model_validate_json(self.canonical_source_bytes)
        if source.canonical_bytes() != self.canonical_source_bytes:
            raise ValueError("operator grant source bytes must be canonical")
        if self.ref.identity != source.payload.source_id:
            raise ValueError("operator grant source identity mismatch")
        if hashlib.sha256(self.canonical_source_bytes).hexdigest() != self.ref.fingerprint:
            raise ValueError("operator grant source fingerprint mismatch")
        if operator_grant_source_content_head_v2(self.canonical_source_bytes) != self.ref.head:
            raise ValueError("operator grant source content head mismatch")
        return self
