"""Inert v2 mandate boundary; constructing any value grants no SEND permission.

The mandate is adopted after exact call initialization. Acceptance retains it and
its acquisition inside the sole external intent. Fresh observations never replace
these original bytes or extend their horizon. V1 interpretation is unchanged.
"""

from typing import Annotated, Literal

from pydantic import Field

from .contracts import CommandIdentity, DispatchSemanticBinding, ExactHead, ProviderRecipient
from .dispatch_authority_contracts import (
    Digest,
    DispatchAuthorityObservation,
    DispatchObservationDTO,
    DispatchSourceInventory,
    Identity,
)
from .fences import WorkerFence


class InitializedCallOrigin(DispatchObservationDTO):
    kind: Literal["INITIALIZED_CONSEQUENTIAL_CALL"]
    original_run_id: Identity
    original_call_id: Identity
    initialization_run_head: ExactHead
    initialized_head: ExactHead
    tool_name: Identity
    tool_schema: Identity
    tool_policy: ExactHead
    sealed_arguments: bytes = Field(min_length=1)


class PlanEffectOrigin(DispatchObservationDTO):
    kind: Literal["PLAN_EFFECT_PUBLICATION"]
    proposal: ExactHead
    planning_predecessor: ExactHead
    proposed_planning_revision: ExactHead
    original_planning_request: bytes = Field(min_length=1)
    original_planning_result: bytes = Field(min_length=1)


DispatchOrigin = Annotated[InitializedCallOrigin | PlanEffectOrigin, Field(discriminator="kind")]


class MandateHorizon(DispatchObservationDTO):
    """Original adopted upper bound; cross-epoch continuity needs independent proof."""

    clock_contract: Identity
    clock_epoch: Identity
    not_before_ns: int = Field(ge=0)
    expires_at_ns: int = Field(gt=0)
    continuity_policy: ExactHead


class DispatchMandateV2(DispatchObservationDTO):
    schema_id: Literal["chiplog.effects.dispatch-mandate.v2"]
    mandate_id: Identity
    tenant_id: Identity
    principal_id: Identity
    actor_id: Identity
    operation_profile: ExactHead
    origin: DispatchOrigin
    planning_revision: ExactHead
    preexisting_authority_basis: ExactHead
    authority_sources: tuple[ExactHead, ...] = Field(min_length=1)
    affected_party_constraints: tuple[ExactHead, ...]
    normative_conflict_generation: ExactHead
    dependencies: tuple[ExactHead, ...]
    factual_assertion_evidence: tuple[ExactHead, ...]
    verification_contradiction: tuple[ExactHead, ...]
    authority_applicability: tuple[ExactHead, ...]
    consequence_scope: ExactHead
    communication_mandate: ExactHead
    disclosure_projection: ExactHead
    channel_class: Identity
    interaction_context: ExactHead
    recipient: ProviderRecipient
    payload: bytes = Field(min_length=1)
    effect_fingerprint: Digest
    bundle_members: tuple[ExactHead, ...] = Field(min_length=1)
    idempotency_fence_key: Identity
    horizon: MandateHorizon
    semantics: DispatchSemanticBinding


class DispatchAdoptionV2(DispatchObservationDTO):
    """Exact submitted adoption, authenticated only by a later private issuer."""

    schema_id: Literal["chiplog.effects.dispatch-adoption.v2"]
    adoption_act_id: Identity
    display: ExactHead
    display_bytes: bytes = Field(min_length=1)
    mandate_bytes: bytes = Field(min_length=1)
    ingress: ExactHead
    ingress_bytes: bytes = Field(min_length=1)


class DispatchPrecursorRequestV2(DispatchObservationDTO):
    """Pre-publication evaluation of an already constructed mandate, not its intent."""

    schema_id: Literal["chiplog.effects.dispatch-precursor-request.v2"]
    request_id: Identity
    mandate_digest: Digest
    interpretation_policy: ExactHead
    preexisting_source_heads: tuple[ExactHead, ...] = Field(min_length=1)


class DispatchPrecursorResultV2(DispatchObservationDTO):
    """Evaluation result predates adoption/acquisition/intent; none can be embedded."""

    schema_id: Literal["chiplog.effects.dispatch-precursor-result.v2"]
    request_digest: Digest
    mandate_digest: Digest
    interpretation_policy: ExactHead
    evaluation_evidence: tuple[ExactHead, ...] = Field(min_length=1)


class DispatchAcquisitionV2(DispatchObservationDTO):
    """Original evidence, retained unchanged even after its observation leases expire."""

    schema_id: Literal["chiplog.effects.dispatch-acquisition.v2"]
    adoption: DispatchAdoptionV2
    authenticated_invocation: bytes = Field(min_length=1)
    precursor_request: DispatchPrecursorRequestV2
    precursor_result: DispatchPrecursorResultV2
    original_sources: DispatchSourceInventory


class ExternalActionIntentV2(DispatchObservationDTO):
    schema_id: Literal["chiplog.effects.external-action-intent.v2"]
    intent_id: Identity
    fingerprint: Digest
    mandate: DispatchMandateV2
    acquisition: DispatchAcquisitionV2


class PublishDispatchIntentV2(DispatchObservationDTO):
    """One intent companion; origin chooses acceptance versus PlanEffect batch."""

    schema_id: Literal["chiplog.effects.publish-dispatch-intent.v2"]
    identity: CommandIdentity
    intent: ExternalActionIntentV2
    complete_publication_manifest: tuple[ExactHead, ...] = Field(min_length=1)
    fence: WorkerFence


class AuthorizeDispatchV2(DispatchObservationDTO):
    schema_id: Literal["chiplog.effects.authorize-dispatch.v2"]
    identity: CommandIdentity
    intent: ExactHead
    expected_attempt: ExactHead
    immutable_mandate: ExactHead
    semantics: DispatchSemanticBinding
    fence: WorkerFence


class CommitFirstSendV2(DispatchObservationDTO):
    """First child only; safe retransmission needs its own all-child proof boundary."""

    schema_id: Literal["chiplog.effects.commit-first-send.v2"]
    identity: CommandIdentity
    intent: ExactHead
    expected_attempt: ExactHead
    authorization: ExactHead
    immutable_mandate: ExactHead
    semantics: DispatchSemanticBinding
    fence: WorkerFence
    ordinal: Literal[0]


class CurrentDispatchInputsV2(DispatchObservationDTO):
    """Independent fresh observations, not a reconstructed or renewed mandate."""

    schema_id: Literal["chiplog.effects.current-dispatch-inputs.v2"]
    command_fingerprint: Digest
    immutable_mandate: ExactHead
    observation: DispatchAuthorityObservation
    supported_semantics: DispatchSemanticBinding | None
    clock_contract: Identity
    clock_epoch: Identity
    observed_time_ns: int = Field(ge=0)
    lease_expires_at_ns: int = Field(gt=0)


class DispatchBoundaryFailureV2(DispatchObservationDTO):
    schema_id: Literal["chiplog.effects.dispatch-boundary-failure.v2"]
    kind: Literal["DENIED", "STALE", "CONFLICT", "UNAVAILABLE", "DISPATCH_VERSION_HOLD"]
    source_role: Identity | None
    reason: Identity
