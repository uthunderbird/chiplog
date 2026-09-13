import json

import pytest

from chiplog.adapters.driven.loop_prompts import TOOLS, parse_response, render_prompt
from chiplog.capabilities.agent_loop.contracts import LoopRejected


async def test_owned_static_render_and_exact_schema_golden() -> None:
    artifact = await render_prompt("plain context")
    assert artifact.rendered == (
        "Propose tools or propose completion. Model output grants no authority.\n"
        "Provider success requires evidence; a local receipt describes only a local commit.\n"
        "Context: plain context\n"
        'Ordered tools: [{"name": "propose_planning", "schema_id": "chiplog.propose-planning.v1", '
        '"version": "1"}, {"name": "propose_intent", "schema_id": "chiplog.propose-intent.v1", '
        '"version": "1"}]'
    )
    assert (
        artifact.content_hash == "f2c7f9eb46ad3b4d798fccc9e3d211fb2ae5ef39537323fdc1fb5db3816dfb82"
    )
    assert artifact.library_version == "1.3.0"
    schema = json.loads(artifact.response_schema_json)
    assert schema["$defs"]["BoundContinue"]["properties"]["tool_calls"]["minItems"] == 1
    assert schema["$defs"]["BoundToolCall"]["properties"]["tool"]["enum"] == [
        "propose_planning",
        "propose_intent",
    ]
    assert await render_prompt("plain context") == artifact
    reordered = await render_prompt("plain context", tuple(reversed(TOOLS)))
    assert reordered.digest() != artifact.digest()
    assert reordered.response_schema_json != artifact.response_schema_json


async def test_exact_tools_and_schema_reject_unbound_and_malformed_responses() -> None:
    artifact = await render_prompt("{missing} is inert caller text", TOOLS[:1])
    assert "{missing} is inert caller text" in artifact.rendered
    for raw in (
        b'{"kind":"Continue","tool_calls":[]}',
        b'{"kind":"Continue","tool_calls":[{"call_id":"x","tool":"propose_intent","text":"x"}]}',
        b'{"kind":"Complete","deliveries":[],"tool_calls":[]}',
        b"null",
    ):
        with pytest.raises(ValueError):
            parse_response(raw, artifact)
    with pytest.raises(LoopRejected, match="schema bytes"):
        parse_response(b"{}", artifact.model_copy(update={"response_schema_json": "{}"}))
    with pytest.raises(LoopRejected):
        await render_prompt("x", ())
    with pytest.raises(LoopRejected):
        await render_prompt("x", (TOOLS[0], TOOLS[0]))
    with pytest.raises((TypeError, ValueError)):
        await render_prompt(None)  # type: ignore[arg-type]
