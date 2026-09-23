"""Independent denying observations and owner requests; construction grants nothing.

Only a future private issuer may authenticate these sources and admit exact owner
output to storage. This contract does not extend or replace original SEND authority.
"""

from typing import Annotated, Literal

from pydantic import Field

from .contracts import (
    CommandIdentity,
    DispatchAuthorization,
    DispatchSemanticBinding,
    EffectStoreSnapshot,
    ExactHead,
)
from .dispatch_authority_contracts import CapturedSource, DispatchObservationDTO, Identity
from .fences import WorkerFence


class HoldDecision(DispatchObservationDTO):
    kind: Literal["HELD_BEFORE_SEND"] = "HELD_BEFORE_SEND"
    evidence: ExactHead


class CancelDecision(DispatchObservationDTO):
    kind: Literal["CANCELLED_BEFORE_SEND"] = "CANCELLED_BEFORE_SEND"
    evidence: ExactHead
    direct_act: ExactHead


class SupersedeDecision(DispatchObservationDTO):
    kind: Literal["SUPERSEDED_BEFORE_SEND"] = "SUPERSEDED_BEFORE_SEND"
    evidence: ExactHead
    successor_intent: ExactHead
    successor_adoption: ExactHead


DenyingDecision = Annotated[
    HoldDecision | CancelDecision | SupersedeDecision, Field(discriminator="kind")
]


class DenyingSources(DispatchObservationDTO):
    """All sources are required; missing acquisition cannot prepare a disposition."""

    invocation: CapturedSource
    principal_rights: CapturedSource
    operation_registry: CapturedSource
    effects_history: CapturedSource
    runtime_fence: CapturedSource
    clock: CapturedSource
    decision: CapturedSource


class DenyingAuthority(DispatchObservationDTO):
    schema_id: Literal["chiplog.effects.denying-authority.v1"]
    tenant_id: Identity
    principal_id: Identity
    actor_id: Identity
    authenticated_session: ExactHead
    operation_profile: ExactHead
    intent: ExactHead
    expected_attempt: ExactHead
    decision: DenyingDecision
    fence: WorkerFence
    clock_contract: Identity
    clock_epoch: Identity
    valid_until_ns: int = Field(ge=0)
    sources: DenyingSources


class BeforeSendDispositionV2Command(DispatchObservationDTO):
    schema_id: Literal["chiplog.effects.before-send-command.v2"]
    identity: CommandIdentity
    intent: ExactHead
    expected_attempt: ExactHead
    authority: DenyingAuthority
    authorization_to_retire: DispatchAuthorization | None


class CurrentDenialInputs(DispatchObservationDTO):
    """Independently captured inputs, not caller-selected authorization flags."""

    command_id: Identity
    command_fingerprint: Identity
    store_frontier: int = Field(ge=0)
    authority: DenyingAuthority
    supported_semantics: DispatchSemanticBinding | None
    fence: WorkerFence
    clock_contract: Identity
    clock_epoch: Identity
    observed_time_ns: int = Field(ge=0)


class DenialPreparationRequest(DispatchObservationDTO):
    schema_id: Literal["chiplog.effects.before-send-preparation.v2"]
    command: BeforeSendDispositionV2Command
    expected: EffectStoreSnapshot
    current: CurrentDenialInputs
