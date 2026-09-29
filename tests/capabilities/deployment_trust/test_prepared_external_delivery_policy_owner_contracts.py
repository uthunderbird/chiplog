"""Contract tests for inert prepared-self-delivery policy owner DTOs.

A real Ed25519 signature makes the retained-source fixture representative, but
these DTO tests intentionally do not verify that signature or grant authority.
"""

import hashlib
from typing import Literal

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import TypeAdapter, ValidationError

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead, ProviderRecipient
from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
    HermeticTrustObservationV1,
)
from chiplog.capabilities.deployment_trust.operator_policy_authorization_contracts import (
    OperatorPolicyAuthorizationPayloadV1,
    RetainedOperatorPolicyAuthorizationSourceV1,
    SignedOperatorPolicyAuthorizationV1,
    operator_policy_source_content_head,
)
from chiplog.capabilities.deployment_trust.prepared_external_delivery_policy_contracts import (
    IssuePreparedExternalSelfDeliveryPolicyRequestV1,
    PreparedExternalSelfDeliveryPolicyAnchorV1,
    PreparedExternalSelfDeliveryPolicyTermsV1,
    PreparedExternalSelfDeliveryPolicyV1,
    RevokePreparedExternalSelfDeliveryPolicyRequestV1,
)
from chiplog.capabilities.deployment_trust.prepared_external_delivery_policy_owner_contracts import (  # noqa: E501
    AuthorizePreparedSelfDeliveryPolicyCallV1,
    PreparedSelfDeliveryPolicyProposalV1,
    PreparedSelfDeliveryPolicyRejectedV1,
    PreparedSelfDeliveryPolicyResultV1,
    prepared_self_delivery_policy_content_head,
    prepared_self_delivery_policy_request_content_head,
)


def head(identity: str, body: bytes = b"test") -> ExactHead:
    return ExactHead(
        identity=identity,
        head=f"{identity}:head",
        fingerprint=hashlib.sha256(body).hexdigest(),
    )


def observation(name: str = "trust-journal") -> HermeticTrustObservationV1:
    return HermeticTrustObservationV1(
        physical_journal_head=head(name), logical_snapshot_head="trust-snapshot"
    )


def terms(payload: bytes = b"prepared payload") -> PreparedExternalSelfDeliveryPolicyTermsV1:
    return PreparedExternalSelfDeliveryPolicyTermsV1(
        principal_id="principal",
        channel_id="telegram-channel",
        recipient=ProviderRecipient(
            provider_id="telegram",
            account_id="self-account",
            recipient_id="self-recipient",
            endpoint=head("endpoint"),
            canonical_address=b"telegram://self-account/self-recipient",
            credential_binding=head("credential"),
        ),
        communication_permission="PREPARED_EXTERNAL_SEND",
        disclosure_permission="EXACT_RENDERED_PAYLOAD",
        self_recipient_semantics="OPERATOR_ATTESTED_SELF",
        payload_class="NonAuthoritativeText",
        payload_digest=hashlib.sha256(payload).hexdigest(),
        payload_byte_length=len(payload),
        source_classes=("CLI", "TELEGRAM_PUSH"),
        selection_modes=("ORIGIN_EXACT", "MODEL_SELECTED_EXACT"),
        external_delivery=True,
        max_calls=1,
        clock_contract="clock-contract",
        clock_epoch="clock-epoch",
        not_before_ns=100,
        expires_at_ns=200,
    )


def issue_request() -> IssuePreparedExternalSelfDeliveryPolicyRequestV1:
    return IssuePreparedExternalSelfDeliveryPolicyRequestV1(
        operation="ISSUE_PREPARED_EXTERNAL_SELF_DELIVERY_POLICY",
        command_id="issue-command",
        tenant_id="tenant",
        database_id="database",
        policy_id="policy",
        expected_trust_observation=observation(),
        expected_policy=None,
        terms=terms(),
    )


def policy_anchor(
    policy: PreparedExternalSelfDeliveryPolicyV1,
) -> PreparedExternalSelfDeliveryPolicyAnchorV1:
    policy_bytes = policy.canonical_bytes()
    return PreparedExternalSelfDeliveryPolicyAnchorV1(
        owner_id="deployment_trust",
        decision=head("trust-decision", b"physical-decision"),
        record_ordinal=1,
        record_type_id="chiplog.deployment_trust.prepared_self_delivery_policy",
        schema_id="chiplog.deployment_trust.record.v1",
        record=head("trust-record", b"physical-record"),
        policy=ExactHead(
            identity=policy.policy_id,
            head=prepared_self_delivery_policy_content_head(policy_bytes),
            fingerprint=hashlib.sha256(policy_bytes).hexdigest(),
        ),
        revision=policy.revision,
    )


