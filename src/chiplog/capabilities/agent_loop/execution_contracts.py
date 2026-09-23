"""Versioned executable Run wire; legacy R13 schemas retain their interpretation.

Calls in these Runs reference separately selected lifecycle records. An embedded
string result or model response cannot stand in for acceptance or terminal proof.
All values are inert: the owner and broker must validate the actual history.
"""

from typing import Literal

from pydantic import ConfigDict, Field

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
from .live_contract import LiveModelBinding
from .recovery_contracts import Digest, Identity, OriginalObligationBinding, UInt64


class ConsequentialToolSpec(Frozen):
    name: Literal["request_self_effect"] = "request_self_effect"
    version: Literal["2"] = "2"
    schema_id: Literal["chiplog.request-self-effect.v2"] = "chiplog.request-self-effect.v2"


class SelfEffectArguments(Frozen):
    """Request an effect under the registered self-recipient policy, not a mandate.

    The exact recipient and all authority/scope/horizon values must be shown in a
    subsequent authenticated preview/adoption. The model supplies none of them.
    """

    model_config = ConfigDict(ser_json_bytes="base64", val_json_bytes="base64")

    payload: bytes = Field(min_length=1)
    bundle_members: tuple[Identity, ...] = Field(min_length=1)


class ConsequentialToolCall(Frozen):
    call_id: Identity
    tool: Literal["request_self_effect"]
    arguments: SelfEffectArguments


class ExecutionContinue(Frozen):
    kind: Literal["Continue"]
    tool_calls: tuple[ToolCall | ConsequentialToolCall, ...] = Field(min_length=1)


class ExecutionPromptArtifact(Frozen):
    prompt_id: Literal["chiplog.agent-loop"] = "chiplog.agent-loop"
    version: Literal["2"] = "2"
    content_hash: Digest
    library_version: Identity
    generator_version: Literal["chiplog.turn-schema.execution.v2"] = (
        "chiplog.turn-schema.execution.v2"
    )
    tools: tuple[ToolSpec | ConsequentialToolSpec, ...] = Field(min_length=1)
    response_schema_json: Identity
    rendered: Identity


class ExecutionVisibilityManifest(Frozen):
    tenant: Identity
    principal: Identity
    contour_head: Identity
    run_id: Identity
    turn_id: Identity
    call_slot: Literal[0] = 0
    generation: UInt64
    worker_session: Identity
    members: tuple[VisibilityMember, ...]
    joined_label: DisclosureLabel
    artifact: ExecutionPromptArtifact


class ExecutionModelAttempt(Frozen):
    attempt_id: Identity
    lineage_id: Identity
    generation: UInt64
    state: AttemptState
    head: Identity
    manifest: ExecutionVisibilityManifest
    request: Identity
    provider_contract: Literal["hermetic-model.v1", "codex-oauth.v1"]
    recipient: Literal["hermetic-model", "openai-codex"]
    live_model: LiveModelBinding | None
    worker_session: Identity
    response_base64: str | None
    receipt: str | None
    rejection: str | None


class ExecutionTurn(Frozen):
    turn_id: Identity
    ordinal: int = Field(gt=0, le=2**64 - 1)
    head: Identity
    state: Literal["PREPARING", "CALL_ACTIVE", "RESPONSE_AVAILABLE", "ACCEPTED", "REJECTED"]
    accumulator: tuple[VisibilityMember, ...]
    attempts: tuple[ExecutionModelAttempt, ...]
    selector: UInt64
    # None before sealed publication; an empty tuple is a sealed zero-call response.
    response_seal: CallSubjectHead | None
    initialized_calls: tuple[CallSubjectHead, ...] | None


class ExecutionRunRecord(Frozen):
    """Schema-selected Run data; no legacy ToolOutcome or free terminal result.

    The complete ordered initialized references join their original independently
    selected acceptance/result/recovery streams at every continuation boundary.
    Reference construction does not establish that those records exist.
    """

    schema_id: Literal["chiplog.agent-loop.execution-record.v2"] = (
        "chiplog.agent-loop.execution-record.v2"
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
    turns: tuple[ExecutionTurn, ...]
    delivery_acceptance: DeliveryAcceptanceReference | None
    suspension_baseline: CallSubjectHead | None
    original_obligations: tuple[OriginalObligationBinding, ...]
    no_retry_references: tuple[CallSubjectHead, ...]
    event: Identity
