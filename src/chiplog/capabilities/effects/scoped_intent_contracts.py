"""Fresh versioned effects origins; old intents and their uncertainty stay immutable.

These shapes retain authority inputs, not authority itself. The registered owner
enforces the origin/acquisition matrix and exact mandate bytes; the broker must
authenticate source records and recheck current bindings at publication and SEND.
"""

from typing import Annotated, Literal, Protocol

from pydantic import Field

from .contracts import (
    CommandIdentity,
    DeliverySendBinding,
    DispatchSemanticBinding,
    ExactHead,
    OriginalAmbiguity,
    ProviderRecipient,
)
from .dispatch_authority_contracts import (
    Digest,
    DispatchObservationDTO,
    DispatchSourceInventory,
    Identity,
)
from .dispatch_v2_contracts import (
    CurrentDispatchInputsV2,
    DispatchBoundaryFailureV2,
    InitializedCallOrigin,
    MandateHorizon,
    PlanEffectOrigin,
)
from .fences import Absent, WorkerFence


class CompensationOriginV3(DispatchObservationDTO):
    kind: Literal["COMPENSATION"] = "COMPENSATION"
    original: OriginalAmbiguity
    consequence_addressed: ExactHead
    proposed_compensation: ExactHead


class DuplicateRiskOriginV3(DispatchObservationDTO):
    kind: Literal["AUTHORIZE_DUPLICATE_RISK"] = "AUTHORIZE_DUPLICATE_RISK"
    unresolved_attempts: tuple[OriginalAmbiguity, ...] = Field(min_length=1)
    possible_duplicate_effects: tuple[ExactHead, ...] = Field(min_length=1)
    affected_parties_resources: tuple[ExactHead, ...] = Field(min_length=1)
    commitment_consequences: tuple[ExactHead, ...] = Field(min_length=1)
    safer_alternatives: tuple[ExactHead, ...] = Field(min_length=1)


class PreparedDeliveryOriginV3(DispatchObservationDTO):
    kind: Literal["PREPARED_DELIVERY"] = "PREPARED_DELIVERY"
    original_run: ExactHead
    captured_attempt: ExactHead
    binding: DeliverySendBinding
    preparation_basis: ExactHead


ScopedDispatchOrigin = Annotated[
    InitializedCallOrigin
    | PlanEffectOrigin
    | CompensationOriginV3
    | DuplicateRiskOriginV3
    | PreparedDeliveryOriginV3,
    Field(discriminator="kind"),
]


class DispatchMandateV3(DispatchObservationDTO):
    schema_id: Literal["chiplog.effects.dispatch-mandate.v3"] = (
        "chiplog.effects.dispatch-mandate.v3"
    )
    mandate_id: Identity
    tenant_id: Identity
    principal_id: Identity
    actor_id: Identity
    operation_profile: ExactHead
    origin: ScopedDispatchOrigin
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


class ScopedAuthorityRecord(DispatchObservationDTO):
    owner: Identity
    head: ExactHead
    schema_id: Identity
    canonical_record_bytes: bytes = Field(min_length=1)
    selected_decision: ExactHead


class ScopedPrecursorRequest(DispatchObservationDTO):
    kind: Literal["SCOPED_PRECURSOR_REQUEST_V3"] = "SCOPED_PRECURSOR_REQUEST_V3"
    request_id: Identity
    mandate: DispatchMandateV3
    interpretation_policy: ScopedAuthorityRecord
    preexisting_sources: tuple[ScopedAuthorityRecord, ...] = Field(min_length=1)


class ScopedPrecursorResult(DispatchObservationDTO):
    kind: Literal["SCOPED_PRECURSOR_RESULT_V3"] = "SCOPED_PRECURSOR_RESULT_V3"
    source_request_fingerprint: Digest
    mandate_fingerprint: Digest
    interpretation_policy: ExactHead
    complete_evaluation_evidence: tuple[ExactHead, ...] = Field(min_length=1)


class HumanScopedAdoption(DispatchObservationDTO):
    kind: Literal["HUMAN_ADOPTION"] = "HUMAN_ADOPTION"
    adoption_act: ScopedAuthorityRecord
    display: ExactHead
    exact_display_bytes: bytes = Field(min_length=1)
    exact_mandate_bytes: bytes = Field(min_length=1)
    authenticated_invocation: ScopedAuthorityRecord