def signed_source(
    request: IssuePreparedExternalSelfDeliveryPolicyRequestV1
    | RevokePreparedExternalSelfDeliveryPolicyRequestV1,
    *,
    source_id: str = "operator-source",
) -> RetainedOperatorPolicyAuthorizationSourceV1:
    request_bytes = request.canonical_bytes()
    payload = OperatorPolicyAuthorizationPayloadV1(
        algorithm="Ed25519",
        source_id=source_id,
        operator_key_id="operator-key",
        tenant_id=request.tenant_id,
        database_id=request.database_id,
        policy_id=request.policy_id,
        command_id=request.command_id,
        operation=request.operation,
        request_sha256=hashlib.sha256(request_bytes).hexdigest(),
        canonical_request_bytes=request_bytes,
    )
    signed = SignedOperatorPolicyAuthorizationV1(
        payload=payload,
        signature=Ed25519PrivateKey.generate().sign(payload.canonical_bytes()),
    )
    source_bytes = signed.canonical_bytes()
    return RetainedOperatorPolicyAuthorizationSourceV1(
        ref=ExactHead(
            identity=source_id,
            head=operator_policy_source_content_head(source_bytes),
            fingerprint=hashlib.sha256(source_bytes).hexdigest(),
        ),
        canonical_source_bytes=source_bytes,
    )


def expected_policy(
    request: IssuePreparedExternalSelfDeliveryPolicyRequestV1
    | RevokePreparedExternalSelfDeliveryPolicyRequestV1,
    source: RetainedOperatorPolicyAuthorizationSourceV1,
    *,
    previous: PreparedExternalSelfDeliveryPolicyV1 | None = None,
) -> PreparedExternalSelfDeliveryPolicyV1:
    request_bytes = request.canonical_bytes()
    if isinstance(request, IssuePreparedExternalSelfDeliveryPolicyRequestV1):
        policy_terms = request.terms
        status: Literal["ACTIVE", "REVOKED"] = "ACTIVE"
    else:
        assert previous is not None
        policy_terms = previous.terms
        status = "REVOKED"
    return PreparedExternalSelfDeliveryPolicyV1(
        issuer="deployment_trust",
        tenant_id=request.tenant_id,
        database_id=request.database_id,
        policy_id=request.policy_id,
        revision=0 if previous is None else previous.revision + 1,
        predecessor=None if request.expected_policy is None else request.expected_policy.policy,
        status=status,
        terms=policy_terms,
        authorization_command=ExactHead(
            identity=request.command_id,
            head=prepared_self_delivery_policy_request_content_head(request_bytes),
            fingerprint=hashlib.sha256(request_bytes).hexdigest(),
        ),
        authorization_source=source.ref,
    )


def proposal(
    call: AuthorizePreparedSelfDeliveryPolicyCallV1,
    request: IssuePreparedExternalSelfDeliveryPolicyRequestV1
    | RevokePreparedExternalSelfDeliveryPolicyRequestV1,
    source: RetainedOperatorPolicyAuthorizationSourceV1,
    *,
    previous: PreparedExternalSelfDeliveryPolicyV1 | None = None,
) -> PreparedSelfDeliveryPolicyProposalV1:
    return PreparedSelfDeliveryPolicyProposalV1(
        call_sha256=hashlib.sha256(call.canonical_bytes()).hexdigest(),
        operator_source=source,
        policy=expected_policy(request, source, previous=previous),
    )


def issue_call() -> tuple[
    AuthorizePreparedSelfDeliveryPolicyCallV1,
    IssuePreparedExternalSelfDeliveryPolicyRequestV1,
    RetainedOperatorPolicyAuthorizationSourceV1,
]:
    request = issue_request()
    source = signed_source(request)
    return (
        AuthorizePreparedSelfDeliveryPolicyCallV1(
            canonical_signed_source_bytes=source.canonical_source_bytes,
            snapshot_bytes=b"authenticated broker snapshot",
            expected_trust_observation=request.expected_trust_observation,
            latest_policy_anchor=None,
            latest_policy_bytes=None,
        ),
        request,
        source,
    )


