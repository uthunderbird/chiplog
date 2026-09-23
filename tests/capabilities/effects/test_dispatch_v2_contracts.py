"""Public consumer shape checks; these establish no authentication or SEND right."""

import hashlib
import json

import pytest
from pydantic import ValidationError

from chiplog.capabilities.effects.contracts import ExactHead
from chiplog.capabilities.effects.dispatch_authority_contracts import DispatchSourceInventory
from chiplog.capabilities.effects.dispatch_v2_contracts import (
    DispatchAcquisitionV2,
    DispatchAdoptionV2,
    DispatchMandateV2,
    DispatchPrecursorRequestV2,
    DispatchPrecursorResultV2,
    ExternalActionIntentV2,
    InitializedCallOrigin,
    MandateHorizon,
)


def head(subject: str = "subject") -> dict[str, str]:
    return {"subject_id": subject, "head": "head", "fingerprint": "a" * 64}


def mandate_wire() -> dict[str, object]:
    value: dict[str, object] = {
        "schema_id": "chiplog.effects.dispatch-mandate.v2",
        "mandate_id": "mandate",
        "tenant_id": "tenant",
        "principal_id": "principal",
        "actor_id": "principal",
        "origin": {
            "kind": "INITIALIZED_CONSEQUENTIAL_CALL",
            "original_run_id": "original-run",
            "original_call_id": "original-call",
            "initialization_run_head": head("original-run"),
            "initialized_head": head("original-call"),
            "tool_name": "effects.emit_adopted_hermetic_v2",
            "tool_schema": "tool.v2",
            "tool_policy": head(),
            "sealed_arguments": b"\xff\x00",
        },
        "channel_class": "hermetic",
        "recipient": {
            "provider": "hermetic-effects",
            "account": "account",
            "recipient": "principal",
            "endpoint": head(),
            "canonical_address": b"hermetic://self",
            "credential_binding": head(),
        },
        "payload": b"\xff\x00payload",
        "effect_fingerprint": "b" * 64,
        "bundle_members": (head(),),
        "idempotency_fence_key": "one-effect",
        "horizon": {
            "clock_contract": "clock.v1",
            "clock_epoch": "epoch",
            "not_before_ns": 1,
            "expires_at_ns": 20,
            "continuity_policy": head(),
        },
        "semantics": {
            "normative_manifest": "manifest.v2",
            "reducer_version": "reducer.v2",
            "transition_registry_version": "transitions.v2",
            "canonicalization_fingerprint_version": "canonical.v2",
            "adapter_contract_version": "adapter.v2",
        },
    }
    for name in (
        "operation_profile",
        "planning_revision",
        "preexisting_authority_basis",
        "normative_conflict_generation",
        "consequence_scope",
        "communication_mandate",
        "disclosure_projection",
        "interaction_context",
    ):
        value[name] = head()
    for name in (
        "authority_sources",
        "affected_party_constraints",
        "dependencies",
        "factual_assertion_evidence",
        "verification_contradiction",
        "authority_applicability",
    ):
        value[name] = (head(),)
    return value


def test_public_mandate_retains_original_binary_bytes() -> None:
    mandate = DispatchMandateV2.model_validate(mandate_wire())
    restored = DispatchMandateV2.model_validate_json(mandate.canonical_bytes())
    assert restored == mandate
    assert isinstance(restored.origin, InitializedCallOrigin)
    assert restored.origin.sealed_arguments == b"\xff\x00"
    assert restored.payload == b"\xff\x00payload"


@pytest.mark.parametrize("field", tuple(DispatchMandateV2.model_fields))
def test_every_mandate_field_is_required(field: str) -> None:
    value = mandate_wire()
    del value[field]
    with pytest.raises(ValidationError):
        DispatchMandateV2.model_validate(value)


@pytest.mark.parametrize("field", ["grant", "send_authorized", "current_authority"])
def test_caller_cannot_add_permission_or_renewal(field: str) -> None:
    value = mandate_wire()
    value[field] = True
    with pytest.raises(ValidationError):
        DispatchMandateV2.model_validate(value)


def test_unknown_origin_and_missing_initialized_head_reject() -> None:
    value = DispatchMandateV2.model_validate(mandate_wire()).model_dump(mode="json")
    del value["origin"]["initialized_head"]
    with pytest.raises(ValidationError):
        DispatchMandateV2.model_validate_json(json.dumps(value))
    value["origin"]["kind"] = "ANY_FUTURE_CALL"
    with pytest.raises(ValidationError):
        DispatchMandateV2.model_validate_json(json.dumps(value))


def test_adoption_preserves_display_mandate_and_ingress_bytes() -> None:
    adoption = DispatchAdoptionV2(
        schema_id="chiplog.effects.dispatch-adoption.v2",
        adoption_act_id="act",
        display=ExactHead.model_validate(head()),
        display_bytes=b"\xffdisplay",
        mandate_bytes=DispatchMandateV2.model_validate(mandate_wire()).canonical_bytes(),
        ingress=ExactHead.model_validate(head()),
        ingress_bytes=b"\x00ingress",
    )
    assert DispatchAdoptionV2.model_validate_json(adoption.canonical_bytes()) == adoption


