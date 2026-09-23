"""Pure response parsers selected only by the immutable captured prompt artifact."""

from __future__ import annotations

import json
from functools import lru_cache
from types import GenericAlias
from typing import Literal

from pydantic import Field, TypeAdapter, create_model

from .contracts import Complete, Continue, LoopRejected, PromptArtifact, ToolCall, ToolSpec
from .delivery_preparation import (
    DELIVERY_GENERATOR,
    DELIVERY_TOOLS,
    DeliveryCompletion,
    delivery_response_adapter,
    parse_delivery_response,
)

TOOLS = (
    ToolSpec(name="propose_planning", schema_id="chiplog.propose-planning.v1"),
    ToolSpec(name="propose_intent", schema_id="chiplog.propose-intent.v1"),
)


@lru_cache(maxsize=4)
def response_adapter(tools: tuple[ToolSpec, ...]) -> TypeAdapter[Continue | Complete]:
    if not tools or len({tool.name for tool in tools}) != len(tools):
        raise LoopRejected("empty or duplicate ToolSpec set")
    if any(tool not in TOOLS for tool in tools):
        raise LoopRejected("unknown ToolSpec identity/version")
    names = tuple(tool.name for tool in tools)
    bound_call = create_model("BoundToolCall", __base__=ToolCall, tool=(Literal[names], ...))
    bound_continue = create_model(
        "BoundContinue",
        __base__=Continue,
        tool_calls=(GenericAlias(tuple, (bound_call, Ellipsis)), Field(min_length=1)),
    )
    return TypeAdapter(bound_continue | Complete)


def parse_response(raw: bytes, artifact: PromptArtifact) -> Continue | Complete:
    if artifact.generator_version != "chiplog.turn-schema.v1":
        raise LoopRejected("legacy parser rejects other response generators")
    adapter = response_adapter(artifact.tools)
    if artifact.response_schema_json != json.dumps(
        adapter.json_schema(), sort_keys=True, separators=(",", ":")
    ):
        raise LoopRejected("schema bytes differ from exact versioned generator")
    response = adapter.validate_json(raw)
    if isinstance(response, Continue):
        names = {tool.name for tool in artifact.tools}
        if any(call.tool not in names for call in response.tool_calls):
            raise LoopRejected("tool absent from exact Turn schema")
        ids = [call.call_id for call in response.tool_calls]
        if len(ids) != len(set(ids)):
            raise LoopRejected("duplicate sealed call identity")
        return Continue.model_validate_json(response.canonical_bytes())
    return response


def parse_captured_response(
    raw: bytes, artifact: PromptArtifact
) -> Continue | Complete | DeliveryCompletion:
    if artifact.generator_version == "chiplog.turn-schema.v1":
        return parse_response(raw, artifact)
    if artifact.generator_version == DELIVERY_GENERATOR:
        return parse_delivery_response(raw, artifact)
    raise LoopRejected("unknown captured response generator")


def prewarm_registered_response_schemas() -> None:
    # Resolve schema-generation imports before isolated owners lose raw I/O.
    for tools in ((TOOLS[0],), (TOOLS[1],), TOOLS, tuple(reversed(TOOLS))):
        response_adapter(tools).json_schema()
    for tools in ((DELIVERY_TOOLS[0],), (DELIVERY_TOOLS[1],), DELIVERY_TOOLS):
        delivery_response_adapter(tools).json_schema()
