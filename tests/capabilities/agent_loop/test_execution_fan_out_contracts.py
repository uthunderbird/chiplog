"""Public wire consumer; fixture data is untrusted, not selected runtime history."""

import json

import pytest
from pydantic import TypeAdapter, ValidationError
from tests.support.fan_out_shapes import shape_proposal as _proposal
from tests.support.fan_out_shapes import shape_request as _request

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallPreparationRejected
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.capabilities.agent_loop.execution_fan_out_contracts import (
    ExecutionCapturedFanOutProposal,
    ExecutionCapturedFanOutRequest,
    ExecutionCapturedFanOutResult,
)
from chiplog.capabilities.agent_loop.fan_out_contracts import CapturedFanOutRequest


def test_execution_fan_out_boundary_retains_complete_request_and_owner_result() -> None:
    legacy = _request()  # Reuse inert shape fixture, not a producer or private implementation.
    head = {"identity": "endpoint", "head": "head", "fingerprint": "a" * 64}
    values = legacy.captured_run.model_dump(
        mode="json", exclude={"deliveries", "accepted_text", "planning_receipts"}
    )
    values.update(
        schema_id="chiplog.agent-loop.execution-record.v2",
        origin={
            "kind": "ORIGIN_EXACT",
            "ingress_binding": head,
            "recipient": {
                "provider_id": "local-cli",
                "account_id": "account",
                "recipient_id": "principal",
                "endpoint": head,
                "canonical_address": "bG9jYWw=",
                "credential_binding": head,
            },
        },
        turns=[],
        delivery_acceptance=None,
        suspension_baseline=None,
        original_obligations=[],
        no_retry_references=[],
    )
    # A shape consumer cannot assert the Run/response/cut relationship. The future
    # owner must reject this deliberately unsealed Run before any publication.
    run = ExecutionRunRecord.model_validate_json(json.dumps(values))
    request = ExecutionCapturedFanOutRequest(
        request=legacy.request,
        captured_run=run,
        tool_registry=legacy.tool_registry,
        tool_registry_head=legacy.tool_registry_head,
    )
    restored = ExecutionCapturedFanOutRequest.model_validate_json(request.canonical_bytes())
    assert restored == request
    assert restored.request.canonical_response_base64 == legacy.request.canonical_response_base64
    assert restored.request.ordered_calls == legacy.request.ordered_calls
    with pytest.raises(ValidationError):
        CapturedFanOutRequest.model_validate_json(request.canonical_bytes())
    with pytest.raises(ValidationError):
        ExecutionCapturedFanOutRequest.model_validate_json(legacy.canonical_bytes())
    wire = json.loads(request.canonical_bytes())
    wire["publication_authorized"] = True
    with pytest.raises(ValidationError):
        ExecutionCapturedFanOutRequest.model_validate_json(json.dumps(wire))

    prepared = _proposal(legacy)
    proposal = ExecutionCapturedFanOutProposal(
        source_request_fingerprint=request.digest(),
        fan_out=prepared.fan_out,
        sealed_run=run,
        proposal_fingerprint="b" * 64,
    )
    adapter: TypeAdapter[ExecutionCapturedFanOutProposal | CallPreparationRejected] = TypeAdapter(
        ExecutionCapturedFanOutResult
    )
    assert adapter.validate_json(proposal.canonical_bytes()) == proposal
    assert proposal.sealed_run.canonical_bytes() == run.canonical_bytes()
    assert proposal.fan_out.complete_ordered_record_manifest == (
        prepared.fan_out.complete_ordered_record_manifest
    )
    with pytest.raises(ValidationError):
        adapter.validate_json(prepared.canonical_bytes())