def test_acquisition_and_horizon_are_required_separate_values() -> None:
    assert ExternalActionIntentV2.model_fields["acquisition"].is_required()
    assert "valid_until_ns" not in DispatchMandateV2.model_fields
    assert set(MandateHorizon.model_fields) == {
        "clock_contract",
        "clock_epoch",
        "not_before_ns",
        "expires_at_ns",
        "continuity_policy",
    }


def test_complete_intent_has_acyclic_digest_graph() -> None:
    # Each object is constructed only from earlier objects. None needs a placeholder
    # for its own fingerprint or any future acquisition/publication output.
    value = mandate_wire()
    value["effect_fingerprint"] = hashlib.sha256(b"\xff\x00payload").hexdigest()
    mandate = DispatchMandateV2.model_validate(value)
    mandate_digest = hashlib.sha256(mandate.canonical_bytes()).hexdigest()
    unavailable = {"kind": "UNAVAILABLE", "reason": "UNIMPLEMENTED", "detail": "shape only"}
    request = DispatchPrecursorRequestV2.model_validate(
        {
            "schema_id": "chiplog.effects.dispatch-precursor-request.v2",
            "request_id": "precursor",
            "mandate_digest": mandate_digest,
            "interpretation_policy": head(),
            "preexisting_source_heads": (head(),),
        }
    )
    result = DispatchPrecursorResultV2.model_validate(
        {
            "schema_id": "chiplog.effects.dispatch-precursor-result.v2",
            "request_digest": hashlib.sha256(request.canonical_bytes()).hexdigest(),
            "mandate_digest": mandate_digest,
            "interpretation_policy": head(),
            "evaluation_evidence": (head(),),
        }
    )
    display = b"Adopt exact mandate SHA256=" + mandate_digest.encode()
    display_head = {**head(), "fingerprint": hashlib.sha256(display).hexdigest()}
    adoption = DispatchAdoptionV2.model_validate(
        {
            "schema_id": "chiplog.effects.dispatch-adoption.v2",
            "adoption_act_id": "exact-act",
            "display": display_head,
            "display_bytes": display,
            "mandate_bytes": mandate.canonical_bytes(),
            "ingress": head(),
            "ingress_bytes": b"accept exact-act",
        }
    )
    acquisition = DispatchAcquisitionV2(
        schema_id="chiplog.effects.dispatch-acquisition.v2",
        adoption=adoption,
        authenticated_invocation=b"retained authenticated precursor IPC frames",
        precursor_request=request,
        precursor_result=result,
        original_sources=DispatchSourceInventory.model_validate(
            {name: unavailable for name in DispatchSourceInventory.model_fields}
        ),
    )
    body = {
        "schema_id": "chiplog.effects.external-action-intent.v2",
        "intent_id": "intent",
        "mandate": mandate.model_dump(mode="json"),
        "acquisition": acquisition.model_dump(mode="json"),
    }
    preimage = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    fingerprint = hashlib.sha256(b"chiplog.effects.intent.v2\x00" + preimage).hexdigest()
    intent = ExternalActionIntentV2.model_validate_json(
        json.dumps({**body, "fingerprint": fingerprint})
    )
    restored = ExternalActionIntentV2.model_validate_json(intent.canonical_bytes())
    assert restored == intent
    assert restored.acquisition.adoption.mandate_bytes == restored.mandate.canonical_bytes()
    assert (
        restored.acquisition.precursor_result.request_digest
        == hashlib.sha256(restored.acquisition.precursor_request.canonical_bytes()).hexdigest()
    )
    assert restored.acquisition.precursor_result.mandate_digest == mandate_digest
    assert (
        restored.mandate.effect_fingerprint == hashlib.sha256(restored.mandate.payload).hexdigest()
    )
    assert isinstance(restored.mandate.origin, InitializedCallOrigin)
    assert (
        restored.mandate.origin.original_call_id
        == restored.mandate.origin.initialized_head.subject_id
    )
    restored_body = restored.model_dump(mode="json", exclude={"fingerprint"})
    restored_preimage = json.dumps(
        restored_body, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode()
    assert (
        restored.fingerprint
        == hashlib.sha256(b"chiplog.effects.intent.v2\x00" + restored_preimage).hexdigest()
    )


def test_precursor_result_cannot_embed_resulting_publication() -> None:
    with pytest.raises(ValidationError):
        DispatchPrecursorResultV2.model_validate(
            {
                "schema_id": "chiplog.effects.dispatch-precursor-result.v2",
                "request_digest": "a" * 64,
                "mandate_digest": "b" * 64,
                "interpretation_policy": head(),
                "evaluation_evidence": (head(),),
                "external_action_intent": b"future publication",
            }
        )
