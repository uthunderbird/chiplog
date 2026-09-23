"""The real owned prompt uses the same closed parser schema as its response port."""

import json

import pytest

from chiplog.adapters.driven.execution_prompts import ExecutionStaticPrompts
from chiplog.adapters.driven.loop_prompts import OwnedStaticPrompts
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionContinue


async def test_real_owned_artifact_and_legacy_parser_remain_separate() -> None:
    port = ExecutionStaticPrompts()
    artifact = await port.render("exact selected context")
    assert "exact selected context" in artifact.rendered
    assert artifact.generator_version == "chiplog.turn-schema.execution.v2"
    assert "request_self_effect" in artifact.response_schema_json
    raw = json.dumps(
        {
            "kind": "Continue",
            "tool_calls": [
                {
                    "call_id": "call",
                    "tool": "request_self_effect",
                    "arguments": {"payload": "aGVsbG8=", "bundle_members": ["one"]},
                }
            ],
        }
    ).encode()
    assert isinstance(port.parse(raw, artifact), ExecutionContinue)
    legacy = OwnedStaticPrompts()
    legacy_artifact = await legacy.render("exact selected context")
    assert "request_self_effect" not in legacy_artifact.response_schema_json
    with pytest.raises(ValueError):
        legacy.parse(raw, legacy_artifact)
