"""Native non-tool effects and bounded work discovery; DTOs grant no authority.

Composition authenticates the caller, independently prepares Planning bytes and
checks current adoption, gate, original evidence and worker fences at publication
and SEND. Discovery is an observation, never a reusable permission to transmit.
"""

from typing import Annotated, Literal, Protocol

from pydantic import Field

from .contracts import ExactHead, ProviderRecipient
from .dispatch_authority_contracts import DispatchObservationDTO, Identity


class EffectActionRequest(DispatchObservationDTO):
    tenant_id: Identity
    principal_id: Identity
    intent_id: Identity
    adopted_preview: ExactHead
    authority_act_id: Identity
    payload: bytes
    bundle_members: tuple[Identity, ...] = Field(min_length=1)
    target: ProviderRecipient
    expires_at_ns: int = Field(gt=0)


class PlanEffectRequest(EffectActionRequest):
    kind: Literal["PLAN"] = "PLAN"
    planning_command_schema: Identity
    planning_command_bytes: bytes = Field(min_length=1)
    expected_planning_revision: ExactHead | None


class CompensationEffectRequest(EffectActionRequest):
    kind: Literal["COMPENSATION"] = "COMPENSATION"
    original_intent: ExactHead
    original_stream_id: Identity
    # Head of the complete original evidence inventory, never a selected subset.
    original_evidence_head: ExactHead


class PlanEffectIntent(DispatchObservationDTO):
    schema_id: Literal["chiplog.effects.plan-intent.v1"] = "chiplog.effects.plan-intent.v1"
    head: Identity
    request: PlanEffectRequest
    planning_revision: ExactHead


class CompensationEffectIntent(DispatchObservationDTO):
    schema_id: Literal["chiplog.effects.compensation-intent.v1"] = (
        "chiplog.effects.compensation-intent.v1"
    )
    head: Identity
    request: CompensationEffectRequest


class EffectWorkIdentity(DispatchObservationDTO):
    tenant_id: Identity
    principal_id: Identity
    stream_id: Identity
    intent: ExactHead
    send: ExactHead


class PendingEffectSend(EffectWorkIdentity):
    kind: Literal["PENDING_SEND"] = "PENDING_SEND"


class UnresolvedEffectClaim(EffectWorkIdentity):
    kind: Literal["RECONCILE"] = "RECONCILE"
    claim: ExactHead


EffectWork = Annotated[PendingEffectSend | UnresolvedEffectClaim, Field(discriminator="kind")]


class EffectWorkScan(DispatchObservationDTO):
    tenant_id: Identity
    principal_id: Identity
    limit: int = Field(gt=0)


class EffectWorkDiscovery(DispatchObservationDTO):
    snapshot_head: Identity
    items: tuple[EffectWork, ...]
    has_more: bool


class EffectWorkStep(DispatchObservationDTO):
    """One item only. EMITTED means custody claimed and provider call attempted.

    EMITTED does not assert delivery or a known outcome. RECONCILED means an
    observation was retained, not that the original obligation was closed.
    """

    work: EffectWork
    disposition: Literal["EMITTED", "RECONCILED", "HELD"]
    current_head: Identity
    detail: str


class EffectWorkPort(Protocol):
    async def discover_effect_work(self, request: EffectWorkScan) -> EffectWorkDiscovery: ...

    async def step_effect_work(self, work: EffectWork) -> EffectWorkStep: ...
