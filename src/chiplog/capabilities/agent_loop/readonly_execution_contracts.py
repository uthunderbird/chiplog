"""Owner-local read-only lineage transitions, separate from consequential acceptance.

These are inert observations and preparation results. The broker independently
captures sources and rechecks the complete lineage, registered proof and current
applicability at the sole writer. A model tool name never proves no mutation.
"""

from typing import Annotated, Literal, Protocol

from pydantic import ConfigDict, Field

from .call_acceptance_contracts import (
    CallAuthorityObservation,
    CallPreparationRejected,
    CallSubjectHead,
    InitializedCallRecord,
)
from .execution_contracts import ExecutionRunRecord
from .recovery_contracts import (
    Absent,
    Digest,
    Identity,
    OriginalObligationBinding,
    Present,
    RecoveryDTO,
    RunExecutionFence,
    UInt64,
    WorkerCommitApplicability,
)
from .recovery_frontier_contracts import (
    PendingCallFrontier,
    ReadOnlyRetryLineage,
    TerminalCallFrontier,
)


class ReadOnlyDTO(RecoveryDTO):
    model_config = ConfigDict(ser_json_bytes="base64", val_json_bytes="base64")


class ConversationHistoryQuery(ReadOnlyDTO):
    """Model arguments only; the broker supplies principal and snapshot authority."""

    limit: int = Field(strict=True, gt=0, le=2**64 - 1)
    after_cursor: Identity | None


class ReadOnlyHistoryToolSpec(RecoveryDTO):
    name: Literal["read_conversation_history"] = "read_conversation_history"
    version: Literal["1"] = "1"
    schema_id: Literal["chiplog.read-conversation-history.v1"] = (
        "chiplog.read-conversation-history.v1"
    )


class ReadOnlyHistoryToolCall(ReadOnlyDTO):
    call_id: Identity
    tool: Literal["read_conversation_history"]
    arguments: ConversationHistoryQuery


class RegisteredReadOnlyProof(ReadOnlyDTO):
    """Registered source preimage; positive no-mutation verification is still required."""

    registry: CallSubjectHead
    tool_schema: CallSubjectHead
    tool_policy: CallSubjectHead
    implementation: CallSubjectHead
    no_mutation_proof: CallSubjectHead
    proof_schema: Identity
    canonical_proof_bytes: bytes = Field(min_length=1)
    query_identity: CallSubjectHead
    canonical_query_bytes: bytes = Field(min_length=1)
    snapshot: CallSubjectHead
    snapshot_frontier: UInt64
    snapshot_contract: CallSubjectHead


class ReadOnlySucceeded(ReadOnlyDTO):
    kind: Literal["SUCCEEDED"] = "SUCCEEDED"
    result: CallSubjectHead
    result_schema: Identity
    canonical_result_bytes: bytes = Field(min_length=1)


class ReadOnlyRetryableFailure(ReadOnlyDTO):
    kind: Literal["FAILED_DEFINITE_RETRYABLE"] = "FAILED_DEFINITE_RETRYABLE"
    result: CallSubjectHead
    error_class: Identity
    reducer_rule: CallSubjectHead
    result_schema: Identity
    canonical_result_bytes: bytes = Field(min_length=1)


class ReadOnlyFinalFailure(ReadOnlyDTO):
    kind: Literal["FAILED_DEFINITE_FINAL"] = "FAILED_DEFINITE_FINAL"
    result: CallSubjectHead
    error_class: Identity
    reducer_rule: CallSubjectHead
    result_schema: Identity
    canonical_result_bytes: bytes = Field(min_length=1)


class ReadOnlyUnknown(ReadOnlyDTO):
    kind: Literal["OUTCOME_UNKNOWN"] = "OUTCOME_UNKNOWN"
    result: CallSubjectHead
    no_retry_boundary: CallSubjectHead
    result_schema: Identity
    canonical_result_bytes: bytes = Field(min_length=1)


class ReadOnlyRecoveryRequired(ReadOnlyDTO):
    kind: Literal["RECOVERY_REQUIRED"] = "RECOVERY_REQUIRED"
    obligation: OriginalObligationBinding
    result: Absent


ReadOnlyAttemptDisposition = Annotated[
    ReadOnlySucceeded
    | ReadOnlyRetryableFailure
    | ReadOnlyFinalFailure
    | ReadOnlyUnknown
    | ReadOnlyRecoveryRequired,
    Field(discriminator="kind"),
]


