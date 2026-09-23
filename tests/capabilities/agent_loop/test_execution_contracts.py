"""Public executable wire consumer; no claim of runtime registration or authority."""

import base64
import json

import pytest
from pydantic import ValidationError

from chiplog.capabilities.agent_loop.contracts import AttemptState, Continue, RunRecord, ToolSpec
from chiplog.capabilities.agent_loop.execution_contracts import (
    ConsequentialToolCall,
    ConsequentialToolSpec,
    ExecutionContinue,
    ExecutionPromptArtifact,
    ExecutionRunRecord,
    ExecutionTurn,
    SelfEffectArguments,
)
from chiplog.capabilities.agent_loop.recovery_contracts import IndividualSubject


def test_consequential_request_is_distinct_and_preserves_binary_payload() -> None:
    call = ConsequentialToolCall(
        call_id="original-model-label",
        tool="request_self_effect",
        arguments=SelfEffectArguments(payload=b"\xff\x00payload", bundle_members=("one",)),
    )
    response = ExecutionContinue(kind="Continue", tool_calls=(call,))
    restored = ExecutionContinue.model_validate_json(response.canonical_bytes())
    assert restored == response
    assert isinstance(restored.tool_calls[0], ConsequentialToolCall)
    assert restored.tool_calls[0].arguments.payload == b"\xff\x00payload"
    with pytest.raises(ValidationError):
        Continue.model_validate_json(response.canonical_bytes())
    with pytest.raises(ValidationError):
        ToolSpec.model_validate_json(ConsequentialToolSpec().canonical_bytes())


def test_new_generator_retains_distinct_proposal_and_consequential_identities() -> None:
    artifact = ExecutionPromptArtifact(
        content_hash="a" * 64,
        library_version="test",
        tools=(
            ToolSpec(name="propose_intent", schema_id="chiplog.propose-intent.v1"),
            ConsequentialToolSpec(),
        ),
        response_schema_json="public shape only",
        rendered="no authority",
    )
    restored = ExecutionPromptArtifact.model_validate_json(artifact.canonical_bytes())
    assert restored == artifact
    assert type(restored.tools[0]) is ToolSpec
    assert type(restored.tools[1]) is ConsequentialToolSpec


@pytest.mark.parametrize("field", ["authority", "recipient", "horizon", "send_authorized"])
def test_model_arguments_cannot_supply_adoption_authority(field: str) -> None:
    args = SelfEffectArguments(payload=b"payload", bundle_members=("one",))
    wire = json.loads(args.canonical_bytes())
    wire[field] = True
    with pytest.raises(ValidationError):
        SelfEffectArguments.model_validate_json(json.dumps(wire))


def test_sealed_zero_call_turn_differs_from_unsealed_and_has_no_embedded_result() -> None:
    preparing = ExecutionTurn(
        turn_id="turn",
        ordinal=1,
        head="head",
        state="PREPARING",
        accumulator=(),
        attempts=(),
        selector=0,
        response_seal=None,
        initialized_calls=None,
    )
    assert ExecutionTurn.model_validate_json(preparing.canonical_bytes()).initialized_calls is None
    wire = json.loads(preparing.canonical_bytes())
    wire["result"] = "pretend success"
    with pytest.raises(ValidationError):
        ExecutionTurn.model_validate_json(json.dumps(wire))
    wire.pop("result")
    wire["initialized_calls"] = []
    assert ExecutionTurn.model_validate_json(json.dumps(wire)).initialized_calls == ()


