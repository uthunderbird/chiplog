"""Inert offline operator signatures; valid DTOs confer no authority.

The future trust owner verifies Ed25519 with an independently pinned public key
from authenticated trust genesis/binding or protected owner configuration. A key
ID, caller-supplied key, CLI login or existing journal HMAC cannot establish that
pin. Missing/unsupported pins deny; no default operator authority exists here.
The owner checks key authorization/currentness, retained source selection and
the request's trust/policy CAS atomically under the authority gate, with durable
command replay protection. This module performs no cryptographic verification,
key enrollment, durable registration, issuance or runtime authentication.
"""

import hashlib
from typing import Literal, Self

from pydantic import Field, model_validator

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead

from .cli_custody_contracts import CliCustodyDTO, Digest, Identity
from .prepared_external_delivery_policy_contracts import (
    IssuePreparedExternalSelfDeliveryPolicyRequestV1,
    RevokePreparedExternalSelfDeliveryPolicyRequestV1,
)


class OperatorPolicyAuthorizationPayloadV1(CliCustodyDTO):
    """Ed25519 signs exactly canonical_bytes(), including this schema domain.

    The retained request is the unwrapped ISSUE/REVOKE request, which includes
    expected trust and policy heads. It never includes the authorization-source
    head. The signed source's own head is computed only after signing, avoiding
    a circular hash. SHA256 alone is a consistency check, never authorization.
    """

    schema_id: Literal["chiplog.deployment-trust.operator-policy-authorization.v1"] = (
        "chiplog.deployment-trust.operator-policy-authorization.v1"
    )
    algorithm: Literal["Ed25519"]
    source_id: Identity
    operator_key_id: Identity
    tenant_id: Identity
    database_id: Identity
    policy_id: Identity
    command_id: Identity
    operation: Literal[
        "ISSUE_PREPARED_EXTERNAL_SELF_DELIVERY_POLICY",
        "REVOKE_PREPARED_EXTERNAL_SELF_DELIVERY_POLICY",
    ]
    request_sha256: Digest
    canonical_request_bytes: bytes = Field(min_length=1)

    @model_validator(mode="after")
    def exact_request(self) -> Self:
        request: (
            IssuePreparedExternalSelfDeliveryPolicyRequestV1
            | RevokePreparedExternalSelfDeliveryPolicyRequestV1
        )
        if self.operation == "ISSUE_PREPARED_EXTERNAL_SELF_DELIVERY_POLICY":
            request = IssuePreparedExternalSelfDeliveryPolicyRequestV1.model_validate_json(
                self.canonical_request_bytes
            )
        else:
            request = RevokePreparedExternalSelfDeliveryPolicyRequestV1.model_validate_json(
                self.canonical_request_bytes
            )
        if request.canonical_bytes() != self.canonical_request_bytes:
            raise ValueError("operator request bytes must be canonical")
        if hashlib.sha256(self.canonical_request_bytes).hexdigest() != self.request_sha256:
            raise ValueError("operator request SHA256 mismatch")
        for name in ("operation", "tenant_id", "database_id", "policy_id", "command_id"):
            if getattr(request, name) != getattr(self, name):
                raise ValueError(f"operator authorization request {name} mismatch")
        return self


class SignedOperatorPolicyAuthorizationV1(CliCustodyDTO):
    """Unverified signature over payload.canonical_bytes(), excluding this wrapper.

    Raw Ed25519 signature bytes use the inherited base64 JSON encoding. Public
    verification keys are intentionally absent: the owner independently resolves
    operator_key_id within the authenticated tenant/database binding and verifies
    its permission to authorize this operation, not merely a valid signature.
    """

    schema_id: Literal["chiplog.deployment-trust.signed-operator-policy-authorization.v1"] = (
        "chiplog.deployment-trust.signed-operator-policy-authorization.v1"
    )
    payload: OperatorPolicyAuthorizationPayloadV1
    signature: bytes = Field(min_length=64, max_length=64)


def operator_policy_source_content_head(canonical_source_bytes: bytes) -> str:
    """Return the immutable content revision of a complete canonical signed wrapper.

    This domain-separated digest is not a physical journal head or evidence of
    signature validity, authenticated retention, or current lineage.
    """
    return hashlib.sha256(
        b"chiplog.deployment-trust.operator-policy-source-content-head.v1\x00"
        + canonical_source_bytes
    ).hexdigest()


class RetainedOperatorPolicyAuthorizationSourceV1(CliCustodyDTO):
    """Retained source preimage, not evidence of authenticated storage/selection.

    ref.identity is payload.source_id; ref.fingerprint is SHA256 of the complete
    canonical signed wrapper. ref.head is its domain-separated immutable content
    revision, not a physical journal head. The owner must independently authenticate
    the containing physical policy decision and current lineage, and compare ref
    to the command's authenticated_operator_source. Source and request bytes remain
    immutable; their content head can be computed before the atomic policy decision.
    """

    ref: ExactHead
    canonical_source_bytes: bytes = Field(min_length=1)

    @model_validator(mode="after")
    def exact_source(self) -> Self:
        source = SignedOperatorPolicyAuthorizationV1.model_validate_json(
            self.canonical_source_bytes
        )
        if source.canonical_bytes() != self.canonical_source_bytes:
            raise ValueError("operator source bytes must be canonical")
        if self.ref.identity != source.payload.source_id:
            raise ValueError("operator source identity mismatch")
        if hashlib.sha256(self.canonical_source_bytes).hexdigest() != self.ref.fingerprint:
            raise ValueError("operator source fingerprint mismatch")
        if operator_policy_source_content_head(self.canonical_source_bytes) != self.ref.head:
            raise ValueError("operator source content head mismatch")
        return self
