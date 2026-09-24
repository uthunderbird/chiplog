"""Public transport boundary; shape checks do not establish owner authenticity."""

import pytest
from pydantic import ValidationError

from chiplog.composition.r14_acceptance_contracts import AcceptancePhysicalEnvelope
from chiplog.composition.r14_acceptance_v2_contracts import (
    AcceptancePhysicalEnvelopeV2,
    RetainedAcceptancePreparationV2,
)


def test_retention_carries_full_effects_input_and_original_owner_outputs() -> None:
    schema = RetainedAcceptancePreparationV2.model_json_schema()
    assert set(schema["required"]) == {
        "loop_request",
        "loop_proposal",
        "effects_request",
        "effects_proposal",
    }
    assert schema["additionalProperties"] is False
    request = schema["$defs"]["DispatchPreparationV2"]
    assert set(request["required"]) == {"schema_id", "command", "previous", "current"}
    assert schema["properties"]["kind"]["const"] == "RETAINED_CALL_EFFECT_ACCEPTANCE_V2"


def test_new_envelope_roundtrips_without_reinterpreting_legacy() -> None:
    member = {
        "record_id": "record",
        "owner": "agent_loop",
        "schema_id": "schema",
        "canonical_payload_base64": "AP8=",
        "fingerprint": "a" * 64,
    }
    wire = {
        "kind": "CALL_EFFECT_ACCEPTANCE_ENVELOPE_V2",
        "tenant_id": "tenant",
        "original_call_id": "call",
        "expected_tenant_head": 0,
        "retained_preparation_fingerprint": "b" * 64,
        "complete_records": (member, member, member),
        "physical_batch_fingerprint": "c" * 64,
    }
    envelope = AcceptancePhysicalEnvelopeV2.model_validate(wire)
    assert AcceptancePhysicalEnvelopeV2.model_validate_json(envelope.canonical_bytes()) == envelope
    # Duplicate identity is deliberately a semantic verifier obligation, not shape proof.
    with pytest.raises(ValidationError):
        AcceptancePhysicalEnvelope.model_validate_json(envelope.canonical_bytes())
    for count in (2, 4):
        with pytest.raises(ValidationError):
            AcceptancePhysicalEnvelopeV2.model_validate(
                {**wire, "complete_records": (member,) * count}
            )
    with pytest.raises(ValidationError):
        AcceptancePhysicalEnvelopeV2.model_validate({**wire, "permission": True})
