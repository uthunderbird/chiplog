"""Loop-owned original-stream recovery; selected foreign records are inputs only.

Preparation authenticates nothing. The broker captures actual owner records and
the registered writer rechecks ownership, complete evidence and exact CAS heads.
Evidence append is a prior independent commit, never part of these results.
"""

from typing import Annotated, Literal, Protocol

from pydantic import Field

from .call_acceptance_contracts import CallPreparationRejected, CallSubjectHead
from .execution_recovery_observations import ExecutionRecoveryDTO, RecoverySourceRecord
from .recovery_contracts import (
    Absent,
    Digest,
    Identity,
    OriginalObligationBinding,
    Present,
    UInt64,
    WorkerCommitApplicability,
)


class LoopRecoveryStream(ExecutionRecoveryDTO):
    owner: Literal["agent_loop"] = "agent_loop"
    stream_id: Identity
    registry: CallSubjectHead
    subject: CallSubjectHead
    schema_id: Identity


class IndependentResolverAuthority(ExecutionRecoveryDTO):
    kind: Literal["INDEPENDENT_RESOLVER"] = "INDEPENDENT_RESOLVER"
    registered_authority: RecoverySourceRecord
    authenticated_invocation: RecoverySourceRecord


class WorkerResolverAuthority(ExecutionRecoveryDTO):
    kind: Literal["WORKER_RESOLVER"] = "WORKER_RESOLVER"
    registered_authority: RecoverySourceRecord
    applicability: WorkerCommitApplicability


ResolverAuthority = Annotated[
    IndependentResolverAuthority | WorkerResolverAuthority, Field(discriminator="kind")
]


class OriginalResolverCut(ExecutionRecoveryDTO):
    tenant_id: Identity
    database_id: Identity
    tenant_commit_sequence: UInt64
    materialization_commitment: Digest
    original: OriginalObligationBinding
    obligation_stream: LoopRecoveryStream
    current_obligation: RecoverySourceRecord
    call_terminal: RecoverySourceRecord
    complete_ordered_evidence: tuple[RecoverySourceRecord, ...]
    complete_selected_foreign_reductions: tuple[RecoverySourceRecord, ...]
    expected_closure: Annotated[Absent | Present, Field(discriminator="kind")]
    expected_recovered_outcome: Annotated[Absent | Present, Field(discriminator="kind")]
    resolver_policy: RecoverySourceRecord
    reducer_policy: RecoverySourceRecord
    authority: ResolverAuthority


class PrepareOriginalCallResolution(ExecutionRecoveryDTO):
    kind: Literal["PREPARE_ORIGINAL_CALL_RESOLUTION_V1"] = "PREPARE_ORIGINAL_CALL_RESOLUTION_V1"
    command_id: Identity
    cut: OriginalResolverCut


class OriginalResolutionBasis(ExecutionRecoveryDTO):
    """Primitive hash domain; no output closure/outcome/batch hash feeds back here."""

    basis_id: Identity
    source_request_fingerprint: Digest
    original: OriginalObligationBinding
    original_terminal: CallSubjectHead
    selected_witness: CallSubjectHead
    complete_evidence_manifest: tuple[CallSubjectHead, ...] = Field(min_length=1)
    resolver_policy: CallSubjectHead
    reducer_policy: CallSubjectHead
    reduction_id: Identity
    accepted_semantic_class: Identity


class OriginalObligationClosureRecord(ExecutionRecoveryDTO):
    schema_id: Literal["chiplog.loop.original-obligation-closure.v1"] = (
        "chiplog.loop.original-obligation-closure.v1"
    )
    closure_id: Identity
    basis: CallSubjectHead
    original: OriginalObligationBinding
    prior_obligation: CallSubjectHead
    accepted_witness: CallSubjectHead
    closure_predicate: CallSubjectHead


class RecoveredCallOutcomeRecord(ExecutionRecoveryDTO):
    schema_id: Literal["chiplog.loop.recovered-call-outcome.v1"] = (
        "chiplog.loop.recovered-call-outcome.v1"
    )
    outcome_id: Identity
    basis: CallSubjectHead
    original: OriginalObligationBinding
    closure: CallSubjectHead
    accepted_witness: CallSubjectHead
    result_schema: Identity
    canonical_result_bytes: bytes = Field(min_length=1)
    accepted_semantic_class: Identity


class OriginalResolverBatchRecord(ExecutionRecoveryDTO):
    batch_id: Identity
    command_id: Identity
    basis: CallSubjectHead
    closure: CallSubjectHead
    recovered_outcome: CallSubjectHead
    source_cut_fingerprint: Digest


