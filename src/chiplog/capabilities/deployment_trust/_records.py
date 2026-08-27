"""Closed durable discriminator vocabulary owned by deployment trust."""

RECORD_TYPE_IDS = (
    "chiplog.deployment_trust.channel_authentication_binding",
    "chiplog.deployment_trust.current_database_state",
    "chiplog.deployment_trust.database_genesis",
    "chiplog.deployment_trust.deployment_tenant_binding",
    "chiplog.deployment_trust.evidence_authentication_binding",
    "chiplog.deployment_trust.evidence_source_head",
    "chiplog.deployment_trust.identity_credential_head",
    "chiplog.deployment_trust.poll_cursor_advance_authorized",
    "chiplog.deployment_trust.poll_cursor_applied",
    "chiplog.deployment_trust.poll_member_disposition",
    "chiplog.deployment_trust.poll_response_page_manifest",
    "chiplog.deployment_trust.principal_contour_prerequisite",
    "chiplog.deployment_trust.principal_registry_entry",
    "chiplog.deployment_trust.session_head",
    "chiplog.deployment_trust.tenant_decision",
    "chiplog.deployment_trust.tenant_principal_contour",
    "chiplog.deployment_trust.tenant_registry_entry",
    "chiplog.deployment_trust.transport_origin_witness",
    "chiplog.deployment_trust.trust_transition",
)

SCHEMA_ID = "chiplog.deployment_trust.record.v1"

KIND_RECORD_TYPES: dict[str, tuple[str, ...]] = {
    "INITIALIZE": (
        "chiplog.deployment_trust.current_database_state",
        "chiplog.deployment_trust.database_genesis",
        "chiplog.deployment_trust.deployment_tenant_binding",
        "chiplog.deployment_trust.tenant_registry_entry",
    ),
    "BOOTSTRAP": (
        "chiplog.deployment_trust.identity_credential_head",
        "chiplog.deployment_trust.principal_registry_entry",
        "chiplog.deployment_trust.session_head",
        "chiplog.deployment_trust.tenant_principal_contour",
    ),
    "REGISTER_EVIDENCE_SOURCE": (
        "chiplog.deployment_trust.evidence_authentication_binding",
        "chiplog.deployment_trust.evidence_source_head",
    ),
    "ROTATE_CREDENTIAL": (
        "chiplog.deployment_trust.identity_credential_head",
        "chiplog.deployment_trust.session_head",
    ),
    "REVOKE_CREDENTIAL": (
        "chiplog.deployment_trust.identity_credential_head",
        "chiplog.deployment_trust.session_head",
    ),
    "EMERGENCY_RECOVERY": (
        "chiplog.deployment_trust.identity_credential_head",
        "chiplog.deployment_trust.session_head",
    ),
    "TRANSPORT_WITNESS": (
        "chiplog.deployment_trust.channel_authentication_binding",
        "chiplog.deployment_trust.transport_origin_witness",
    ),
    "POLL_CURSOR_ADVANCE_AUTHORIZED": ("chiplog.deployment_trust.poll_cursor_advance_authorized",),
    "POLL_CURSOR_APPLIED": ("chiplog.deployment_trust.poll_cursor_applied",),
    "POLL_RESPONSE_PAGE": (
        "chiplog.deployment_trust.poll_member_disposition",
        "chiplog.deployment_trust.poll_response_page_manifest",
    ),
    "PRINCIPAL_CONTOUR_PREREQUISITE": ("chiplog.deployment_trust.principal_contour_prerequisite",),
    "TRUST_TRANSITION_PREPARED": ("chiplog.deployment_trust.trust_transition",),
    "TRUST_TRANSITION_READY": ("chiplog.deployment_trust.trust_transition",),
    "TRUST_TRANSITION_ABORTED": ("chiplog.deployment_trust.trust_transition",),
    "TRUST_TRANSITION_ACCEPTED": (
        "chiplog.deployment_trust.deployment_tenant_binding",
        "chiplog.deployment_trust.trust_transition",
    ),
    "OPERATOR_BINDING_KEY_ROTATION": (
        "chiplog.deployment_trust.deployment_tenant_binding",
        "chiplog.deployment_trust.trust_transition",
    ),
    "JOURNAL_ROOT_ROTATION": (
        "chiplog.deployment_trust.deployment_tenant_binding",
        "chiplog.deployment_trust.trust_transition",
    ),
    "AUTHENTICATED_LATE_EVIDENCE": ("chiplog.deployment_trust.evidence_authentication_binding",),
}
