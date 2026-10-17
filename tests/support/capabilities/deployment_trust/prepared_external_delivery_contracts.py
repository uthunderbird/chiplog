"""Shared builders extracted without changing their behavior."""

import hashlib

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead, ProviderRecipient
from chiplog.capabilities.deployment_trust.prepared_external_delivery_contracts import (
    BoundedExternalSelfSendMandateV1,
    ExternalDeliveryResourcesV1,
    PreparedExternalDeliveryGrantV1,
    SelectedExternalDeliverySourceV1,
)


def head(identity: str, body: bytes = b"inert") -> ExactHead:
    return ExactHead(
        identity=identity,
        head=identity,
        fingerprint=hashlib.sha256(body).hexdigest(),
    )


def grant() -> PreparedExternalDeliveryGrantV1:
    return PreparedExternalDeliveryGrantV1(
        issuer="deployment_trust",
        tenant_id="tenant",
        database_id="database",
        grant_id="grant-0",
        revision=0,
        predecessor=None,
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
            communication_authority=head("communication-authority"),
            disclosure_authority=head("disclosure-authority"),
            self_recipient_binding=head("self-recipient-binding"),
            original_run=head("original-run"),
            captured_attempt=head("captured-attempt"),
            preparation_basis=head("preparation-basis"),
            delivery_id="delivery",
            payload_digest=hashlib.sha256(b"prepared payload").hexdigest(),
            payload_byte_length=len(b"prepared payload"),
            max_calls=1,
            clock_contract="clock-contract",
            clock_epoch="epoch-7",
            not_before_ns=100,
            expires_at_ns=200,
        ),
    )
