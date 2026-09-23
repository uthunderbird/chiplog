"""Owned-static promptstrings boundary with exact artifact identity."""

from __future__ import annotations

import hashlib
import json
from importlib.metadata import version
from typing import cast

from promptstrings import PromptContext, promptstring
from pydantic import TypeAdapter

from chiplog.capabilities.agent_loop.contracts import (
    Complete,
    Continue,
    LoopRejected,
    PromptArtifact,
    ToolSpec,
)
from chiplog.capabilities.agent_loop.delivery_preparation import (
    DeliveryCompletion,
    delivery_response_adapter,
    parse_delivery_response,
)
from chiplog.capabilities.agent_loop.response_parsing import (
    TOOLS as TOOLS,
)
from chiplog.capabilities.agent_loop.response_parsing import (
    parse_response as parse_response,
)
from chiplog.capabilities.agent_loop.response_parsing import (
    response_adapter as response_adapter,
)


def _turn_prompt(context: str, tools: str) -> Continue | Complete:
    """Propose tools or propose completion. Model output grants no authority.
    Provider success requires evidence; a local receipt describes only a local commit.
    Context: {context}
    Ordered tools: {tools}"""
    return cast("Continue | Complete", None)


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


class OwnedStaticPrompts:
    async def render(self, context: str) -> PromptArtifact:
        return await render_prompt(context)

    def parse(self, raw: bytes, artifact: PromptArtifact) -> Continue | Complete:
        return parse_response(raw, artifact)


async def render_delivery_prompt(
    context: str,
    tools: tuple[ToolSpec, ...] = TOOLS,
) -> PromptArtifact:
    if type(context) is not str:
        raise LoopRejected("prompt context must be exact text")
    adapter = delivery_response_adapter(tools)

    def bound_prompt(context: str, tools: str) -> Continue | DeliveryCompletion:
        """Propose tools or typed delivery completion; output grants no authority.
        Use stable Run and Turn identities. Assertions cite exact observed query evidence.
        Free prose is UNVERIFIED MODEL COMMENTARY, never an authoritative result.
        Context: {context}
        Ordered tools: {tools}"""
        return cast("Continue | DeliveryCompletion", None)

    template = bound_prompt.__doc__ or ""
    bound_prompt.__annotations__ = {"context": str, "tools": str, "return": adapter._type}
    owned = promptstring(bound_prompt, strict=True)
    schema = TypeAdapter(owned.response_schema).json_schema()
    if schema != adapter.json_schema():
        raise LoopRejected("delivery prompt schema differs from registered owner parser")
    rendered = await owned.render(
        PromptContext(
            values={
                "context": context,
                "tools": json.dumps([tool.model_dump() for tool in tools], sort_keys=True),
            }
        )
    )
    return PromptArtifact(
        content_hash=hashlib.sha256(template.encode()).hexdigest(),
        library_version=version("promptstrings"),
        tools=tools,
        generator_version="chiplog.turn-schema.delivery.v1",
        response_schema_json=json.dumps(
            schema, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ),
        rendered=rendered,
    )


class DeliveryStaticPrompts:
    """Separate registered parser; canonical assembly explicitly selects this port."""

    async def render(self, context: str) -> PromptArtifact:
        return await render_delivery_prompt(context)

    def parse(self, raw: bytes, artifact: PromptArtifact) -> Continue | DeliveryCompletion:
        return parse_delivery_response(raw, artifact)
