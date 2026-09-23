"""Consumer-only wire checks; these tests establish no runtime authority."""

import json

import pytest
from pydantic import ValidationError

from chiplog.composition.r14_cancellation_contracts import (
    CancelCallSubmission,
    CancellationPhysicalEnvelope,
    HermeticCancellationPolicy,
    RetainedCancellationPreparation,
)


def _submission() -> dict[str, object]:
    return {
        "act_id": "cancel-1",
        "original_call_id": "call:one",
        "initialized": {
            "subject_id": "call:one",
            "revision": {"kind": "PRESENT", "head": "record:one", "fingerprint": "a" * 64},
        },
        "current_run": {
            "subject_id": "run:one",
            "revision": {"kind": "PRESENT", "head": "loop:one", "fingerprint": "b" * 64},
        },
    }


def test_submission_roundtrip_preserves_requested_exact_heads() -> None:
    submitted = CancelCallSubmission.model_validate_json(json.dumps(_submission()))
    assert CancelCallSubmission.model_validate_json(submitted.canonical_bytes()) == submitted
    assert submitted.initialized.subject_id == submitted.original_call_id
    assert submitted.current_run.revision.head == "loop:one"


@pytest.mark.parametrize("missing", ("act_id", "original_call_id", "initialized", "current_run"))
def test_submission_requires_every_identity(missing: str) -> None:
    value = _submission()
    del value[missing]
    with pytest.raises(ValidationError):
        CancelCallSubmission.model_validate_json(json.dumps(value))


@pytest.mark.parametrize("claim", ("authorized", "cut", "cancellation_act", "owner_result"))
def test_submission_cannot_smuggle_authority_or_prepared_results(claim: str) -> None:
    with pytest.raises(ValidationError):
        CancelCallSubmission.model_validate_json(json.dumps({**_submission(), claim: True}))


def test_policy_cannot_expand_to_run_termination_or_other_principal() -> None:
    for field, value in (("permits_run_termination", True), ("principal_id", "another")):
        with pytest.raises(ValidationError):
            HermeticCancellationPolicy.model_validate({field: value})


def test_retention_requires_original_act_trust_owner_exchange_and_run_edge() -> None:
    schema = RetainedCancellationPreparation.model_json_schema()
    assert set(schema["required"]) == {
        "act",
        "trust",
        "request",
        "proposal",
        "run_predecessor",
        "run_companion",
        "expected_snapshot_fingerprint",
        "owner_request",
        "owner_response",
    }
    assert schema["additionalProperties"] is False
    members = CancellationPhysicalEnvelope.model_json_schema()["properties"]["records"]
    assert members["minItems"] == members["maxItems"] == 3
