"""Owner preparation for the separately selected R14 resolver policy."""

from __future__ import annotations

import hashlib

from .call_acceptance_contracts import CallPreparationRejected
from .original_recovery_contracts import (
    AdoptOriginalResolverAuthorityPolicyV1,
    PreparedOriginalResolverAuthorityPolicyV1,
    original_resolver_authority_policy_v1,
)


def prepare_original_resolver_authority_policy_v1(
    request: AdoptOriginalResolverAuthorityPolicyV1,
) -> PreparedOriginalResolverAuthorityPolicyV1 | CallPreparationRejected:
    """Accept only the closed policy for this tenant; broker auth is checked at publication."""
    try:
        request = AdoptOriginalResolverAuthorityPolicyV1.model_validate_json(
            request.canonical_bytes()
        )
        expected = original_resolver_authority_policy_v1(request.policy.tenant_id)
        command_id = "adopt-original-resolver-authority:" + request.policy.tenant_id
        if request.command_id != command_id or request.policy != expected:
            raise ValueError("original resolver policy differs from the closed tenant profile")
        policy_bytes = request.policy.canonical_bytes()
        return PreparedOriginalResolverAuthorityPolicyV1(
            source_request_fingerprint=request.digest(),
            policy=request.policy,
            complete_batch_fingerprint=hashlib.sha256(policy_bytes).hexdigest(),
        )
    except (TypeError, ValueError) as error:
        return CallPreparationRejected(
            command_id=request.command_id, code="HOLD", reason=str(error)
        )
