"""Concrete owner transitions and wire routes, before broker publication wiring."""

import base64
from pathlib import Path

import pytest
from tests.support.delivery_completion import _captured, _run

from chiplog.adapters.driven.loop_prompts import (
    parse_response,
    render_prompt,
)
from chiplog.capabilities.agent_loop import domain
from chiplog.capabilities.agent_loop._delivery_process import ROUTES, dispatch
from chiplog.capabilities.agent_loop.contracts import (
    Continue,
    DeliveryAcceptanceReference,
    LoopRejected,
    ToolCall,
    ToolOutcome,
    TransitionRequest,
)
from chiplog.capabilities.agent_loop.delivery_preparation import (
    DeliveryCompletion,
    DeliveryPrepareRequest,
    DeliveryValidateRequest,
    complete_delivery,
    parse_delivery_response,
)


def test_generic_owner_rejects_legacy_completion_under_delivery_generator() -> None:
    from chiplog.capabilities.agent_loop._r14_process import dispatch as generic_dispatch

    # Original failing owner frame, captured before the parser-selection repair.
    payload = (
        Path(__file__).parent / "fixtures/delivery_generator_legacy_completion.json"
    ).read_bytes()
    frame = TransitionRequest.model_validate_json(payload)
    assert frame.previous is not None
    assert frame.previous.turns[-1].attempts[-1].manifest.artifact.generator_version == (
        "chiplog.turn-schema.delivery.v1"
    )
    assert frame.proposed.accepted_delivery_binding == "LEGACY_R13"
    output = generic_dispatch("agent_loop.validate_transition", payload)
    assert output.get("failure") == "PROTOCOL_REJECTED"


def test_legacy_run_canonical_bytes_are_unchanged() -> None:
    assert _run().digest() == "02bf3dd7a6b3d26e35d7037433dde22d3c0ed460772300ef5170cfdec38c1ec2"
    assert b"accepted_delivery_binding" not in _run().canonical_bytes()


async def test_captured_completion_has_exact_terminal_successor_and_wire_validation() -> None:
    captured, observation = await _captured()
    accepted = complete_delivery(captured, observation)
    assert accepted.state == "SUCCEEDED"
    assert accepted.predecessor == captured.head
    assert accepted.turns[-1].attempts[-1].state == "TERMINAL_ACCEPTED"
    assert accepted.turns[-1].sealed_calls == ()
    assert accepted.deliveries == ()
    assert isinstance(accepted.accepted_delivery_binding, DeliveryAcceptanceReference)
    assert accepted.accepted_text == (
        'UNVERIFIED MODEL COMMENTARY\n"hello"\nEND UNVERIFIED MODEL COMMENTARY',
    )
    domain.validate_record(captured, accepted, observation)
    prepare = DeliveryPrepareRequest(previous=captured, observation=observation)
    validate = DeliveryValidateRequest(
        previous=captured, proposed=accepted, observation=observation
    )
    for route, request in zip(ROUTES, (prepare, validate), strict=True):
        output = dispatch(route[0], request.canonical_bytes())
        assert output == {
            "payload": base64.b64encode(accepted.canonical_bytes()).decode(),
            "schema_id": "chiplog.agent-loop.record.v1",
        }
        assert "failure" in dispatch(route[0], b" " + request.canonical_bytes())
    with pytest.raises(LoopRejected, match="independent delivery"):
        domain.validate_record(captured, accepted)


async def test_both_owner_routes_reject_expanded_response_under_legacy_artifact() -> None:
    captured, observation = await _captured()
    accepted = complete_delivery(captured, observation)
    legacy, legacy_observation = await _captured(legacy=True)
    requests = (
        DeliveryPrepareRequest(previous=legacy, observation=legacy_observation),
        DeliveryValidateRequest(previous=legacy, proposed=accepted, observation=legacy_observation),
    )
    for route, request in zip(ROUTES, requests, strict=True):
        output = dispatch(route[0], request.canonical_bytes())
        assert output["failure"] == "PROTOCOL_REJECTED"
        assert "generator" in str(output["reason"])