@pytest.mark.parametrize(
    "attempt_state",
    [
        "PREPARED_NOT_EMITTED",
        "EMITTED_OUTCOME_UNKNOWN",
        "RESPONSE_CAPTURED",
        "TERMINAL_REJECTED",
        "TERMINAL_ACCEPTED",
    ],
)
def test_complete_run_wire_retains_original_recovery_and_delivery_references(
    attempt_state: AttemptState,
) -> None:
    # Shape-only external consumer. No referenced authority record is asserted
    # selected, and state combinations still require the registered owner reducer.
    head = {"identity": "endpoint", "head": "head", "fingerprint": "a" * 64}
    call_head = {
        "subject_id": "original-call",
        "revision": {"kind": "PRESENT", "head": "call/head", "fingerprint": "b" * 64},
    }
    artifact = ExecutionPromptArtifact(
        content_hash="a" * 64,
        library_version="test",
        tools=(ConsequentialToolSpec(),),
        response_schema_json="shape only",
        rendered="shape only",
    )
    value = {
        "tenant": "tenant",
        "principal": "principal",
        "run_id": "current-run",
        "state": "SUSPENDED",
        "head": "run/head",
        "predecessor": "run/previous",
        "prompt": "request",
        "policy": {},
        "origin": {
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
        "contour_head": "contour",
        "policy_head": "policy",
        "worker_session": "worker",
        "root_binding": {
            "kind": "SCHEDULER_LINEAGE",
            "root_id": "stable-root",
            "subject_canonical_base64": base64.b64encode(
                IndividualSubject(occurrence_id="occurrence").canonical_bytes()
            ).decode(),
            "subject_schema_version": "chiplog.execution-lineage-subject.v1",
            "root_fingerprint": "c" * 64,
            "initial_run_id": "original-run",
        },
        "turns": [
            {
                "turn_id": "turn",
                "ordinal": 1,
                "head": "turn/head",
                "state": "CALL_ACTIVE",
                "accumulator": [],
                "selector": 0,
                "response_seal": None,
                "initialized_calls": None,
                "attempts": [
                    {
                        "attempt_id": "attempt/0",
                        "lineage_id": "stable-attempt",
                        "generation": 0,
                        "state": attempt_state,
                        "head": "attempt/head",
                        "manifest": {
                            "tenant": "tenant",
                            "principal": "principal",
                            "contour_head": "contour",
                            "run_id": "current-run",
                            "turn_id": "turn",
                            "generation": 0,
                            "worker_session": "worker",
                            "members": [],
                            "joined_label": {"value": "UNRESTRICTED", "allowed_endpoints": []},
                            "artifact": artifact.model_dump(mode="json"),
                        },
                        "request": "request",
                        "provider_contract": "hermetic-model.v1",
                        "recipient": "hermetic-model",
                        "live_model": None,
                        "worker_session": "worker",
                        "response_base64": None,
                        "receipt": None,
                        "rejection": None,
                    }
                ],
            }
        ],
        "delivery_acceptance": {
            "kind": "R17_DELIVERY_ACCEPTANCE",
            "acceptance_identity": "delivery",
            "acceptance_head": "delivery/head",
            "acceptance_fingerprint": "d" * 64,
            "proposal_canonical_base64": "e30=",
        },
        "suspension_baseline": call_head,
        "original_obligations": [
            {
                "original_run_id": "original-run",
                "original_call_id": "original-call",
                "obligation_id": "obligation",
                "obligation_stream_id": "original-stream",
                "obligation_head": "obligation/head",
                "closure_predicate_id": "closure",
                "closure_predicate_version": "1",
                "resolver_id": "resolver",
                "resolver_version": "1",
                "reducer_id": "reducer",
                "reducer_version": "1",
                "evidence_stream_id": "evidence",
                "evidence_head": {"kind": "ABSENT"},
            }
        ],
        "no_retry_references": [call_head],
        "event": "RunSuspended",
    }
    run = ExecutionRunRecord.model_validate_json(json.dumps(value))
    assert ExecutionRunRecord.model_validate_json(run.canonical_bytes()) == run
    assert run.turns[0].attempts[0].state == attempt_state
    assert run.original_obligations[0].original_run_id == "original-run"
    assert run.no_retry_references[0].subject_id == "original-call"
    assert run.delivery_acceptance is not None
    with pytest.raises(ValidationError):
        RunRecord.model_validate_json(run.canonical_bytes())
