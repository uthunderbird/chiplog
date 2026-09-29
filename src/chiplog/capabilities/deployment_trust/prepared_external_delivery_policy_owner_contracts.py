"""Inert private owner calls and proposals; construction grants no authority.

The broker authenticates snapshot and physical policy selection before calling.
The owner receives an operator binding derived from the protected broker pin at
startup; it does not resolve operator keys from the snapshot.
A proposal is not durable issuance: the broker must authenticate its origin,
compare its pinned call and repeat trust/latest-policy CAS at fenced append.
"""

import hashlib
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead

from .cli_custody_contracts import CliCustodyDTO, Digest, Identity
from .hermetic_output_scope_contracts import HermeticTrustObservationV1
from .operator_policy_authorization_contracts import (
    RetainedOperatorPolicyAuthorizationSourceV1,
    SignedOperatorPolicyAuthorizationV1,
)
from .prepared_external_delivery_policy_contracts import (
    IssuePreparedExternalSelfDeliveryPolicyRequestV1,
    PreparedExternalSelfDeliveryPolicyAnchorV1,
    PreparedExternalSelfDeliveryPolicyV1,
    RevokePreparedExternalSelfDeliveryPolicyRequestV1,
)


def prepared_self_delivery_policy_content_head(canonical_policy_bytes: bytes) -> str:
    """Logical content revision, not a physical journal head or authority proof."""
    return hashlib.sha256(
        b"chiplog.deployment-trust.prepared-self-delivery-policy-content-head.v1\x00"
        + canonical_policy_bytes
    ).hexdigest()


def prepared_self_delivery_policy_request_content_head(canonical_request_bytes: bytes) -> str:
    """Hash the unwrapped request, excluding source refs to avoid circularity."""
    return hashlib.sha256(
        b"chiplog.deployment-trust.prepared-self-delivery-policy-request-content-head.v1\x00"
        + canonical_request_bytes
    ).hexdigest()


class AuthorizePreparedSelfDeliveryPolicyCallV1(CliCustodyDTO):
    """Broker-selected immutable inputs; authentication is outside this DTO.

    Expected heads inside the signature may be stale. The owner returns STALE
    for a CAS mismatch rather than treating structural validity as currentness.
    """

    schema_id: Literal["chiplog.deployment-trust.authorize-self-delivery-policy-call.v1"] = (
        "chiplog.deployment-trust.authorize-self-delivery-policy-call.v1"
    )
    canonical_signed_source_bytes: bytes = Field(min_length=1)
    snapshot_bytes: bytes = Field(min_length=1)
    expected_trust_observation: HermeticTrustObservationV1
    latest_policy_anchor: PreparedExternalSelfDeliveryPolicyAnchorV1 | None
    latest_policy_bytes: bytes | None

    @model_validator(mode="after")
    def canonical_inputs(self) -> Self:
        source = SignedOperatorPolicyAuthorizationV1.model_validate_json(
            self.canonical_signed_source_bytes
        )
        if source.canonical_bytes() != self.canonical_signed_source_bytes:
            raise ValueError("signed operator source bytes must be canonical")
        if (self.latest_policy_anchor is None) != (self.latest_policy_bytes is None):
            raise ValueError("latest policy anchor and bytes must both be present or absent")
        if self.latest_policy_anchor is not None and self.latest_policy_bytes is not None:
            policy = PreparedExternalSelfDeliveryPolicyV1.model_validate_json(
                self.latest_policy_bytes
            )
            if policy.canonical_bytes() != self.latest_policy_bytes:
                raise ValueError("latest policy bytes must be canonical")
            anchor = self.latest_policy_anchor
            if anchor.policy != ExactHead(
                identity=policy.policy_id,
                head=prepared_self_delivery_policy_content_head(self.latest_policy_bytes),
                fingerprint=hashlib.sha256(self.latest_policy_bytes).hexdigest(),
            ) or anchor.revision != policy.revision:
                raise ValueError("latest policy anchor differs from policy bytes")
        return self


