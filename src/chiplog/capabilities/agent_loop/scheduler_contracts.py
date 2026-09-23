"""R15 public proposals and immutable observations; no scheduler implementation.

Constructing these DTOs supplies neither authority nor proof of current state.
The registered owner/writer derives canonical decisions and verifies issued
context, exact heads, enumeration and complete publication at its transaction cut.
"""

from __future__ import annotations

from typing import Annotated, Literal, Protocol

from pydantic import Field

from chiplog.capabilities.agent_loop.recovery_contracts import (
    Absent,
    Digest,
    ExecutionLineageBinding,
    ExecutionLineageSubject,
    Identity,
    LeaseBinding,
    PhysicalRootBinding,
    PhysicalRootRolloverFence,
    PreRootDecisionFence,
    Present,
    RecoveryDTO,
    RolloverPredecessor,
    TrustedClockProofRef,
    UInt64,
)


class SchedulerContextRef(RecoveryDTO):
    """Reference to separately authenticated tenant/service/mandate issuance."""

    tenant_id: Identity
    service_identity: Identity
    session_id: Identity
    mandate_head: Identity
    issuance_id: Identity
    issuance_fingerprint: Digest


class ScheduleDefinitionHead(RecoveryDTO):
    schedule_id: Identity
    schedule_revision: Identity
    head: Identity
    fingerprint: Digest


class MissedOccurrencePolicyHead(RecoveryDTO):
    policy_id: Identity
    policy_revision: Identity
    head: Identity
    fingerprint: Digest
    kind: Literal["SKIP", "COALESCE", "MATERIALIZE_EACH"]


class DueCoordinate(RecoveryDTO):
    """Opaque canonical coordinate under the exact schedule coordinate policy."""

    coordinate_policy_version: Identity
    canonical_coordinate: Identity


class SchedulerEligibilityBoundary(RecoveryDTO):
    schedule_definition_head: ScheduleDefinitionHead
    missed_occurrence_policy_head: MissedOccurrencePolicyHead
    previous_due_boundary: DueCoordinate
    cutoff_due_coordinate: DueCoordinate
    enumeration_frontier: Identity
    predecessor_interval: Annotated[Absent | Present, Field(discriminator="kind")]
    canonicalization_version: Identity


class UndisposedOccurrence(RecoveryDTO):
    occurrence_id: Identity
    schedule_id: Identity
    schedule_revision: Identity
    due_coordinate: DueCoordinate
    undisposed_head: Identity
    undisposed_fingerprint: Digest


class SchedulerEligibilityManifest(RecoveryDTO):
    members: tuple[UndisposedOccurrence, ...]
    fingerprint: Digest


class SchedulerIntervalBound(RecoveryDTO):
    max_member_count: int = Field(gt=0)
    max_manifest_bytes: int = Field(gt=0)
    max_serialized_batch_bytes: int = Field(gt=0)


class SchedulerIntervalBoundHead(RecoveryDTO):
    head_id: Identity
    predecessor: Annotated[Absent | Present, Field(discriminator="kind")]
    generation: UInt64
    bound: SchedulerIntervalBound
    owner_id: Identity
    authority_epoch: Identity
    broker_generation: Identity
    runtime_graph_generation: Identity
    canonicalization_version: Identity


class SchedulerCommandIdentity(RecoveryDTO):
    command_id: Identity
    schema_version: Identity
    canonicalization_version: Identity


class DecideIntervalCommand(RecoveryDTO):
    identity: SchedulerCommandIdentity
    boundary: SchedulerEligibilityBoundary
    bound_head: SchedulerIntervalBoundHead
    manifest: SchedulerEligibilityManifest | StreamingEligibilityEvidence
    publication_fence: PreRootDecisionFence


class ReplaceIntervalBoundCommand(RecoveryDTO):
    identity: SchedulerCommandIdentity
    observed_head: SchedulerIntervalBoundHead
    proposed_bound: SchedulerIntervalBound
    scheduler_authority_head: Identity
    authority_epoch: Identity
    broker_generation: Identity
    runtime_graph_generation: Identity


class FullEligibilityEvidence(RecoveryDTO):
    kind: Literal["FULL_MANIFEST"] = "FULL_MANIFEST"
    manifest: SchedulerEligibilityManifest


class StreamingEligibilityEvidence(RecoveryDTO):
    kind: Literal["STREAMING_MANIFEST"] = "STREAMING_MANIFEST"
    manifest_digest: Digest
    member_count: int = Field(ge=0)
    first_member: Annotated[Absent | Present, Field(discriminator="kind")]
    last_member: Annotated[Absent | Present, Field(discriminator="kind")]
    order_contract_version: Identity
    enumeration_completeness_proof: Present


