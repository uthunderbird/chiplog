"""Owned prompt artifact for the separately versioned executable response schema."""

import hashlib
import json
from importlib.metadata import version
from typing import cast

from promptstrings import PromptContext, promptstring
from pydantic import TypeAdapter

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.delivery_preparation import DeliveryCompletion
from chiplog.capabilities.agent_loop.execution_contracts import (
    ExecutionContinue,
    ExecutionPromptArtifact,
)
from chiplog.capabilities.agent_loop.execution_parsing import (
    EXECUTION_TOOLS,
    execution_response_adapter,
    execution_response_schema,
    parse_execution_response,
)


async def render_execution_prompt(context: str) -> ExecutionPromptArtifact:
    if type(context) is not str:
        raise LoopRejected("execution prompt context must be exact text")

    def bound_prompt(context: str, tools: str) -> ExecutionContinue | DeliveryCompletion:
        """Propose a tool request or an evidence-bound completion.
        A request_self_effect call requests explicit adoption of its exact initialized
        call; it does not grant execution or send authority. Preserve exact payload and
        ordered bundle members. Proposal tools remain proposals. Do not claim success
        from acceptance or dispatch; use only the supplied closed result evidence.
        Context: {context}
        Ordered tools: {tools}"""
        return cast("ExecutionContinue | DeliveryCompletion", None)

    template = bound_prompt.__doc__ or ""
    adapter = execution_response_adapter()
    bound_prompt.__annotations__ = {"context": str, "tools": str, "return": adapter._type}
    owned = promptstring(bound_prompt, strict=True)
    if TypeAdapter(owned.response_schema).json_schema() != adapter.json_schema():
        raise LoopRejected("execution prompt schema differs from registered parser")
    rendered = await owned.render(
        PromptContext(
            values={
                "context": context,
                "tools": json.dumps(
                    [tool.model_dump() for tool in EXECUTION_TOOLS], sort_keys=True
                ),
            }
        )
    )
    return ExecutionPromptArtifact(
        content_hash=hashlib.sha256(template.encode()).hexdigest(),
        library_version=version("promptstrings"),
        tools=EXECUTION_TOOLS,
        response_schema_json=execution_response_schema(),
        rendered=rendered,
    )


class ExecutionStaticPrompts:
    async def render(self, context: str) -> ExecutionPromptArtifact:
        return await render_execution_prompt(context)

    def parse(
        self, raw: bytes, artifact: ExecutionPromptArtifact
    ) -> ExecutionContinue | DeliveryCompletion:
        return parse_execution_response(raw, artifact)
