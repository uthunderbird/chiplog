"""Closed, inert H1 local preparation record contracts."""

from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from chiplog.capabilities.agent_loop.delivery_contracts import (
    AcceptedDelivery,
    OriginSelection,
    ProviderRecipient,
)
from chiplog.capabilities.agent_loop.delivery_contracts import (
    ExactHead as LoopHead,
)
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
from chiplog.capabilities.effects.contracts import ExactHead
from chiplog.capabilities.effects.h1_local_preparation_contracts import (
    H1LocalPreparedCommentaryIntentV1,
    H1SelectedScopeSourceV1,
)
from chiplog.capabilities.effects.h1_local_preparation_record_contracts import (
    H1LocalPreparationRecordIntegrityError,
    decode_h1_local_prepared_commentary_member,
    h1_local_complete_owner_commitment,
    h1_local_intent_fingerprint,
    make_h1_local_prepared_commentary_member,
)
from chiplog.capabilities.effects.lifecycle_transition_contracts import SelectedEffectsSource
from chiplog.capabilities.effects.scoped_delivery_source_contracts import (
    decode_selected_delivery_intent,
)
from chiplog.capabilities.effects.scoped_intent_contracts import PreparedDeliveryBasisV3
from chiplog.domain_primitives import PrincipalId, TenantId


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _loop_head(name: str) -> LoopHead:
    return LoopHead(identity=name, head=name + "/head", fingerprint=_sha(name.encode()))


def _effects_head(name: str) -> ExactHead:
    return ExactHead(subject_id=name, head=name + "/head", fingerprint=_sha(name.encode()))


def _scope_source() -> H1SelectedScopeSourceV1:
    resource = SelectedHermeticResourceObservationRefV1(
        signature_domain="dispatch-resources.v1",
        selected_initialization=_loop_head("initialization"),
        signed_observation_fingerprint=_sha(b"resource"),
    )
    recipient = ProviderRecipient(
        provider_id="hermetic-effects",
        account_id="hermetic-account",
        recipient_id="hermetic-principal",
        endpoint=_loop_head("endpoint"),
        canonical_address=b"hermetic://effects/hermetic-principal",
        credential_binding=_loop_head("credential"),
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
    admitted = _loop_head("admitted")
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
    scope_ref = _loop_head("scope")
    request = ReadCurrentHermeticExecutionScopeV1(
        expected_trust_observation=HermeticTrustObservationV1(
            physical_journal_head=_loop_head("trust"), logical_snapshot_head="trust-logical"
        ),
        source_anchor=anchor,
        expected_revision=0,
        admitted_authentication_ref=admitted,
        authenticated_cli_ref=TrustReference(
            TenantId("hermetic-tenant"),
            PrincipalId("hermetic-principal"),
            "CLI",
            "credential",
            "session",
            "source",
            _sha(b"trust"),
            "materialization",
            0,
            "peer",
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


def _intent() -> H1LocalPreparedCommentaryIntentV1:
    source = _scope_source()
    delivery = AcceptedDelivery(
        delivery_id="delivery",
        acceptance=_loop_head("acceptance"),
        selection=OriginSelection(
            ingress_binding=_loop_head("ingress"), recipient=source.scope.recipient
        ),
        rendered_bytes=b"commentary",
        render_digest=_sha(b"commentary"),
        manifest_digest=_sha(b"manifest"),
        visibility=(_loop_head("visibility"),),
        provenance=(_loop_head("provenance"),),
        disclosure=(_loop_head("disclosure"),),
        narrowing=(),
        policy=source.scope.disclosure_policy.ref,
    )
    unsigned = H1LocalPreparedCommentaryIntentV1(
        intent_id="h1-local-commentary:intent",
        tenant_id="hermetic-tenant",
        database_id="database",
        principal_id="hermetic-principal",
        worker_session_id="worker",
        original_run=_loop_head("run"),
        captured_attempt=_loop_head("attempt"),
        delivery=delivery,
        basis=PreparedDeliveryBasisV3(
            source_cut=_effects_head("cut"),
            acceptance=_effects_head("acceptance"),
            completion_command_bytes=b"completion",
            delivery_observation_bytes=b"observation",
            loop_proposal_bytes=b"proposal",
        ),
        scope_anchor=source.anchor,
        scope_ref=source.current_result.scope_ref,
        disclosure_policy_ref=source.scope.disclosure_policy.ref,
        source_request_fingerprint=_sha(b"request"),
        fingerprint="0" * 64,
    )
    return unsigned.model_copy(update={"fingerprint": h1_local_intent_fingerprint(unsigned)})


def test_local_member_is_deterministic_and_closed_to_external_decoder() -> None:
    member = make_h1_local_prepared_commentary_member(_intent())
    assert decode_h1_local_prepared_commentary_member(member) == _intent()
    assert h1_local_complete_owner_commitment(member) == h1_local_complete_owner_commitment(member)
    source = SelectedEffectsSource(
        owner="effects",
        subject=_effects_head(member.record_id),
        schema_id=member.schema_id,
        canonical_record_bytes=member.canonical_record_bytes,
        selected_decision=_effects_head("decision"),
        physical_record=_effects_head("physical"),
    )
    with pytest.raises(ValueError, match="invalid selected delivery intent schema"):
        decode_selected_delivery_intent(source)


def test_local_member_rejects_noncanonical_or_changed_semantic_bytes() -> None:
    member = make_h1_local_prepared_commentary_member(_intent())
    with pytest.raises(H1LocalPreparationRecordIntegrityError, match="fingerprint"):
        decode_h1_local_prepared_commentary_member(
            member.model_copy(update={"fingerprint": "0" * 64})
        )
    with pytest.raises(H1LocalPreparationRecordIntegrityError, match="semantic"):
        changed = _intent().model_copy(update={"tenant_id": "other", "fingerprint": "0" * 64})
        raw = changed.canonical_bytes()
        decode_h1_local_prepared_commentary_member(
            member.model_copy(update={"canonical_record_bytes": raw, "fingerprint": _sha(raw)})
        )


def test_selected_scope_rejects_physical_and_logical_join_tampering() -> None:
    source = _scope_source()
    with pytest.raises(ValidationError, match="physical anchor"):
        H1SelectedScopeSourceV1(
            anchor=source.anchor,
            scope=source.scope,
            selected_decision_bytes=b"forged",
            selected_record_bytes=source.selected_record_bytes,
            current_request=source.current_request,
            current_result=source.current_result,
        )
    with pytest.raises(ValidationError, match="logical join"):
        H1SelectedScopeSourceV1(
            anchor=source.anchor,
            scope=source.scope,
            selected_decision_bytes=source.selected_decision_bytes,
            selected_record_bytes=source.selected_record_bytes,
            current_request=source.current_request,
            current_result=source.current_result.model_copy(
                update={"scope_ref": _loop_head("other")}
            ),
        )