# The closed command union includes the streaming witness declared below it.
DecideIntervalCommand.model_rebuild()


class OverflowActive(RecoveryDTO):
    kind: Literal["ACTIVE"] = "ACTIVE"


class OverflowResolved(RecoveryDTO):
    kind: Literal["RESOLVED_BY_OPERATOR"] = "RESOLVED_BY_OPERATOR"
    resolution_decision: Present


class SchedulerOverflowHold(RecoveryDTO):
    hold: Present
    command: SchedulerCommandIdentity
    boundary: SchedulerEligibilityBoundary
    bound_head: SchedulerIntervalBoundHead
    exceeded_dimension: Literal["MEMBER_COUNT", "MANIFEST_BYTES", "SERIALIZED_BATCH_BYTES"]
    actual_value: int = Field(ge=0)
    limit: int = Field(gt=0)
    evidence: Annotated[
        FullEligibilityEvidence | StreamingEligibilityEvidence, Field(discriminator="kind")
    ]
    operator_recovery_owner: Identity
    state: Annotated[OverflowActive | OverflowResolved, Field(discriminator="kind")]


class ResolveIntervalCommand(RecoveryDTO):
    identity: SchedulerCommandIdentity
    active_hold: SchedulerOverflowHold
    successor_bound: SchedulerIntervalBoundHead
    boundary: SchedulerEligibilityBoundary
    complete_manifest: SchedulerEligibilityManifest
    operator_proof: Present
    publication_fence: PreRootDecisionFence


class MaterializationIdentity(RecoveryDTO):
    """Acyclic primitive-domain identity, never the finalized envelope commitment."""

    materialization_id: Identity
    primitive_domain_fingerprint: Digest


class IndividualDisposition(RecoveryDTO):
    kind: Literal["CLAIMED_INDIVIDUALLY"] = "CLAIMED_INDIVIDUALLY"
    occurrence: UndisposedOccurrence
    materialization: MaterializationIdentity


class CoalescedDisposition(RecoveryDTO):
    kind: Literal["COALESCED_INTO"] = "COALESCED_INTO"
    occurrence: UndisposedOccurrence
    aggregate_id: Identity
    manifest_fingerprint: Digest
    materialization: MaterializationIdentity


class SkippedDisposition(RecoveryDTO):
    kind: Literal["SKIPPED"] = "SKIPPED"
    occurrence: UndisposedOccurrence
    policy_decision: Present


OccurrenceDisposition = Annotated[
    IndividualDisposition | CoalescedDisposition | SkippedDisposition,
    Field(discriminator="kind"),
]


class GenesisLease(RecoveryDTO):
    kind: Literal["UNLEASED"] = "UNLEASED"
    lease_head: Identity
    generation: Literal[0] = 0


class HeldLease(RecoveryDTO):
    kind: Literal["HELD"] = "HELD"
    binding: LeaseBinding


class ExhaustedLease(RecoveryDTO):
    kind: Literal["GENERATION_EXHAUSTED_HOLD"] = "GENERATION_EXHAUSTED_HOLD"
    lease_head: Identity
    generation: Literal[18446744073709551615] = 18446744073709551615
    exhausted_command_id: Identity
    trusted_expiry: UInt64
    authority_epoch: Identity
    preceding_held_lease: LeaseBinding


ExecutionRootLeaseState = Annotated[
    GenesisLease | HeldLease | ExhaustedLease, Field(discriminator="kind")
]


class MaterializationCommitment(RecoveryDTO):
    """Internal owner result. Encoders/writer, not this DTO, prove complete joins.

    primitive_domain excludes lease, initialization, reciprocal and commitment
    slots; finalized_members excludes the envelope commitment slot. Neither
    digest may include itself. The writer independently checks the registered DAG.
    """

    kind: Literal["INDIVIDUAL", "COALESCED"]
    parent_decision: Present
    subject: ExecutionLineageSubject
    dispositions: tuple[OccurrenceDisposition, ...] = Field(min_length=1)
    lineage: ExecutionLineageBinding
    initial_run: Present
    initialization: Present
    reciprocal_run_root: Present
    physical_root: PhysicalRootBinding
    physical_epoch: Present
    genesis_lease: GenesisLease
    companion_manifest: Present
    primitive_domain_fingerprint: Digest
    finalized_members_fingerprint: Digest
    batch_fingerprint: Digest
    schema_dependency_manifest: Present