def revoke_call() -> tuple[
    AuthorizePreparedSelfDeliveryPolicyCallV1,
    RevokePreparedExternalSelfDeliveryPolicyRequestV1,
    RetainedOperatorPolicyAuthorizationSourceV1,
    PreparedExternalSelfDeliveryPolicyV1,
]:
    genesis_call, genesis_request, genesis_source = issue_call()
    previous = proposal(genesis_call, genesis_request, genesis_source).policy
    anchor = policy_anchor(previous)
    request = RevokePreparedExternalSelfDeliveryPolicyRequestV1(
        operation="REVOKE_PREPARED_EXTERNAL_SELF_DELIVERY_POLICY",
        command_id="revoke-command",
        tenant_id="tenant",
        database_id="database",
        policy_id="policy",
        expected_trust_observation=observation(),
        expected_policy=anchor,
    )
    source = signed_source(request)
    return (
        AuthorizePreparedSelfDeliveryPolicyCallV1(
            canonical_signed_source_bytes=source.canonical_source_bytes,
            snapshot_bytes=b"authenticated broker snapshot",
            expected_trust_observation=request.expected_trust_observation,
            latest_policy_anchor=anchor,
            latest_policy_bytes=previous.canonical_bytes(),
        ),
        request,
        source,
        previous,
    )


def test_canonical_issue_genesis_and_revoke_successor_pin_their_calls() -> None:
    issue, issue_request_value, issue_source = issue_call()
    issue_proposal = proposal(issue, issue_request_value, issue_source)
    issue_proposal.check_pinned_call(issue)
    assert issue_proposal.policy.revision == 0
    assert issue_proposal.policy.status == "ACTIVE"
    assert issue_proposal.policy.predecessor is None

    revoke, revoke_request_value, revoke_source, previous = revoke_call()
    revoke_proposal = proposal(revoke, revoke_request_value, revoke_source, previous=previous)
    revoke_proposal.check_pinned_call(revoke)
    assert revoke_proposal.policy.revision == 1
    assert revoke_proposal.policy.status == "REVOKED"
    assert revoke_proposal.policy.predecessor == revoke_request_value.expected_policy.policy
    assert revoke_proposal.policy.terms == previous.terms


def test_result_union_and_content_heads_are_canonical_and_deterministic() -> None:
    call, request, source = issue_call()
    candidate = proposal(call, request, source)
    rejected = PreparedSelfDeliveryPolicyRejectedV1(
        disposition="STALE", call_sha256=candidate.call_sha256, reason="stale"
    )
    adapter: TypeAdapter[PreparedSelfDeliveryPolicyResultV1] = TypeAdapter(
        PreparedSelfDeliveryPolicyResultV1
    )

    assert adapter.validate_json(candidate.canonical_bytes()) == candidate
    assert adapter.validate_json(rejected.canonical_bytes()) == rejected
    assert prepared_self_delivery_policy_content_head(candidate.policy.canonical_bytes()) == (
        prepared_self_delivery_policy_content_head(candidate.policy.canonical_bytes())
    )
    assert prepared_self_delivery_policy_request_content_head(request.canonical_bytes()) == (
        prepared_self_delivery_policy_request_content_head(request.canonical_bytes())
    )
    assert prepared_self_delivery_policy_content_head(candidate.policy.canonical_bytes()) != (
        prepared_self_delivery_policy_request_content_head(request.canonical_bytes())
    )


def test_call_rejects_noncanonical_source_and_mismatched_latest_pair() -> None:
    call, _, _ = issue_call()
    data = call.model_dump()
    data["canonical_signed_source_bytes"] += b" "
    with pytest.raises(ValidationError, match="must be canonical"):
        AuthorizePreparedSelfDeliveryPolicyCallV1.model_validate(data)

    revoke, _, _, _ = revoke_call()
    for field in ("latest_policy_anchor", "latest_policy_bytes"):
        data = revoke.model_dump()
        data[field] = None
        with pytest.raises(ValidationError, match="both be present or absent"):
            AuthorizePreparedSelfDeliveryPolicyCallV1.model_validate(data)

    data = revoke.model_dump()
    data["latest_policy_anchor"]["policy"]["fingerprint"] = "0" * 64
    with pytest.raises(ValidationError, match="differs from policy bytes"):
        AuthorizePreparedSelfDeliveryPolicyCallV1.model_validate(data)


