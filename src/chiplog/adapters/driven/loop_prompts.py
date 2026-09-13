"""Owned-static promptstrings boundary with exact artifact identity."""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from importlib.metadata import version
from types import GenericAlias
from typing import Literal, cast

from promptstrings import PromptContext, promptstring
from pydantic import Field, TypeAdapter, create_model

from chiplog.capabilities.agent_loop.contracts import (
    Complete,
    Continue,
    LoopRejected,
    PromptArtifact,
    ToolCall,
    ToolSpec,
)


def _turn_prompt(context: str, tools: str) -> Continue | Complete:
    """Propose tools or propose completion. Model output grants no authority.
    Provider success requires evidence; a local receipt describes only a local commit.
    Context: {context}
    Ordered tools: {tools}"""
    return cast("Continue | Complete", None)


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


async def render_prompt(context: str, tools: tuple[ToolSpec, ...] = TOOLS) -> PromptArtifact:
    if type(context) is not str:
        raise LoopRejected("prompt context must be exact text")
    if not tools or len({tool.name for tool in tools}) != len(tools):
        raise LoopRejected("empty or duplicate ToolSpec set")
    if any(tool not in TOOLS for tool in tools):
        raise LoopRejected("unknown ToolSpec identity/version")
    adapter = response_adapter(tools)

    # Each decoration owns a fresh function; its return annotation is the same
    # generated type used for parsing. No delegated template or ambient values.
    def bound_prompt(context: str, tools: str) -> Continue | Complete:
        return cast("Continue | Complete", None)

    bound_prompt.__doc__ = _turn_prompt.__doc__
    bound_prompt.__annotations__ = {"context": str, "tools": str, "return": adapter._type}
    owned = promptstring(bound_prompt, strict=True)
    schema = TypeAdapter(owned.response_schema).json_schema()
    if schema != adapter.json_schema():
        raise LoopRejected("promptstrings response schema differs from Turn parser")
    rendered = await owned.render(
        PromptContext(
            values={
                "context": context,
                "tools": json.dumps([tool.model_dump() for tool in tools], sort_keys=True),
            }
        )
    )
    return PromptArtifact(
        content_hash=hashlib.sha256((_turn_prompt.__doc__ or "").encode()).hexdigest(),
        library_version=version("promptstrings"),
        tools=tools,
        response_schema_json=json.dumps(schema, sort_keys=True, separators=(",", ":")),
        rendered=rendered,
    )


def parse_response(raw: bytes, artifact: PromptArtifact) -> Continue | Complete:
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


class OwnedStaticPrompts:
    async def render(self, context: str) -> PromptArtifact:
        return await render_prompt(context)

    def parse(self, raw: bytes, artifact: PromptArtifact) -> Continue | Complete:
        return parse_response(raw, artifact)