class ReadOnlyAttemptAcceptedRecord(ReadOnlyDTO):
    """Nonterminal acceptance; no effect intent or invented call-level outcome."""

    schema_id: Literal["chiplog.readonly.attempt-accepted.v1"] = (
        "chiplog.readonly.attempt-accepted.v1"
    )
    original_call_id: Identity
    lineage_id: Identity
    attempt_id: Identity
    ordinal: UInt64
    initialized: CallSubjectHead
    predecessor_attempt: Annotated[Absent | Present, Field(discriminator="kind")]
    active_run: CallSubjectHead
    proof: RegisteredReadOnlyProof
    previous_counter: CallSubjectHead
    next_counter: CallSubjectHead
    fence: RunExecutionFence


class ReadOnlyAttemptOutcomeRecord(ReadOnlyDTO):
    schema_id: Literal["chiplog.readonly.attempt-outcome.v1"] = (
        "chiplog.readonly.attempt-outcome.v1"
    )
    original_call_id: Identity
    lineage_id: Identity
    attempt_id: Identity
    ordinal: UInt64
    accepted: CallSubjectHead
    source_evidence: CallSubjectHead
    disposition: ReadOnlyAttemptDisposition


class ObservedReadOnlyOutcome(ReadOnlyDTO):
    kind: Literal["OBSERVED_OUTCOME"] = "OBSERVED_OUTCOME"
    head: CallSubjectHead
    record: ReadOnlyAttemptOutcomeRecord


class ObservedReadOnlyAttempt(ReadOnlyDTO):
    accepted_head: CallSubjectHead
    accepted: ReadOnlyAttemptAcceptedRecord
    outcome: Annotated[Absent | ObservedReadOnlyOutcome, Field(discriminator="kind")]


class ReadOnlyLineageSnapshot(ReadOnlyDTO):
    """Complete ordered history, never a suffix selected by the current Run."""

    initialized_head: CallSubjectHead
    initialized: InitializedCallRecord
    lineage: ReadOnlyRetryLineage
    complete_ordered_attempts: tuple[ObservedReadOnlyAttempt, ...]
    shared_counter: CallSubjectHead
    attempts_consumed: UInt64
    call_outcome: Annotated[Absent | Present, Field(discriminator="kind")]
    terminal: Annotated[Absent | Present, Field(discriminator="kind")]
    pending: Annotated[Absent | PendingCallFrontier, Field(discriminator="kind")]
    complete_manifest_fingerprint: Digest


class ReadOnlyCurrentCut(ReadOnlyDTO):
    tenant_id: Identity
    database_id: Identity
    tenant_commit_sequence: UInt64
    materialization_commitment: Digest
    authority_registry: CallSubjectHead
    sources: tuple[CallAuthorityObservation, ...] = Field(min_length=1)
    applicability: WorkerCommitApplicability


class SameRunReadOnlyAttempt(ReadOnlyDTO):
    kind: Literal["SAME_RUN"] = "SAME_RUN"
    pending: Absent


class SuccessorReadOnlyAttempt(ReadOnlyDTO):
    kind: Literal["SUCCESSOR_PENDING"] = "SUCCESSOR_PENDING"
    exact_pending: PendingCallFrontier
    predecessor_run: CallSubjectHead
    successor_initialization: CallSubjectHead
    inherited_pending_reference: CallSubjectHead


class PrepareReadOnlyAttempt(ReadOnlyDTO):
    kind: Literal["PREPARE_READONLY_ATTEMPT_V1"] = "PREPARE_READONLY_ATTEMPT_V1"
    command_id: Identity
    run: ExecutionRunRecord
    observed: ReadOnlyLineageSnapshot
    proof: RegisteredReadOnlyProof
    route: Annotated[SameRunReadOnlyAttempt | SuccessorReadOnlyAttempt, Field(discriminator="kind")]
    cut: ReadOnlyCurrentCut
    fence: RunExecutionFence


class PrepareReadOnlyOutcome(ReadOnlyDTO):
    kind: Literal["PREPARE_READONLY_OUTCOME_V1"] = "PREPARE_READONLY_OUTCOME_V1"
    command_id: Identity
    observed: ReadOnlyLineageSnapshot
    accepted_attempt: CallSubjectHead
    source_evidence: CallSubjectHead
    evidence_schema: Identity
    canonical_evidence_bytes: bytes = Field(min_length=1)
    cut: ReadOnlyCurrentCut


