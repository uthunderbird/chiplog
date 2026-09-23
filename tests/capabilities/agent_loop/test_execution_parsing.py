"""Actual new generator interpretation and legacy schema separation."""

import json

import pytest

from chiplog.capabilities.agent_loop.contracts import LoopRejected, PromptArtifact, ToolCall
from chiplog.capabilities.agent_loop.execution_contracts import (
    ConsequentialToolCall,
    ExecutionContinue,
    ExecutionPromptArtifact,
)
from chiplog.capabilities.agent_loop.execution_parsing import (
    EXECUTION_TOOLS,
    execution_response_schema,
    parse_execution_response,
)
from chiplog.capabilities.agent_loop.response_parsing import TOOLS, parse_response, response_adapter


def artifact() -> ExecutionPromptArtifact:
    return ExecutionPromptArtifact(
        content_hash="a" * 64,
        library_version="test",
        tools=EXECUTION_TOOLS,
        response_schema_json=execution_response_schema(),
        rendered="exact registered schema, no execution authority",
    )


def wire() -> dict[str, object]:
    return {
        "kind": "Continue",
        "tool_calls": [
            {
                "call_id": "call",
                "tool": "request_self_effect",
                "arguments": {"payload": "AP8=", "bundle_members": ["first", "second"]},
            }
        ],
    }


def test_registered_executable_parser_preserves_exact_binary_request() -> None:
    parsed = parse_execution_response(json.dumps(wire()).encode(), artifact())
    assert isinstance(parsed, ExecutionContinue)
    call = parsed.tool_calls[0]
    assert isinstance(call, ConsequentialToolCall)
    assert call.arguments.payload == b"\x00\xff"
    assert call.arguments.bundle_members == ("first", "second")


@pytest.mark.parametrize("mutation", ["omit", "duplicate", "reorder", "schema", "version"])
def test_captured_artifact_cannot_substitute_registered_tool_set(mutation: str) -> None:
    original = artifact()
    changes: dict[str, dict[str, object]] = {
        "omit": {"tools": EXECUTION_TOOLS[:-1]},
        "duplicate": {"tools": (*EXECUTION_TOOLS, EXECUTION_TOOLS[-1])},
        "reorder": {"tools": tuple(reversed(EXECUTION_TOOLS))},
        "schema": {"response_schema_json": "{}"},
        "version": {"generator_version": "chiplog.turn-schema.v1"},
    }
    changed = changes[mutation]
    with pytest.raises(ValueError):
        parse_execution_response(json.dumps(wire()).encode(), original.model_copy(update=changed))


def test_duplicate_call_and_duplicate_bundle_are_distinct_rejections() -> None:
    value = wire()
    calls = value["tool_calls"]
    assert isinstance(calls, list)
    value["tool_calls"] = [*calls, *calls]
    with pytest.raises(LoopRejected, match="call identity"):
        parse_execution_response(json.dumps(value).encode(), artifact())
    raw = json.dumps(wire()).replace('["first", "second"]', '["first", "first"]').encode()
    with pytest.raises(LoopRejected, match="bundle member"):
        parse_execution_response(raw, artifact())


@pytest.mark.parametrize("tool", ["unknown", "propose_intent"])
def test_unknown_or_relabelled_consequential_call_rejects(tool: str) -> None:
    raw = json.dumps(wire()).replace('"request_self_effect"', json.dumps(tool)).encode()
    with pytest.raises(ValueError):
        parse_execution_response(raw, artifact())


def test_legacy_registered_parser_does_not_accept_the_executable_wire() -> None:
    legacy = PromptArtifact(
        content_hash="a" * 64,
        library_version="test",
        tools=TOOLS,
        response_schema_json=json.dumps(
            response_adapter(TOOLS).json_schema(), sort_keys=True, separators=(",", ":")
        ),
        rendered="legacy",
    )
    with pytest.raises(ValueError):
        parse_response(json.dumps(wire()).encode(), legacy)


def test_proposal_tool_keeps_its_original_type_in_new_generator() -> None:
    raw = json.dumps(
        {
            "kind": "Continue",
            "tool_calls": [{"call_id": "proposal", "tool": "propose_intent", "text": "idea"}],
        }
    ).encode()
    parsed = parse_execution_response(raw, artifact())
    assert isinstance(parsed, ExecutionContinue)
    assert type(parsed.tool_calls[0]) is ToolCall
