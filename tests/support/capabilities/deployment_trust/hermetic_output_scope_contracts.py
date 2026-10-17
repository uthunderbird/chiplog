"""Shared builders extracted without changing their behavior."""

import hashlib

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead, ProviderRecipient
from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
    H1AuthenticatedCliStateV1,
    HermeticOutputPolicyV1,
    HermeticOutputScopeV1,
    HermeticOutputSourceV1,
    SelectedHermeticResourceObservationRefV1,
)


def head(identity: str, body: bytes = b"claimed source") -> ExactHead:
    return ExactHead(identity=identity, head=identity, fingerprint=hashlib.sha256(body).hexdigest())


def resource_ref() -> SelectedHermeticResourceObservationRefV1:
    return SelectedHermeticResourceObservationRefV1(
        signature_domain="dispatch-resources.v1",
        selected_initialization=head("selected-initialization"),
        signed_observation_fingerprint=hashlib.sha256(b"inert signed observation").hexdigest(),
    )


def scope() -> HermeticOutputScopeV1:
    endpoint_ref = head("r16-endpoint")
    policy = HermeticOutputPolicyV1(
        endpoint_ref=endpoint_ref,
        selected_resource_observation_ref=resource_ref(),
        selection="ORIGIN_EXACT",
        ingress_class="AUTHENTICATED_R17_CLI",
        payload_class="NonAuthoritativeText",
        purpose="H1_LOCAL_COMMENTARY",
        external_delivery=False,
        attempt_ordinal=0,
        call_count=0,
    )
    return HermeticOutputScopeV1(
        issuer="deployment_trust",
        source_profile="chiplog.execution.h1-cli-hermetic-source-profile.v1",
        slot="h1-cli-effects-origin",
        tenant_id="hermetic-tenant",
        database_id="database",
        scope_id="owner-scope",
        revision=0,
        predecessor=None,
        principal_id="hermetic-principal",
        worker_session_id="worker",
        contour_head="contour",
        admitted_authentication=head("auth"),
        authenticated_cli_state=H1AuthenticatedCliStateV1(
            trust_binding_digest=hashlib.sha256(b"trust-binding").hexdigest(),
            credential_head="credential:1",
            session_head="session:1",
        ),
        recipient=ProviderRecipient(
            provider_id="hermetic-effects",
            account_id="hermetic-account",
            recipient_id="hermetic-principal",
            canonical_address=b"hermetic://effects/hermetic-principal",
            endpoint=endpoint_ref,
            credential_binding=head("r16-credential"),
        ),
        selected_resource_observation_ref=resource_ref(),
        disclosure_policy=HermeticOutputSourceV1(
            field_path="disclosure_policy",
            ref=head("output-policy", policy.canonical_bytes()),
            canonical_source_bytes=policy.canonical_bytes(),
        ),
        mandate_applicability="HERMETIC_EFFECTS_ORIGIN_NO_EXTERNAL_ACTION_V1",
        mandate_profile="h1-cli-effects-origin-zero-call-v1",
        mandate_inventory_complete=True,
        ordered_mandates=(),
    )