class RegisteredCompensationAuthority(DispatchObservationDTO):
    kind: Literal["REGISTERED_COMPENSATION_AUTHORITY"] = "REGISTERED_COMPENSATION_AUTHORITY"
    authority_or_bounded_mandate: ScopedAuthorityRecord
    current_applicability: ScopedAuthorityRecord
    adoption_requirement: ScopedAuthorityRecord
    exact_mandate_bytes: bytes = Field(min_length=1)


class PreparedDeliveryBasisV3(DispatchObservationDTO):
    """Loop-only primitive; final combined completion/effects outputs are forbidden.

    Decode loop_proposal_bytes as DeliveryAcceptanceProposal, which contains no
    effects members. A caller-selected schema string cannot change that decoder.
    """

    schema_id: Literal["chiplog.loop.delivery-preparation-basis.v1"] = (
        "chiplog.loop.delivery-preparation-basis.v1"
    )
    owner: Literal["agent_loop"] = "agent_loop"
    source_cut: ExactHead
    acceptance: ExactHead
    completion_command_bytes: bytes = Field(min_length=1)
    delivery_observation_bytes: bytes = Field(min_length=1)
    loop_proposal_bytes: bytes = Field(min_length=1)


class PreparedDeliveryAuthority(DispatchObservationDTO):
    kind: Literal["DELIVERY_PREPARATION"] = "DELIVERY_PREPARATION"
    basis: PreparedDeliveryBasisV3
    preexisting_communication_authority: ScopedAuthorityRecord
    current_disclosure_authority: ScopedAuthorityRecord
    exact_mandate_bytes: bytes = Field(min_length=1)


ScopedAcquisitionAuthority = Annotated[
    HumanScopedAdoption | RegisteredCompensationAuthority | PreparedDeliveryAuthority,
    Field(discriminator="kind"),
]

# Declarative contract, not a validator or a permission grant. The owner checks
# both this matrix and actual independently authenticated authority semantics.
SCOPED_AUTHORITY_MATRIX: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("INITIALIZED_CONSEQUENTIAL_CALL", ("HUMAN_ADOPTION",)),
    ("PLAN_EFFECT_PUBLICATION", ("HUMAN_ADOPTION",)),
    ("COMPENSATION", ("HUMAN_ADOPTION", "REGISTERED_COMPENSATION_AUTHORITY")),
    ("AUTHORIZE_DUPLICATE_RISK", ("HUMAN_ADOPTION",)),
    ("PREPARED_DELIVERY", ("DELIVERY_PREPARATION",)),
)


class ScopedDispatchAcquisition(DispatchObservationDTO):
    authority: ScopedAcquisitionAuthority
    precursor_request: ScopedPrecursorRequest
    precursor_result: ScopedPrecursorResult
    original_sources: DispatchSourceInventory


class ExternalActionIntentV3(DispatchObservationDTO):
    """Fingerprint domain excludes only fingerprint; it includes all scope/acquisition bytes."""

    schema_id: Literal["chiplog.effects.external-action-intent.v3"] = (
        "chiplog.effects.external-action-intent.v3"
    )
    intent_id: Identity
    fingerprint: Digest
    mandate: DispatchMandateV3
    acquisition: ScopedDispatchAcquisition


class PrepareScopedIntentPublication(DispatchObservationDTO):
    kind: Literal["PREPARE_SCOPED_INTENT_PUBLICATION_V3"] = "PREPARE_SCOPED_INTENT_PUBLICATION_V3"
    identity: CommandIdentity
    intent: ExternalActionIntentV3
    expected_intent: Absent
    current: CurrentDispatchInputsV2
    complete_current_origin_sources: tuple[ScopedAuthorityRecord, ...]
    fence: WorkerFence


class PreparedScopedIntentPublication(DispatchObservationDTO):
    """Fresh intent only; no original-intent mutation, SEND, or provider receipt."""

    kind: Literal["PREPARED_SCOPED_INTENT_PUBLICATION_V3"] = "PREPARED_SCOPED_INTENT_PUBLICATION_V3"
    source_request_fingerprint: Digest
    intent: ExternalActionIntentV3
    original_references: tuple[ExactHead, ...]
    complete_owner_commitment: Digest


class ScopedIntentPreparationPort(Protocol):
    async def prepare_precursor(
        self, request: ScopedPrecursorRequest
    ) -> ScopedPrecursorResult | DispatchBoundaryFailureV2: ...

    async def prepare_scoped_intent(
        self, request: PrepareScopedIntentPublication
    ) -> PreparedScopedIntentPublication | DispatchBoundaryFailureV2: ...
