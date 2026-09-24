"""Effects-owned all-child retry and semantic reduction preparation.

Original v2 intent/mandate and historical schemas are unchanged. Construction
confers no SEND authority; the adapter rechecks the independent current cut and
coverage at the last local send boundary. Reduction can never allocate a child.
"""

from typing import Annotated, Literal, Protocol

from pydantic import Field

from .contracts import (
    AttemptState,
    CommandIdentity,
    ExactHead,
    ProviderRecipient,
    TransmissionAttempt,
)
from .dispatch_authority_contracts import Digest, DispatchObservationDTO, Identity
from .dispatch_outcome_contracts import DispatchObligationV2
from .dispatch_v2 import DispatchAuthorizationV2
from .dispatch_v2_contracts import (
    CurrentDispatchInputsV2,
    DispatchBoundaryFailureV2,
    ExternalActionIntentV2,
)
from .fences import Absent, Present, UInt64, WorkerFence


class SelectedEffectsSource(DispatchObservationDTO):
    owner: Identity
    subject: ExactHead
    schema_id: Identity
    canonical_record_bytes: bytes = Field(min_length=1)
    selected_decision: ExactHead
    physical_record: ExactHead


class EffectsLineageCut(DispatchObservationDTO):
    tenant_id: Identity
    database_id: Identity
    tenant_commit_sequence: UInt64
    materialization_commitment: Digest
    original_intent: ExternalActionIntentV2
    original_authorization: DispatchAuthorizationV2
    current_parent: ExactHead
    state: AttemptState
    complete_ordered_children: tuple[TransmissionAttempt, ...] = Field(min_length=1)
    complete_child_sources: tuple[SelectedEffectsSource, ...] = Field(min_length=1)
    complete_ordered_evidence: tuple[SelectedEffectsSource, ...]
    original_obligation: DispatchObligationV2
    complete_inventory_fingerprint: Digest


class RetryCoverageSubject(DispatchObservationDTO):
    original_intent: ExactHead
    exact_effect_fingerprint: Digest
    exact_payload_fingerprint: Digest
    recipient: ProviderRecipient
    idempotency_fence_key: Identity
    complete_prior_children: tuple[ExactHead, ...] = Field(min_length=1)
    next_transmission_id: Identity
    next_ordinal: int = Field(strict=True, gt=0, le=2**64 - 1)
    clock_contract: Identity
    clock_epoch: Identity
    coverage_starts_ns: UInt64
    coverage_expires_ns: UInt64


class ProviderIdempotencyCoverage(DispatchObservationDTO):
    kind: Literal["PROVIDER_IDEMPOTENCY_COVERS_ALL"] = "PROVIDER_IDEMPOTENCY_COVERS_ALL"
    subject: RetryCoverageSubject
    provider_contract: SelectedEffectsSource
    complete_coverage_proof: SelectedEffectsSource


class ChildPermanentIncapacity(DispatchObservationDTO):
    child: ExactHead
    reason: Literal[
        "TERMINAL_PROVIDER_REJECTION",
        "TERMINAL_PROVIDER_CANCELLATION",
        "ENFORCED_LEASE_EXPIRED",
        "WINNING_PROVIDER_FENCE",
    ]
    registered_protocol: ExactHead
    evidence: SelectedEffectsSource


class AllPriorChildrenIncapable(DispatchObservationDTO):
    kind: Literal["ALL_PRIOR_PERMANENTLY_INCAPABLE"] = "ALL_PRIOR_PERMANENTLY_INCAPABLE"
    subject: RetryCoverageSubject
    complete_ordered_proofs: tuple[ChildPermanentIncapacity, ...] = Field(min_length=1)


SafeRetryCoverage = Annotated[
    ProviderIdempotencyCoverage | AllPriorChildrenIncapable, Field(discriminator="kind")
]


class PrepareSafeRetransmission(DispatchObservationDTO):
    kind: Literal["PREPARE_SAFE_RETRANSMISSION_V1"] = "PREPARE_SAFE_RETRANSMISSION_V1"
    identity: CommandIdentity
    observed: EffectsLineageCut
    expected_parent_state: Literal["SEND_COMMITTED", "SENT", "OUTCOME_UNKNOWN"]
    coverage: SafeRetryCoverage
    current: CurrentDispatchInputsV2
    fence: WorkerFence


class RetransmissionDecisionRecord(DispatchObservationDTO):
    """Primitive SEND commitment: child.send_commit points here, not to its parent.

    Derive decision → child → parent → complete batch. The last-local writer
    publishes all three together; no child/parent hash feeds back into decision.
    """

    identity: CommandIdentity
    source_request_fingerprint: Digest
    original_intent: ExactHead
    original_authorization: ExactHead
    prior_parent: ExactHead
    coverage: SafeRetryCoverage
    current_inputs_fingerprint: Digest
    fence: WorkerFence