class PreparedOriginalCallResolution(ExecutionRecoveryDTO):
    kind: Literal["PREPARED_ORIGINAL_CALL_RESOLUTION_V1"] = "PREPARED_ORIGINAL_CALL_RESOLUTION_V1"
    source_request_fingerprint: Digest
    basis: OriginalResolutionBasis
    closure: OriginalObligationClosureRecord
    recovered_outcome: RecoveredCallOutcomeRecord
    batch: OriginalResolverBatchRecord
    complete_batch_fingerprint: Digest


class UnresolvedReductionAnchor(ExecutionRecoveryDTO):
    kind: Literal["UNRESOLVED"] = "UNRESOLVED"
    original: OriginalObligationBinding
    closure: Absent
    recovered_outcome: Absent


class ResolvedReductionAnchor(ExecutionRecoveryDTO):
    kind: Literal["RESOLVED"] = "RESOLVED"
    original: OriginalObligationBinding
    accepted_witness: CallSubjectHead
    recovered_outcome: CallSubjectHead
    closure: CallSubjectHead
    resolver_batch: CallSubjectHead
    accepted_semantic_class: Identity


ReductionAnchor = Annotated[
    UnresolvedReductionAnchor | ResolvedReductionAnchor, Field(discriminator="kind")
]


class ConsumableReduction(ExecutionRecoveryDTO):
    kind: Literal["CONSUMABLE"] = "CONSUMABLE"
    semantic_class: Identity


class HeldReduction(ExecutionRecoveryDTO):
    kind: Literal["HOLD"] = "HOLD"
    reason: Literal[
        "INCOMPLETE",
        "RIVAL",
        "CONTRADICTORY",
        "MEANING_CHANGED",
        "UNCLASSIFIED",
        "REGISTRY_CHANGED",
    ]
    conflicting_or_unclassified_evidence: tuple[CallSubjectHead, ...]


class LoopSemanticReductionRecord(ExecutionRecoveryDTO):
    """Appendable producer record; its current head is external, avoiding self-hashing.

    UNRESOLVED can classify evidence without inventing closure/outcome. Even a
    consumable evidence class cannot satisfy continuation without resolved anchors.
    """

    schema_id: Literal["chiplog.loop.semantic-reduction.v1"] = "chiplog.loop.semantic-reduction.v1"
    stream: LoopRecoveryStream
    reduction_id: Identity
    reducer_id: Identity
    reducer_version: Identity
    predecessor: Annotated[Absent | Present, Field(discriminator="kind")]
    complete_ordered_evidence: tuple[CallSubjectHead, ...]
    anchor: ReductionAnchor
    disposition: Annotated[ConsumableReduction | HeldReduction, Field(discriminator="kind")]


class SelectedLoopSemanticReduction(ExecutionRecoveryDTO):
    kind: Literal["SELECTED_LOOP_REDUCTION"] = "SELECTED_LOOP_REDUCTION"
    record: LoopSemanticReductionRecord
    source: RecoverySourceRecord


class PrepareLoopSemanticReduction(ExecutionRecoveryDTO):
    kind: Literal["PREPARE_LOOP_SEMANTIC_REDUCTION_V1"] = "PREPARE_LOOP_SEMANTIC_REDUCTION_V1"
    command_id: Identity
    tenant_id: Identity
    database_id: Identity
    tenant_commit_sequence: UInt64
    materialization_commitment: Digest
    stream: LoopRecoveryStream
    expected: Annotated[Absent | SelectedLoopSemanticReduction, Field(discriminator="kind")]
    original: OriginalObligationBinding
    complete_ordered_evidence: tuple[RecoverySourceRecord, ...]
    complete_anchor_sources: tuple[RecoverySourceRecord, ...]
    reducer_policy: RecoverySourceRecord
    authority: ResolverAuthority


class PreparedLoopSemanticReduction(ExecutionRecoveryDTO):
    kind: Literal["PREPARED_LOOP_SEMANTIC_REDUCTION_V1"] = "PREPARED_LOOP_SEMANTIC_REDUCTION_V1"
    source_request_fingerprint: Digest
    record: LoopSemanticReductionRecord
    complete_batch_fingerprint: Digest


OriginalRecoveryRequest = Annotated[
    PrepareOriginalCallResolution | PrepareLoopSemanticReduction, Field(discriminator="kind")
]
OriginalRecoveryResult = Annotated[
    PreparedOriginalCallResolution | PreparedLoopSemanticReduction | CallPreparationRejected,
    Field(discriminator="kind"),
]


class OriginalLoopRecoveryPreparationPort(Protocol):
    async def prepare_original_recovery(
        self, request: OriginalRecoveryRequest
    ) -> OriginalRecoveryResult: ...
