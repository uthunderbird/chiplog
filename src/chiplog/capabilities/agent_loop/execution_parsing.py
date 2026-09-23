"""Closed executable response generator, separate from every legacy parser."""

import json
from functools import lru_cache

from pydantic import TypeAdapter

from .contracts import LoopRejected, ToolSpec
from .delivery_preparation import DeliveryCompletion
from .execution_contracts import (
    ConsequentialToolCall,
    ConsequentialToolSpec,
    ExecutionContinue,
    ExecutionPromptArtifact,
)

EXECUTION_TOOLS: tuple[ToolSpec | ConsequentialToolSpec, ...] = (
    ToolSpec(name="propose_planning", schema_id="chiplog.propose-planning.v1"),
    ToolSpec(name="propose_intent", schema_id="chiplog.propose-intent.v1"),
    ConsequentialToolSpec(),
)
EXECUTION_GENERATOR = "chiplog.turn-schema.execution.v2"


@lru_cache(maxsize=1)
def execution_response_adapter() -> TypeAdapter[ExecutionContinue | DeliveryCompletion]:
    return TypeAdapter(ExecutionContinue | DeliveryCompletion)


def execution_response_schema() -> str:
    return json.dumps(
        execution_response_adapter().json_schema(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def parse_execution_response(
    raw: bytes, artifact: ExecutionPromptArtifact
) -> ExecutionContinue | DeliveryCompletion:
    artifact = ExecutionPromptArtifact.model_validate_json(artifact.canonical_bytes())
    if (
        artifact.generator_version != EXECUTION_GENERATOR
        or artifact.tools != EXECUTION_TOOLS
        or artifact.response_schema_json != execution_response_schema()
    ):
        raise LoopRejected("executable response differs from the exact registered schema/tools")
    response = execution_response_adapter().validate_json(raw)
    if isinstance(response, ExecutionContinue):
        identifiers = tuple(call.call_id for call in response.tool_calls)
        if len(set(identifiers)) != len(identifiers):
            raise LoopRejected("duplicate sealed execution call identity")
        for call in response.tool_calls:
            if isinstance(call, ConsequentialToolCall):
                members = call.arguments.bundle_members
                if len(set(members)) != len(members):
                    raise LoopRejected("duplicate requested effect bundle member")
    return response
