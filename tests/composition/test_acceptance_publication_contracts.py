"""Consumer shape and closed interpretation; no authority or publication claims."""

import hashlib

import pytest
from pydantic import ValidationError

from chiplog.composition.r14_acceptance_contracts import (
    AcceptancePhysicalEnvelope,
    RetainedAcceptancePreparation,
    SemanticComponent,
    effects_dispatch_semantics,
    loop_dispatch_semantics,
    semantic_components,
)


def test_closed_semantic_descriptors_have_exact_fields_and_independent_domains() -> None:
    expected = (
        ("normative_manifest", "normative_manifest", "chiplog.hermetic-self-effect-policy.v1"),
        ("reducer", "reducer_version", "chiplog.effects.domain.v1"),
        (
            "transition_registry",
            "transition_registry_version",
            "chiplog.effects.application.prepare-transition.v1",
        ),
        (
            "canonicalization",
            "canonicalization_fingerprint_version",
            "chiplog.effects.sorted-json-sha256.v1",
        ),
        ("adapter_contract", "adapter_contract_version", "chiplog.hermetic-effects.v1"),
    )
    loop, effects = loop_dispatch_semantics(), effects_dispatch_semantics()
    for descriptor, (component, field, version) in zip(
        semantic_components(), expected, strict=True
    ):
        assert descriptor.model_dump() == {
            "schema_id": "chiplog.call-effects.semantic-component.v1",
            "component": component,
            "effects_field": field,
            "version": version,
        }
        ref = getattr(loop, component)
        assert ref.subject_id == f"call-effects:semantic-component:{component}:v1"
        digest = hashlib.sha256(descriptor.canonical_bytes()).hexdigest()
        assert ref.revision.head == "record:" + digest
        assert ref.revision.fingerprint == digest
        assert getattr(effects, field) == version
        with pytest.raises(ValidationError):
            SemanticComponent.model_validate({**descriptor.model_dump(), "caller_registered": True})


def test_retention_requires_both_complete_owner_requests_and_results() -> None:
    schema = RetainedAcceptancePreparation.model_json_schema()
    assert set(schema["required"]) == {
        "loop_request",
        "loop_proposal",
        "effects_command",
        "effects_proposal",
    }
    assert schema["additionalProperties"] is False
    assert "registry" not in schema["properties"]
    records = AcceptancePhysicalEnvelope.model_json_schema()["properties"]["complete_records"]
    assert records["minItems"] == records["maxItems"] == 3