def test_proposal_rejects_changed_physical_cas_or_trust_observation() -> None:
    call, request, source, previous = revoke_call()
    candidate = proposal(call, request, source, previous=previous)
    assert call.latest_policy_anchor is not None
    mutated_anchor = call.latest_policy_anchor.model_copy(
        update={"decision": head("other-decision", b"other-physical-decision")}
    )
    mutated_call = call.model_copy(update={"latest_policy_anchor": mutated_anchor})
    assert mutated_call.latest_policy_bytes == call.latest_policy_bytes
    assert mutated_call.latest_policy_anchor is not None
    assert mutated_call.latest_policy_anchor.policy == call.latest_policy_anchor.policy
    changed_digest = candidate.model_copy(
        update={"call_sha256": hashlib.sha256(mutated_call.canonical_bytes()).hexdigest()}
    )
    with pytest.raises(ValueError, match="full physical policy CAS"):
        changed_digest.check_pinned_call(mutated_call)

    stale_trust = call.model_copy(update={"expected_trust_observation": observation("other-trust")})
    changed_digest = candidate.model_copy(
        update={"call_sha256": hashlib.sha256(stale_trust.canonical_bytes()).hexdigest()}
    )
    with pytest.raises(ValueError, match="matching trust"):
        changed_digest.check_pinned_call(stale_trust)


def test_proposal_rejects_wrong_source_bytes_or_ref() -> None:
    call, request, source = issue_call()
    candidate = proposal(call, request, source)
    other_source = signed_source(request, source_id="other-source")
    with pytest.raises(ValueError, match="operator source differs"):
        candidate.model_copy(update={"operator_source": other_source}).check_pinned_call(call)

    wrong_ref_source = source.model_copy(update={"ref": head("operator-source", b"wrong-ref")})
    with pytest.raises(ValueError, match="operator source"):
        candidate.model_copy(update={"operator_source": wrong_ref_source}).check_pinned_call(call)


@pytest.mark.parametrize("mutation", ["digest", "terms", "status", "predecessor", "command"])
def test_proposal_rejects_wrong_digest_or_derived_policy_fields(mutation: str) -> None:
    call, request, source, previous = revoke_call()
    candidate = proposal(call, request, source, previous=previous)
    if mutation == "digest":
        changed = candidate.model_copy(update={"call_sha256": "0" * 64})
        message = "call SHA256"
    elif mutation == "terms":
        other_policy = candidate.policy.model_copy(update={"terms": terms(b"other payload")})
        changed = candidate.model_copy(
            update={"policy": other_policy}
        )
        message = "differs from signed request"
    elif mutation == "status":
        changed = candidate.model_copy(
            update={"policy": candidate.policy.model_copy(update={"status": "ACTIVE"})}
        )
        message = "differs from signed request"
    elif mutation == "predecessor":
        changed = candidate.model_copy(
            update={
                "policy": candidate.policy.model_copy(
                    update={"predecessor": head("policy", b"wrong-predecessor")}
                )
            }
        )
        message = "differs from signed request"
    else:
        changed = candidate.model_copy(
            update={
                "policy": candidate.policy.model_copy(
                    update={"authorization_command": head("revoke-command", b"wrong-command")}
                )
            }
        )
        message = "differs from signed request"

    with pytest.raises(ValueError, match=message):
        changed.check_pinned_call(call)


def test_revoke_without_latest_prior_policy_is_rejected() -> None:
    _, request, source, _ = revoke_call()
    missing_prior = AuthorizePreparedSelfDeliveryPolicyCallV1(
        canonical_signed_source_bytes=source.canonical_source_bytes,
        snapshot_bytes=b"authenticated broker snapshot",
        expected_trust_observation=request.expected_trust_observation,
        latest_policy_anchor=None,
        latest_policy_bytes=None,
    )
    candidate = PreparedSelfDeliveryPolicyProposalV1(
        call_sha256=hashlib.sha256(missing_prior.canonical_bytes()).hexdigest(),
        operator_source=source,
        policy=expected_policy(issue_request(), source),
    )

    with pytest.raises(ValueError, match="full physical policy CAS"):
        candidate.check_pinned_call(missing_prior)
