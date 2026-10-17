"""Shared builders extracted without changing their behavior."""

import hashlib
from typing import Literal

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead, ProviderRecipient
from chiplog.capabilities.deployment_trust import prepared_external_delivery_policy_owner_contracts
from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
    HermeticTrustObservationV1,
)
from chiplog.capabilities.deployment_trust.prepared_external_delivery_contracts import (
    BoundedExternalSelfSendMandateV1,
    ExternalDeliveryResourcesV1,
    IssuePreparedExternalDeliveryGrantRequestV2,
    PreparedExternalDeliveryGrantAnchorV2,
    PreparedExternalDeliveryGrantScopeV2,
    PreparedExternalDeliveryGrantV2,
    RevokePreparedExternalDeliveryGrantRequestV2,
    SelectedExternalDeliverySourceV1,
    prepared_external_delivery_grant_content_head_v2,
)
from chiplog.capabilities.deployment_trust.prepared_external_delivery_policy_contracts import (
    PreparedExternalSelfDeliveryPolicyAnchorV1,
    PreparedExternalSelfDeliveryPolicyTermsV1,
    PreparedExternalSelfDeliveryPolicyV1,
)


def head(identity: str, body: bytes = b"inert") -> ExactHead:
    return ExactHead(
        identity=identity,
        head=f"{identity}:head",
        fingerprint=hashlib.sha256(body).hexdigest(),
    )


def observation() -> HermeticTrustObservationV1:
    return HermeticTrustObservationV1(
        physical_journal_head=head("trust-journal"), logical_snapshot_head="trust-snapshot"
    )


def policy_terms() -> PreparedExternalSelfDeliveryPolicyTermsV1:
    payload = b"prepared payload"
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
        source_classes=("CLI",),
        selection_modes=("ORIGIN_EXACT",),
        external_delivery=True,
        max_calls=1,
        clock_contract="clock-contract",
        clock_epoch="epoch-7",
        not_before_ns=100,
        expires_at_ns=200,
    )


def policy(
    status: Literal["ACTIVE", "REVOKED"] = "ACTIVE",
) -> tuple[PreparedExternalSelfDeliveryPolicyV1, bytes]:
    value = PreparedExternalSelfDeliveryPolicyV1(
        issuer="deployment_trust",
        tenant_id="tenant",
        database_id="database",
        policy_id="policy",
        revision=0,
        predecessor=None,
        status=status,
        terms=policy_terms(),
        authorization_command=head("policy-command"),
        authorization_source=head("policy-source"),
    )
    return value, value.canonical_bytes()


def policy_anchor(
    value: PreparedExternalSelfDeliveryPolicyV1, raw: bytes
) -> PreparedExternalSelfDeliveryPolicyAnchorV1:
    return PreparedExternalSelfDeliveryPolicyAnchorV1(
        owner_id="deployment_trust",
        decision=head("policy-decision"),
        record_ordinal=0,
        record_type_id="chiplog.deployment_trust.prepared_self_delivery_policy",
        schema_id="chiplog.deployment_trust.record.v1",
        record=head("policy-record"),
        policy=ExactHead(
            identity=value.policy_id,
            head=prepared_external_delivery_policy_owner_contracts.prepared_self_delivery_policy_content_head(
                raw
            ),
            fingerprint=hashlib.sha256(raw).hexdigest(),
        ),
        revision=value.revision,
    )