IntervalBranch = Literal[
    "BOUNDARY_ONLY_NO_WORK",
    "SKIP_ALL",
    "COALESCE_SINGLE",
    "COALESCE_MULTI",
    "MATERIALIZE_EACH_SINGLE",
    "MATERIALIZE_EACH_MULTI",
]


class ScheduledIntervalDecision(RecoveryDTO):
    kind: Literal["INTERVAL_DECISION"] = "INTERVAL_DECISION"
    decision: Present
    command: SchedulerCommandIdentity
    branch: IntervalBranch
    boundary: SchedulerEligibilityBoundary
    bound_head: SchedulerIntervalBoundHead
    manifest: SchedulerEligibilityManifest
    dispositions: tuple[OccurrenceDisposition, ...]
    materializations: tuple[MaterializationCommitment, ...]
    resulting_boundary: DueCoordinate
    complete_commitment: Digest


class SchedulerIntervalResolutionDecision(RecoveryDTO):
    kind: Literal["INTERVAL_RESOLUTION"] = "INTERVAL_RESOLUTION"
    decision: Present
    command: SchedulerCommandIdentity
    original_hold: Present
    original_bound_head: SchedulerIntervalBoundHead
    successor_bound_head: SchedulerIntervalBoundHead
    boundary: SchedulerEligibilityBoundary
    manifest: SchedulerEligibilityManifest
    branch: IntervalBranch
    dispositions: tuple[OccurrenceDisposition, ...]
    materializations: tuple[MaterializationCommitment, ...]
    resulting_boundary: DueCoordinate
    complete_commitment: Digest


class LeaseTransitionCommand(RecoveryDTO):
    identity: SchedulerCommandIdentity
    kind: Literal["ACQUIRE", "RENEW", "TAKEOVER"]
    lineage: ExecutionLineageBinding
    physical_root: PhysicalRootBinding
    observed_lease: ExecutionRootLeaseState
    proposed_holder_id: Identity
    proposed_holder_session_id: Identity
    proposed_lease_id: Identity
    proposed_expiry: UInt64
    proposed_generation: UInt64
    clock_proof: TrustedClockProofRef


class PhysicalRootRolloverCommand(RecoveryDTO):
    identity: SchedulerCommandIdentity
    fence: PhysicalRootRolloverFence


class SchedulerLineageView(RecoveryDTO):
    lineage: ExecutionLineageBinding
    physical_root: PhysicalRootBinding
    lease: ExecutionRootLeaseState
    predecessor_rollover: Annotated[Absent | RolloverPredecessor, Field(discriminator="kind")]


class SchedulerCommitted(RecoveryDTO):
    disposition: Literal["COMMITTED", "REPLAY"]
    command_id: Identity
    journal_decision: Present
    complete_commitment: Digest
    result: (
        ScheduledIntervalDecision
        | SchedulerIntervalResolutionDecision
        | SchedulerOverflowHold
        | SchedulerIntervalBoundHead
        | SchedulerLineageView
    )


class SchedulerRejected(RecoveryDTO):
    disposition: Literal[
        "STALE_HEAD",
        "CHANGED_REPLAY",
        "RIVAL_DISPOSITION",
        "INVALID_MANIFEST",
        "INVALID_CANONICAL_DOMAIN",
        "AUTHORITY_DENIED",
        "CLOCK_UNVERIFIABLE",
        "LEASE_NOT_LIVE",
        "OVERFLOW_HELD",
        "GENERATION_EXHAUSTED",
        "INTEGRITY_HOLD",
        "UNSUPPORTED_SCHEMA_VERSION",
    ]
    command_id: Identity
    reason: Identity


SchedulerResult = SchedulerCommitted | SchedulerRejected


class SchedulerPort(Protocol):
    async def decide_interval(
        self, context: SchedulerContextRef, command: DecideIntervalCommand
    ) -> SchedulerResult: ...

    async def replace_bound(
        self, context: SchedulerContextRef, command: ReplaceIntervalBoundCommand
    ) -> SchedulerResult: ...

    async def resolve_interval(
        self, context: SchedulerContextRef, command: ResolveIntervalCommand
    ) -> SchedulerResult: ...

    async def transition_lease(
        self, context: SchedulerContextRef, command: LeaseTransitionCommand
    ) -> SchedulerResult: ...

    async def rollover(
        self, context: SchedulerContextRef, command: PhysicalRootRolloverCommand
    ) -> SchedulerResult: ...

    def lineage(
        self, context: SchedulerContextRef, subject: ExecutionLineageSubject
    ) -> SchedulerLineageView | SchedulerRejected: ...