@pytest.mark.parametrize("field", ["generator", "schema", "tools"])
async def test_owner_schema_identity_checks_are_not_only_external_parser_checks(field: str) -> None:
    captured, observation = await _captured()
    accepted = complete_delivery(captured, observation)
    turn = captured.turns[-1]
    attempt = turn.attempts[-1]
    artifact = attempt.manifest.artifact
    mutations: dict[str, dict[str, object]] = {
        "generator": {"generator_version": "chiplog.turn-schema.v1"},
        "schema": {"response_schema_json": "{}"},
        "tools": {"tools": tuple(reversed(artifact.tools))},
    }
    changed_artifact = artifact.model_copy(update=mutations[field])
    changed_attempt = attempt.model_copy(
        update={"manifest": attempt.manifest.model_copy(update={"artifact": changed_artifact})}
    )
    changed = captured.model_copy(
        update={"turns": (turn.model_copy(update={"attempts": (changed_attempt,)}),)}
    )
    for route, request in zip(
        ROUTES,
        (
            DeliveryPrepareRequest(previous=changed, observation=observation),
            DeliveryValidateRequest(previous=changed, proposed=accepted, observation=observation),
        ),
        strict=True,
    ):
        assert "failure" in dispatch(route[0], request.canonical_bytes())


async def test_prior_unsealed_response_and_nonterminal_call_block_completion() -> None:
    captured, observation = await _captured()
    accepted = complete_delivery(captured, observation)
    prior = captured.turns[0].model_copy(update={"turn_id": "previous", "ordinal": 1})
    changed = captured.model_copy(update={"turns": (prior, captured.turns[0])})
    for route, request in zip(
        ROUTES,
        (
            DeliveryPrepareRequest(previous=changed, observation=observation),
            DeliveryValidateRequest(previous=changed, proposed=accepted, observation=observation),
        ),
        strict=True,
    ):
        output = dispatch(route[0], request.canonical_bytes())
        assert output["failure"] == "PROTOCOL_REJECTED"
        assert "unsealed" in str(output["reason"])


@pytest.mark.parametrize("call_state", ["INITIALIZED", "RECOVERY_REQUIRED"])
async def test_prior_nonterminal_and_recovery_required_calls_reject(call_state: str) -> None:
    captured, observation = await _captured()
    accepted = complete_delivery(captured, observation)
    call = ToolCall(call_id="prior-call", tool="propose_intent", text="proposal")
    prior_response = Continue(kind="Continue", tool_calls=(call,))
    prior_attempt = (
        captured.turns[0]
        .attempts[0]
        .model_copy(
            update={
                "state": "TERMINAL_ACCEPTED",
                "response_base64": base64.b64encode(prior_response.canonical_bytes()).decode(),
            }
        )
    )
    outcome = ToolOutcome.model_validate({"call": call, "state": call_state})
    prior = captured.turns[0].model_copy(
        update={
            "turn_id": "previous",
            "state": "ACCEPTED",
            "attempts": (prior_attempt,),
            "sealed_calls": (outcome,),
        }
    )
    changed = captured.model_copy(update={"turns": (prior, captured.turns[0])})
    for route, request in zip(
        ROUTES,
        (
            DeliveryPrepareRequest(previous=changed, observation=observation),
            DeliveryValidateRequest(previous=changed, proposed=accepted, observation=observation),
        ),
        strict=True,
    ):
        output = dispatch(route[0], request.canonical_bytes())
        assert output["failure"] == "PROTOCOL_REJECTED"
        assert "not continuation-ready" in str(output["reason"])


async def test_embedded_reference_history_and_wrong_event_mutations_reject() -> None:
    captured, observation = await _captured()
    accepted = complete_delivery(captured, observation)
    ref = accepted.accepted_delivery_binding
    assert isinstance(ref, DeliveryAcceptanceReference)
    for updates in (
        {"accepted_text": ("unverified success",)},
        {"accepted_delivery_binding": ref.model_copy(update={"acceptance_head": "rival"})},
        {
            "accepted_delivery_binding": ref.model_copy(
                update={"proposal_canonical_base64": base64.b64encode(b"{}").decode()}
            )
        },
        {"event": "RunActive"},
    ):
        candidate = accepted.model_copy(update={**updates, "head": "pending"})
        candidate = candidate.model_copy(update={"head": "loop:" + candidate.digest()})
        with pytest.raises(LoopRejected):
            domain.validate_record(captured, candidate, observation)
    with pytest.raises(LoopRejected):
        domain.validate_record(accepted, accepted, observation)


async def test_versioned_prompt_parsers_reject_cross_schema_response() -> None:
    captured, observation = await _captured()
    artifact = captured.turns[-1].attempts[-1].manifest.artifact
    assert isinstance(
        parse_delivery_response(observation.captured_response, artifact), DeliveryCompletion
    )
    with pytest.raises(LoopRejected):
        parse_response(observation.captured_response, artifact)
    with pytest.raises(LoopRejected):
        parse_delivery_response(observation.captured_response, await render_prompt("fixture"))
