"""Public execution recovery consumers; no constructed proof is a credential."""

import hashlib
import json

import pytest
from pydantic import TypeAdapter, ValidationError
from tests.support.execution_fan_out import fixture
from tests.support.fan_out_shapes import head

from chiplog.capabilities.agent_loop import model_attempt_recovery_contracts as model
from chiplog.capabilities.agent_loop.contracts import NoExposureProof, RunRecord
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.capabilities.agent_loop.execution_transition_contracts import (
    ExecutionTransitionRequest,
)
from chiplog.capabilities.agent_loop.recovery_contracts import WorkEpochRolloverFence


def no_exposure() -> model.RegisteredModelNoExposure:
    return model.RegisteredModelNoExposure(
        registry=head("registry"),
        proof=head("proof"),
        original_run=head("run"),
        original_attempt=head("attempt"),
        lineage_id="lineage",
        selector_generation=0,
        immutable_request=head("request"),
        visibility_manifest=head("manifest"),
        provider_contract="hermetic-model.v1",
        recipient="hermetic-model",
        observed_emission_head=head("not-emitted"),
        source_schema="pre-emission-cas.v1",
        canonical_source_bytes=b"\x00\xfforiginal-source",
    )


async def test_replacement_retains_complete_run_fence_and_independent_proof() -> None:
    captured = await fixture()
    request = model.ReplaceExecutionModelAttempt(
        command_id="replace",
        run=captured.captured_run,
        selected_attempt=head("attempt"),
        expected_selector=0,
        no_exposure=no_exposure(),
        fence=captured.request.cut.fence,
    )
    adapter: TypeAdapter[model.ModelAttemptRecoveryRequest] = TypeAdapter(
        model.ModelAttemptRecoveryRequest
    )
    restored = adapter.validate_json(request.canonical_bytes())
    assert isinstance(restored, model.ReplaceExecutionModelAttempt)
    assert restored == request
    assert restored.canonical_bytes() == request.canonical_bytes()
    assert restored.no_exposure.canonical_source_bytes == b"\x00\xfforiginal-source"
    assert restored.run.canonical_bytes() == captured.captured_run.canonical_bytes()
    # Fixture state is deliberately already captured: a producer must reject it.
    # Shape preservation is not proof of absence of emitted bytes.
    with pytest.raises(ValidationError):
        TypeAdapter(ExecutionTransitionRequest).validate_json(request.canonical_bytes())
    wire = json.loads(request.canonical_bytes())
    wire["safe_to_retry"] = True
    with pytest.raises(ValidationError):
        adapter.validate_json(json.dumps(wire))
    wire.pop("safe_to_retry")
    wire.pop("no_exposure")
    with pytest.raises(ValidationError):
        adapter.validate_json(json.dumps(wire))
    result = model.PreparedModelAttemptReplacement(
        source_request_fingerprint=request.digest(),
        original_attempt=head("original"),
        superseded_attempt=head("superseded"),
        replacement_attempt=head("replacement"),
        run=captured.captured_run,
        proposal_fingerprint="b" * 64,
    )
    assert (
        TypeAdapter(model.ModelAttemptRecoveryResult).validate_json(result.canonical_bytes())
        == result
    )


def test_late_response_is_separate_binary_evidence_not_run_mutation() -> None:
    request = model.RetainLateExecutionResponse(
        command_id="late",
        original_run=head("run"),
        original_attempt=head("old-attempt"),
        original_manifest=head("original-manifest"),
        lineage_id="lineage",
        generation=0,
        receipt_token=head("token"),
        selected_custody=head("custody"),
        source_authentication=head("source"),
        raw_response=b"\xff\x00late",
        transport_receipt=b"\x80receipt",
    )
    adapter: TypeAdapter[model.ModelAttemptRecoveryRequest] = TypeAdapter(
        model.ModelAttemptRecoveryRequest
    )
    assert adapter.validate_json(request.canonical_bytes()) == request
    with pytest.raises(ValidationError):
        model.ReplaceExecutionModelAttempt.model_validate_json(request.canonical_bytes())
    with pytest.raises(ValidationError):
        TypeAdapter(ExecutionTransitionRequest).validate_json(request.canonical_bytes())
    result = model.PreparedLateExecutionResponse(
        source_request_fingerprint=request.digest(),
        record=model.LateExecutionResponseRecord(evidence_id="evidence", request=request),
        proposal_fingerprint="b" * 64,
    )
    assert (
        TypeAdapter(model.ModelAttemptRecoveryResult).validate_json(result.canonical_bytes())
        == result
    )
    assert "run" not in type(result).model_fields


@pytest.mark.parametrize(
    "field",
    [
        "registry",
        "proof",
        "original_run",
        "original_attempt",
        "lineage_id",
        "selector_generation",
        "immutable_request",
        "visibility_manifest",
        "observed_emission_head",
        "canonical_source_bytes",
    ],
)
def test_no_exposure_proof_requires_complete_identity_and_source(field: str) -> None:
    wire = json.loads(no_exposure().canonical_bytes())
    del wire[field]
    with pytest.raises(ValidationError):
        model.RegisteredModelNoExposure.model_validate_json(json.dumps(wire))


def test_legacy_proof_and_new_proof_are_not_interchangeable() -> None:
    with pytest.raises(ValidationError):
        NoExposureProof.model_validate_json(no_exposure().canonical_bytes())
    legacy = NoExposureProof(
        attempt_id="attempt", attempt_head="head", run_head="run", worker_session="worker"
    )
    with pytest.raises(ValidationError):
        model.RegisteredModelNoExposure.model_validate_json(legacy.canonical_bytes())


def test_existing_wire_schemas_remain_unchanged() -> None:
    for cls, expected in (
        (NoExposureProof, "ab11c9dc6393ad0910d672f4b72a5c24bba5f0b796e1c4d9b8e5691ebd5864dc"),
        (RunRecord, "4edeb11408dcc35f94a41efce37a0414657b0354b5237cb748cf804fae076ed2"),
        (ExecutionRunRecord, "b2ffa539bf5392ad3d0a203c91f6253e0760855d95313a3c54b7b889a2309333"),
        (
            WorkEpochRolloverFence,
            "d90891f5155546416a8f53fcaca34fc498ed1e03d06805b5888c8f7278350d62",
        ),
    ):
        raw = json.dumps(cls.model_json_schema(), sort_keys=True, separators=(",", ":")).encode()
        assert hashlib.sha256(raw).hexdigest() == expected
