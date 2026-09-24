"""Value checks deliberately make no issuance/currentness claim."""

import hashlib
import json

import pytest
from pydantic import TypeAdapter, ValidationError

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead, ProviderRecipient
from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
    CurrentHermeticExecutionScopeResultV1,
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
        trust_state=head("trust"),
        credential_state=head("credential"),
        session_state=head("session"),
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


def test_canonical_roundtrip_preserves_original_sources() -> None:
    value = scope()
    restored = HermeticOutputScopeV1.model_validate_json(value.canonical_bytes())
    assert restored == value
    assert restored.canonical_bytes() == value.canonical_bytes()
    assert restored.selected_resource_observation_ref == value.selected_resource_observation_ref


@pytest.mark.parametrize(
    "field",
    [
        "ordered_mandates",
        "mandate_inventory_complete",
        "source_profile",
        "selected_resource_observation_ref",
        "disclosure_policy",
        "predecessor",
    ],
)
def test_required_fields_cannot_be_inferred(field: str) -> None:
    data = scope().model_dump(mode="json")
    del data[field]
    with pytest.raises(ValidationError):
        HermeticOutputScopeV1.model_validate_json(json.dumps(data))


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_profile", "future"),
        ("tenant_id", "other"),
        ("mandate_inventory_complete", False),
        ("mandate_profile", "future"),
        ("authority", True),
        ("ordered_mandates", [head("mandate").model_dump()]),
    ],
)
def test_unknown_profile_fields_and_nonempty_closure_reject(field: str, value: object) -> None:
    data = scope().model_dump(mode="json")
    data[field] = value
    with pytest.raises(ValidationError):
        HermeticOutputScopeV1.model_validate_json(json.dumps(data))


def test_forged_source_digest_and_recipient_join_reject() -> None:
    data = scope().model_dump(mode="json")
    data["disclosure_policy"]["ref"]["fingerprint"] = "f" * 64
    with pytest.raises(ValidationError):
        HermeticOutputScopeV1.model_validate_json(json.dumps(data))
    data = scope().model_dump(mode="json")
    data["recipient"]["provider_id"] = "hermetic-local"
    with pytest.raises(ValidationError):
        HermeticOutputScopeV1.model_validate_json(json.dumps(data))


def test_constructed_claim_is_inert_not_authenticated() -> None:
    # A fabricated but internally consistent scope is a valid value, never an issuance.
    value = scope()
    assert HermeticOutputScopeV1.model_validate_json(value.canonical_bytes()) == value
    assert not hasattr(value, "is_current")


@pytest.mark.parametrize("disposition", ["STALE", "UNSUPPORTED", "DENIED"])
def test_closed_noncurrent_results(disposition: str) -> None:
    adapter: TypeAdapter[CurrentHermeticExecutionScopeResultV1] = TypeAdapter(
        CurrentHermeticExecutionScopeResultV1
    )
    result = adapter.validate_json(json.dumps({"disposition": disposition}))
    assert adapter.validate_json(adapter.dump_json(result)) == result
    with pytest.raises(ValidationError):
        adapter.validate_json(
            json.dumps({"disposition": disposition, "scope_ref": head("s").model_dump()})
        )


def test_current_requires_full_locator_and_closed_discriminant() -> None:
    adapter: TypeAdapter[CurrentHermeticExecutionScopeResultV1] = TypeAdapter(
        CurrentHermeticExecutionScopeResultV1
    )
    for invalid in ({"disposition": "CURRENT"}, {"disposition": "VALID"}):
        with pytest.raises(ValidationError):
            adapter.validate_json(json.dumps(invalid))
    data = dict(
        disposition="CURRENT",
        scope_ref=head("scope").model_dump(),
        source_anchor=dict(
            owner_id="deployment_trust",
            decision=head("journal").model_dump(),
            record_ordinal=1,
            record_type_id="chiplog.deployment_trust.hermetic_output_scope",
            schema_id="chiplog.deployment_trust.record.v1",
            record=head("record").model_dump(),
            scope_revision=0,
            predecessor=None,
            selected_resource_observation_ref=resource_ref().model_dump(),
        ),
        selector_generation=1,
        ordered_current_source_refs=[head("trust").model_dump()],
    )
    result = adapter.validate_json(json.dumps(data))
    assert adapter.validate_json(adapter.dump_json(result)) == result
    data["ordered_current_source_refs"] = [head("trust").model_dump()] * 2
    with pytest.raises(ValidationError):
        adapter.validate_json(json.dumps(data))
