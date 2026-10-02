"""Shared canonical selected-H1 scope fixture builder."""

from __future__ import annotations

import hashlib

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead as LoopHead
from chiplog.capabilities.agent_loop.delivery_contracts import ProviderRecipient
from chiplog.capabilities.deployment_trust import TrustReference
from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
    CurrentHermeticExecutionScopeV1,
    H1AuthenticatedCliStateV1,
    HermeticOutputPolicyV1,
    HermeticOutputScopeAnchorV1,
    HermeticOutputScopeV1,
    HermeticOutputSourceV1,
    HermeticTrustObservationV1,
    ReadCurrentHermeticExecutionScopeV1,
    SelectedHermeticResourceObservationRefV1,
)
from chiplog.capabilities.effects.h1_local_preparation_contracts import H1SelectedScopeSourceV1
from chiplog.domain_primitives import PrincipalId, TenantId


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _head(name: str) -> LoopHead:
    return LoopHead(identity=name, head=name + "/head", fingerprint=_sha(name.encode()))


def build_h1_selected_scope_source() -> H1SelectedScopeSourceV1:
    """Build one canonical inert selected scope for contract-level consumers."""
    resource = SelectedHermeticResourceObservationRefV1(
        signature_domain="dispatch-resources.v1",
        selected_initialization=_head("initialization"),
        signed_observation_fingerprint=_sha(b"resource"),
    )
    recipient = ProviderRecipient(
        provider_id="hermetic-effects",
        account_id="hermetic-account",
        recipient_id="hermetic-principal",
        endpoint=_head("endpoint"),
        canonical_address=b"hermetic://effects/hermetic-principal",
        credential_binding=_head("credential"),
    )
    policy = HermeticOutputPolicyV1(
        endpoint_ref=recipient.endpoint,
        selected_resource_observation_ref=resource,
        selection="ORIGIN_EXACT",
        ingress_class="AUTHENTICATED_R17_CLI",
        payload_class="NonAuthoritativeText",
        purpose="H1_LOCAL_COMMENTARY",
        external_delivery=False,
        attempt_ordinal=0,
        call_count=0,
    )
    policy_bytes = policy.canonical_bytes()
    admitted = _head("admitted")
    scope = HermeticOutputScopeV1(
        issuer="deployment_trust",
        source_profile="chiplog.execution.h1-cli-hermetic-source-profile.v1",
        slot="h1-cli-effects-origin",
        tenant_id="hermetic-tenant",
        database_id="database",
        scope_id="scope",
        revision=0,
        predecessor=None,
        principal_id="hermetic-principal",
        worker_session_id="worker",
        contour_head="CLI",
        admitted_authentication=admitted,
        authenticated_cli_state=H1AuthenticatedCliStateV1(
            trust_binding_digest=_sha(b"trust"),
            credential_head="credential",
            session_head="session",
        ),
        recipient=recipient,
        selected_resource_observation_ref=resource,
        disclosure_policy=HermeticOutputSourceV1(
            field_path="disclosure_policy",
            ref=LoopHead(identity="policy", head="policy/head", fingerprint=_sha(policy_bytes)),
            canonical_source_bytes=policy_bytes,
        ),
        mandate_applicability="HERMETIC_EFFECTS_ORIGIN_NO_EXTERNAL_ACTION_V1",
        mandate_profile="h1-cli-effects-origin-zero-call-v1",
        mandate_inventory_complete=True,
        ordered_mandates=(),
    )
    decision_bytes, record_bytes = b"decision", b"record"
    anchor = HermeticOutputScopeAnchorV1(
        owner_id="deployment_trust",
        decision=LoopHead(
            identity="decision", head="decision/head", fingerprint=_sha(decision_bytes)
        ),
        record_ordinal=1,
        record_type_id="chiplog.deployment_trust.hermetic_output_scope",
        schema_id="chiplog.deployment_trust.record.v1",
        record=LoopHead(identity="record", head="record/head", fingerprint=_sha(record_bytes)),
        scope_revision=0,
        predecessor=None,
        selected_resource_observation_ref=resource,
    )
    scope_ref = _head("scope")
    request = ReadCurrentHermeticExecutionScopeV1(
        expected_trust_observation=HermeticTrustObservationV1(
            physical_journal_head=_head("trust"), logical_snapshot_head="trust-logical"
        ),
        source_anchor=anchor,
        expected_revision=0,
        admitted_authentication_ref=admitted,
        authenticated_cli_ref=TrustReference(
            TenantId("hermetic-tenant"), PrincipalId("hermetic-principal"), "CLI",
            "credential", "session", "source", _sha(b"trust"), "materialization", 0, "peer",
        ),
        tenant_id="hermetic-tenant",
        database_id="database",
        scope_id="scope",
        expected_scope_ref=scope_ref,
        expected_worker_session_id="worker",
        selected_resource_observation_ref=resource,
    )
    return H1SelectedScopeSourceV1(
        anchor=anchor,
        scope=scope,
        selected_decision_bytes=decision_bytes,
        selected_record_bytes=record_bytes,
        current_request=request,
        current_result=CurrentHermeticExecutionScopeV1(
            disposition="CURRENT",
            scope_ref=scope_ref,
            source_anchor=anchor,
            selector_generation=0,
            ordered_current_source_refs=(admitted, anchor.decision, anchor.record),
        ),
    )


__all__ = ["build_h1_selected_scope_source"]