def scope(policy_head: ExactHead) -> PreparedExternalDeliveryGrantScopeV2:
    payload = b"prepared payload"
    return PreparedExternalDeliveryGrantScopeV2(
        principal_id="principal",
        worker_session_id="worker-session",
        contour_head="contour",
        authenticated_credential_head="credential",
        authenticated_session_head="session",
        selected_source=SelectedExternalDeliverySourceV1(
            source_class="CLI",
            selected_initialization=head("r17-initialization"),
            selected_admission_decision=head("r17-decision"),
            selected_admission_record=head("r17-record"),
            admitted_authentication=head("r17-authentication"),
            ingress_binding=head("r17-ingress"),
        ),
        resources=ExternalDeliveryResourcesV1(
            signature_domain="dispatch-resources.v1",
            signed_observation_fingerprint=hashlib.sha256(b"r16-observation").hexdigest(),
            resource_grant=head("r16-grant"),
            recipient=ProviderRecipient(
                provider_id="telegram",
                account_id="self-account",
                recipient_id="self-recipient",
                endpoint=head("r16-endpoint"),
                canonical_address=b"telegram://self-account/self-recipient",
                credential_binding=head("r16-credential"),
            ),
            clock_epoch="epoch-7",
        ),
        mandate=BoundedExternalSelfSendMandateV1(
            purpose="PREPARED_EXTERNAL_SELF_SEND",
            external_delivery=True,
            selection="ORIGIN_EXACT",
            payload_class="NonAuthoritativeText",
            communication_authority=policy_head,
            disclosure_authority=policy_head,
            self_recipient_binding=policy_head,
            original_run=head("original-run"),
            captured_attempt=head("captured-attempt"),
            preparation_basis=head("preparation-basis"),
            delivery_id="delivery",
            payload_digest=hashlib.sha256(payload).hexdigest(),
            payload_byte_length=len(payload),
            max_calls=1,
            clock_contract="clock-contract",
            clock_epoch="epoch-7",
            not_before_ns=100,
            expires_at_ns=200,
        ),
    )


def grant(status: Literal["ACTIVE", "REVOKED"] = "ACTIVE") -> PreparedExternalDeliveryGrantV2:
    value, raw = policy()
    selected = policy_anchor(value, raw)
    return PreparedExternalDeliveryGrantV2(
        issuer="deployment_trust",
        tenant_id="tenant",
        database_id="database",
        grant_id="grant",
        revision=0,
        predecessor=None,
        status=status,
        selected_policy_anchor=selected,
        scope=scope(selected.policy),
        authorization_command=head("grant-command"),
        authorization_source=head("grant-source"),
    )


def grant_anchor(value: PreparedExternalDeliveryGrantV2) -> PreparedExternalDeliveryGrantAnchorV2:
    raw = value.canonical_bytes()
    return PreparedExternalDeliveryGrantAnchorV2(
        owner_id="deployment_trust",
        decision=head("grant-decision"),
        record_ordinal=0,
        record_type_id="chiplog.deployment_trust.prepared_external_delivery_grant",
        schema_id="chiplog.deployment_trust.record.v1",
        record=head("grant-record"),
        grant=ExactHead(
            identity=value.grant_id,
            head=prepared_external_delivery_grant_content_head_v2(raw),
            fingerprint=hashlib.sha256(raw).hexdigest(),
        ),
        revision=value.revision,
    )


def issue_request() -> IssuePreparedExternalDeliveryGrantRequestV2:
    value, raw = policy()
    selected = policy_anchor(value, raw)
    return IssuePreparedExternalDeliveryGrantRequestV2(
        operation="ISSUE_PREPARED_EXTERNAL_DELIVERY_GRANT",
        command_id="issue-command",
        tenant_id="tenant",
        database_id="database",
        grant_id="grant",
        policy_id="policy",
        expected_trust_observation=observation(),
        expected_grant=None,
        selected_policy_anchor=selected,
        canonical_selected_policy_bytes=raw,
        proposed_scope=scope(selected.policy),
    )


def revoke_request() -> RevokePreparedExternalDeliveryGrantRequestV2:
    value = grant()
    return RevokePreparedExternalDeliveryGrantRequestV2(
        operation="REVOKE_PREPARED_EXTERNAL_DELIVERY_GRANT",
        command_id="revoke-command",
        tenant_id="tenant",
        database_id="database",
        grant_id="grant",
        policy_id="policy",
        expected_trust_observation=observation(),
        expected_grant_anchor=grant_anchor(value),
        expected_grant=value,
    )