class PreparedSelfDeliveryPolicyProposalV1(CliCustodyDTO):
    """Uncommitted owner proposal; consistency is not signature authentication."""

    schema_id: Literal["chiplog.deployment-trust.self-delivery-policy-result.v1"] = (
        "chiplog.deployment-trust.self-delivery-policy-result.v1"
    )
    disposition: Literal["PROPOSED"] = "PROPOSED"
    call_sha256: Digest
    operator_source: RetainedOperatorPolicyAuthorizationSourceV1
    policy: PreparedExternalSelfDeliveryPolicyV1

    def check_pinned_call(self, call: AuthorizePreparedSelfDeliveryPolicyCallV1) -> None:
        """Revalidate bytes and compare every proposed field to a broker-pinned call."""
        call = AuthorizePreparedSelfDeliveryPolicyCallV1.model_validate_json(call.canonical_bytes())
        proposal = type(self).model_validate_json(self.canonical_bytes())
        if proposal.call_sha256 != hashlib.sha256(call.canonical_bytes()).hexdigest():
            raise ValueError("proposal call SHA256 mismatch")
        if proposal.operator_source.canonical_source_bytes != call.canonical_signed_source_bytes:
            raise ValueError("proposal operator source differs from pinned call")
        source = SignedOperatorPolicyAuthorizationV1.model_validate_json(
            call.canonical_signed_source_bytes
        )
        raw_request = source.payload.canonical_request_bytes
        request: (
            IssuePreparedExternalSelfDeliveryPolicyRequestV1
            | RevokePreparedExternalSelfDeliveryPolicyRequestV1
        )
        if source.payload.operation == "ISSUE_PREPARED_EXTERNAL_SELF_DELIVERY_POLICY":
            request = IssuePreparedExternalSelfDeliveryPolicyRequestV1.model_validate_json(
                raw_request
            )
        else:
            request = RevokePreparedExternalSelfDeliveryPolicyRequestV1.model_validate_json(
                raw_request
            )
        if (
            request.expected_trust_observation != call.expected_trust_observation
            or request.expected_policy != call.latest_policy_anchor
        ):
            raise ValueError("proposal requires matching trust and full physical policy CAS")
        previous = (
            None
            if call.latest_policy_bytes is None
            else PreparedExternalSelfDeliveryPolicyV1.model_validate_json(call.latest_policy_bytes)
        )
        if previous is not None and (
            previous.tenant_id != request.tenant_id
            or previous.database_id != request.database_id
            or previous.policy_id != request.policy_id
        ):
            raise ValueError("previous policy scope differs from signed request")
        if isinstance(request, IssuePreparedExternalSelfDeliveryPolicyRequestV1):
            terms = request.terms
            status: Literal["ACTIVE", "REVOKED"] = "ACTIVE"
        else:
            if previous is None:
                raise ValueError("revocation requires a previous policy")
            terms = previous.terms
            status = "REVOKED"
        expected = PreparedExternalSelfDeliveryPolicyV1(
            issuer="deployment_trust",
            tenant_id=request.tenant_id,
            database_id=request.database_id,
            policy_id=request.policy_id,
            revision=0 if previous is None else previous.revision + 1,
            predecessor=None if request.expected_policy is None else request.expected_policy.policy,
            status=status,
            terms=terms,
            authorization_command=ExactHead(
                identity=request.command_id,
                head=prepared_self_delivery_policy_request_content_head(raw_request),
                fingerprint=hashlib.sha256(raw_request).hexdigest(),
            ),
            authorization_source=proposal.operator_source.ref,
        )
        if proposal.policy != expected:
            raise ValueError("proposal policy differs from signed request and pinned predecessor")


class PreparedSelfDeliveryPolicyRejectedV1(CliCustodyDTO):
    schema_id: Literal["chiplog.deployment-trust.self-delivery-policy-result.v1"] = (
        "chiplog.deployment-trust.self-delivery-policy-result.v1"
    )
    disposition: Literal["DENIED", "STALE"]
    call_sha256: Digest
    reason: Identity


PreparedSelfDeliveryPolicyResultV1 = Annotated[
    PreparedSelfDeliveryPolicyProposalV1 | PreparedSelfDeliveryPolicyRejectedV1,
    Field(discriminator="disposition"),
]