class PrepareReadOnlyPending(ReadOnlyDTO):
    kind: Literal["PREPARE_READONLY_PENDING_V1"] = "PREPARE_READONLY_PENDING_V1"
    command_id: Identity
    run: ExecutionRunRecord
    observed: ReadOnlyLineageSnapshot
    current_proof: RegisteredReadOnlyProof
    crossed_binding_heads: tuple[CallSubjectHead, ...] = Field(min_length=1)
    closure_registry: CallSubjectHead
    cut: ReadOnlyCurrentCut
    fence: RunExecutionFence


class PrepareReadOnlyReduction(ReadOnlyDTO):
    kind: Literal["PREPARE_READONLY_REDUCTION_V1"] = "PREPARE_READONLY_REDUCTION_V1"
    command_id: Identity
    observed: ReadOnlyLineageSnapshot
    reducer: CallSubjectHead
    reducer_schema: Identity
    canonical_reducer_bytes: bytes = Field(min_length=1)
    cut: ReadOnlyCurrentCut


ReadOnlyPreparationRequest = Annotated[
    PrepareReadOnlyAttempt
    | PrepareReadOnlyOutcome
    | PrepareReadOnlyPending
    | PrepareReadOnlyReduction,
    Field(discriminator="kind"),
]


class ReadOnlyCounterRecord(ReadOnlyDTO):
    original_call_id: Identity
    lineage_id: Identity
    predecessor: CallSubjectHead
    attempts_consumed: UInt64
    closed: bool


class ReadOnlyPendingTransition(ReadOnlyDTO):
    """One atomic old-head closure and replacement; no branchless intermediate state."""

    previous: PendingCallFrontier
    closure: Literal["NEXT_ATTEMPT_ACCEPTED", "CALL_REDUCED_TERMINAL", "RECOVERY_REQUIRED"]
    replacement: Annotated[PendingCallFrontier | TerminalCallFrontier, Field(discriminator="kind")]


class ReadOnlyCallOutcomeRecord(ReadOnlyDTO):
    original_call_id: Identity
    lineage: ReadOnlyRetryLineage
    complete_ordered_outcomes: tuple[CallSubjectHead, ...] = Field(min_length=1)
    complete_manifest_fingerprint: Digest
    selected_attempt_outcome: CallSubjectHead
    disposition: Literal["SUCCEEDED", "FAILED_DEFINITE", "OUTCOME_UNKNOWN", "RECOVERY_REQUIRED"]
    provenance: Literal["ATTEMPT_RESULT", "FROZEN_BUDGET_EXHAUSTED"]
    result_or_obligation: CallSubjectHead
    terminal_disposition: CallSubjectHead
    closed_counter: CallSubjectHead


class PreparedReadOnlyAttempt(ReadOnlyDTO):
    kind: Literal["PREPARED_READONLY_ATTEMPT_V1"] = "PREPARED_READONLY_ATTEMPT_V1"
    source_request_fingerprint: Digest
    acceptance: ReadOnlyAttemptAcceptedRecord
    counter: ReadOnlyCounterRecord
    pending_transition: ReadOnlyPendingTransition | None
    complete_batch_fingerprint: Digest


class PreparedReadOnlyOutcome(ReadOnlyDTO):
    kind: Literal["PREPARED_READONLY_OUTCOME_V1"] = "PREPARED_READONLY_OUTCOME_V1"
    source_request_fingerprint: Digest
    outcome: ReadOnlyAttemptOutcomeRecord
    complete_batch_fingerprint: Digest


class PreparedReadOnlyPending(ReadOnlyDTO):
    kind: Literal["PREPARED_READONLY_PENDING_V1"] = "PREPARED_READONLY_PENDING_V1"
    source_request_fingerprint: Digest
    pending: PendingCallFrontier
    complete_batch_fingerprint: Digest


class PreparedReadOnlyReduction(ReadOnlyDTO):
    kind: Literal["PREPARED_READONLY_REDUCTION_V1"] = "PREPARED_READONLY_REDUCTION_V1"
    source_request_fingerprint: Digest
    call_outcome: ReadOnlyCallOutcomeRecord
    terminal: TerminalCallFrontier
    counter: ReadOnlyCounterRecord
    pending_transition: ReadOnlyPendingTransition | None
    complete_batch_fingerprint: Digest


ReadOnlyPreparationResult = Annotated[
    PreparedReadOnlyAttempt
    | PreparedReadOnlyOutcome
    | PreparedReadOnlyPending
    | PreparedReadOnlyReduction
    | CallPreparationRejected,
    Field(discriminator="kind"),
]


class ReadOnlyExecutionPreparationPort(Protocol):
    async def prepare_readonly(
        self, request: ReadOnlyPreparationRequest
    ) -> ReadOnlyPreparationResult: ...