class RetriedParentRevision(DispatchObservationDTO):
    schema_id: Literal["chiplog.effects.retried-parent.v1"] = "chiplog.effects.retried-parent.v1"
    original_intent: ExactHead
    original_authorization: ExactHead
    send_decision: ExactHead
    predecessor: ExactHead
    state: Literal["SEND_COMMITTED", "SENT", "OUTCOME_UNKNOWN"]
    complete_ordered_children: tuple[ExactHead, ...] = Field(min_length=2)
    retained_evidence: tuple[ExactHead, ...]
    retained_obligation: ExactHead
    coverage: SafeRetryCoverage
    current_command_fingerprint: Digest
    fence: WorkerFence


class PreparedSafeRetransmission(DispatchObservationDTO):
    kind: Literal["PREPARED_SAFE_RETRANSMISSION_V1"] = "PREPARED_SAFE_RETRANSMISSION_V1"
    source_request_fingerprint: Digest
    decision: RetransmissionDecisionRecord
    parent: RetriedParentRevision
    child: TransmissionAttempt
    complete_batch_fingerprint: Digest


class EffectsReductionConsumable(DispatchObservationDTO):
    kind: Literal["CONSUMABLE"] = "CONSUMABLE"
    semantic_class: Identity


class EffectsReductionHold(DispatchObservationDTO):
    kind: Literal["HOLD"] = "HOLD"
    reason: Literal[
        "INCOMPLETE",
        "RIVAL",
        "CONTRADICTORY",
        "MEANING_CHANGED",
        "UNCLASSIFIED",
        "REGISTRY_CHANGED",
    ]
    relevant_evidence: tuple[ExactHead, ...]


class EffectsSemanticReductionRecord(DispatchObservationDTO):
    """Owner producer, also valid before closure; own head is external to this record."""

    schema_id: Literal["chiplog.effects.semantic-reduction.v1"] = (
        "chiplog.effects.semantic-reduction.v1"
    )
    owner: Literal["effects"] = "effects"
    stream_id: Identity
    reduction_id: Identity
    original_intent: ExactHead
    predecessor: Annotated[Absent | Present, Field(discriminator="kind")]
    reducer: ExactHead
    complete_ordered_children: tuple[ExactHead, ...] = Field(min_length=1)
    complete_ordered_evidence: tuple[ExactHead, ...]
    current_parent: ExactHead
    original_obligation: ExactHead
    original_terminal_closure: Annotated[Absent | Present, Field(discriminator="kind")]
    disposition: Annotated[
        EffectsReductionConsumable | EffectsReductionHold, Field(discriminator="kind")
    ]


class SelectedEffectsReduction(DispatchObservationDTO):
    kind: Literal["SELECTED_EFFECTS_REDUCTION"] = "SELECTED_EFFECTS_REDUCTION"
    source: SelectedEffectsSource
    record: EffectsSemanticReductionRecord


class PrepareEffectsSemanticReduction(DispatchObservationDTO):
    """Separately authenticated evidence processing needs no live original Run."""

    kind: Literal["PREPARE_EFFECTS_SEMANTIC_REDUCTION_V1"] = "PREPARE_EFFECTS_SEMANTIC_REDUCTION_V1"
    identity: CommandIdentity
    observed: EffectsLineageCut
    stream_id: Identity
    expected: Annotated[Absent | SelectedEffectsReduction, Field(discriminator="kind")]
    registered_reducer: SelectedEffectsSource
    authenticated_invocation: SelectedEffectsSource


class PreparedEffectsSemanticReduction(DispatchObservationDTO):
    kind: Literal["PREPARED_EFFECTS_SEMANTIC_REDUCTION_V1"] = (
        "PREPARED_EFFECTS_SEMANTIC_REDUCTION_V1"
    )
    source_request_fingerprint: Digest
    record: EffectsSemanticReductionRecord
    complete_batch_fingerprint: Digest


EffectsLifecycleRequest = Annotated[
    PrepareSafeRetransmission | PrepareEffectsSemanticReduction, Field(discriminator="kind")
]
EffectsLifecycleResult = (
    Annotated[
        PreparedSafeRetransmission | PreparedEffectsSemanticReduction, Field(discriminator="kind")
    ]
    | DispatchBoundaryFailureV2
)


class EffectsLifecyclePreparationPort(Protocol):
    async def prepare_lifecycle(
        self, request: EffectsLifecycleRequest
    ) -> EffectsLifecycleResult: ...
