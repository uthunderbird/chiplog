"""Version 3 executable-history wire contracts; these values mount no runtime.

The v3 containment chain keeps a history tool out of the frozen v2 Run schema.
Read-only execution/recovery records stay in their own owner family; this module
only carries the call shape into a selected v3 executable response.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import TYPE_CHECKING, Annotated, Literal, Protocol

from pydantic import Field, TypeAdapter, model_validator

from .call_acceptance_contracts import CallSubjectHead
from .contracts import (
    AttemptState,
    BudgetPolicy,
    DeliveryAcceptanceReference,
    DisclosureLabel,
    Frozen,
    RunState,
    SchedulerRootReference,
    ToolCall,
    ToolSpec,
    VisibilityMember,
)
from .delivery_contracts import OriginSelection
from .delivery_preparation import DeliveryCompletion
from .execution_contracts import ConsequentialToolCall, ConsequentialToolSpec
from .live_contract import LiveModelBinding
from .readonly_history_tool_contracts import ReadOnlyHistoryToolCall, ReadOnlyHistoryToolSpec
from .recovery_contracts import Digest, Identity, OriginalObligationBinding, UInt64

EXECUTION_GENERATOR_V3: Literal["chiplog.turn-schema.execution.v3"] = (
    "chiplog.turn-schema.execution.v3"
)
EXECUTION_TOOLS_V3: tuple[ToolSpec | ConsequentialToolSpec | ReadOnlyHistoryToolSpec, ...] = (
    ToolSpec(name="propose_planning", schema_id="chiplog.propose-planning.v1"),
    ToolSpec(name="propose_intent", schema_id="chiplog.propose-intent.v1"),
    ConsequentialToolSpec(),
    ReadOnlyHistoryToolSpec(),
)
V3CallSlot = Annotated[int, Field(strict=True, ge=0, le=0)]


class ExecutionContinueV3(Frozen):
    kind: Literal["Continue"]
    tool_calls: tuple[ToolCall | ConsequentialToolCall | ReadOnlyHistoryToolCall, ...] = Field(
        min_length=1
    )


type ExecutionResponseV3 = ExecutionContinueV3 | DeliveryCompletion


@lru_cache(maxsize=1)
def execution_response_adapter_v3() -> TypeAdapter[ExecutionResponseV3]:
    return TypeAdapter(ExecutionResponseV3)


def execution_response_schema_v3() -> str:
    """The exact response schema represented by the v3 artifact."""

    return json.dumps(
        execution_response_adapter_v3().json_schema(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


class ExecutionPromptArtifactV3(Frozen):
    prompt_id: Literal["chiplog.agent-loop"] = "chiplog.agent-loop"
    version: Literal["3"] = "3"
    content_hash: Digest
    library_version: Identity
    generator_version: Literal["chiplog.turn-schema.execution.v3"] = EXECUTION_GENERATOR_V3
    tools: tuple[ToolSpec | ConsequentialToolSpec | ReadOnlyHistoryToolSpec, ...] = Field(
        min_length=1
    )
    response_schema_json: Identity
    rendered: Identity

    @model_validator(mode="after")
    def exact_registered_response_shape(self) -> ExecutionPromptArtifactV3:
        if (
            self.generator_version != EXECUTION_GENERATOR_V3
            or self.tools != EXECUTION_TOOLS_V3
            or self.response_schema_json != execution_response_schema_v3()
        ):
            raise ValueError("v3 artifact differs from the exact registered schema/tools")
        return self


class ExecutionVisibilityManifestV3(Frozen):
    tenant: Identity
    principal: Identity
    contour_head: Identity
    run_id: Identity
    turn_id: Identity
    call_slot: V3CallSlot = 0
    generation: UInt64
    worker_session: Identity
    members: tuple[VisibilityMember, ...]
    joined_label: DisclosureLabel
    artifact: ExecutionPromptArtifactV3


class ExecutionModelAttemptV3(Frozen):
    attempt_id: Identity
    lineage_id: Identity
    generation: UInt64
    state: AttemptState
    head: Identity
    manifest: ExecutionVisibilityManifestV3
    request: Identity
    provider_contract: Literal["hermetic-model.v1", "codex-oauth.v1"]
    recipient: Literal["hermetic-model", "openai-codex"]
    live_model: LiveModelBinding | None
    worker_session: Identity
    response_base64: str | None
    receipt: str | None
    rejection: str | None


class ExecutionTurnV3(Frozen):
    turn_id: Identity
    ordinal: int = Field(gt=0, le=2**64 - 1)
    head: Identity
    state: Literal["PREPARING", "CALL_ACTIVE", "RESPONSE_AVAILABLE", "ACCEPTED", "REJECTED"]
    accumulator: tuple[VisibilityMember, ...]
    attempts: tuple[ExecutionModelAttemptV3, ...]
    selector: UInt64
    response_seal: CallSubjectHead | None
    initialized_calls: tuple[CallSubjectHead, ...] | None


class ExecutionRunRecordV3(Frozen):
    """Schema-selected v3 Run; containment alone does not publish or replay it."""

    schema_id: Literal["chiplog.agent-loop.execution-record.v3"] = (
        "chiplog.agent-loop.execution-record.v3"
    )
    tenant: Identity
    principal: Identity
    run_id: Identity
    state: RunState
    head: Identity
    predecessor: Identity | None
    prompt: Identity
    policy: BudgetPolicy
    origin: OriginSelection
    contour_head: Identity
    policy_head: Identity
    worker_session: Identity
    root_binding: Literal["NOT_APPLICABLE"] | SchedulerRootReference
    turns: tuple[ExecutionTurnV3, ...]
    delivery_acceptance: DeliveryAcceptanceReference | None
    suspension_baseline: CallSubjectHead | None
    original_obligations: tuple[OriginalObligationBinding, ...]
    no_retry_references: tuple[CallSubjectHead, ...]
    event: Identity


class ExecutionResponseParserV3(Protocol):
    """Typed T/I port. No semantic parser is supplied by Phase C."""

    def parse_execution_response_v3(
        self, raw: bytes, artifact: ExecutionPromptArtifactV3
    ) -> ExecutionResponseV3: ...


class ExecutionRequestPreparationV3Port(Protocol):
    """Typed T/I port. It has no transition implementation in this phase."""

    async def prepare_execution_request_v3(
        self, request: PrepareExecutionRequestV3
    ) -> ExecutionTransitionResultV3: ...


if TYPE_CHECKING:
    from .execution_history_transition_contracts import (
        ExecutionTransitionResultV3,
        PrepareExecutionRequestV3,
    )
