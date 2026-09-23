"""Closed recovery observations and commands; no observation grants authority."""

from __future__ import annotations

from typing import Annotated, Literal, Protocol

from pydantic import Field

from chiplog.capabilities.agent_loop.recovery_contracts import (
    Absent,
    Digest,
    HeadMarker,
    Identity,
    NotApplicable,
    OriginalObligationBinding,
    Present,
    RecoveryDTO,
    RunExecutionFence,
    UInt64,
)


class FanOutBound(RecoveryDTO):
    max_call_count: Annotated[int, Field(gt=0, le=2**64 - 1)]
    max_manifest_bytes: Annotated[int, Field(gt=0, le=2**64 - 1)]
    max_serialized_batch_bytes: Annotated[int, Field(gt=0, le=2**64 - 1)]
    canonicalization_version: Literal["chiplog.recovery.frontier.v1"]


class InitializedCall(RecoveryDTO):
    kind: Literal["INITIALIZED"] = "INITIALIZED"
    initialized: Present


class ConsequentialAcceptedCall(RecoveryDTO):
    kind: Literal["CONSEQUENTIAL_ACCEPTED"] = "CONSEQUENTIAL_ACCEPTED"
    initialized: Present
    accepted: Present
    execution_intent: Present
    external_effect_intent: Annotated[NotApplicable | Present, Field(discriminator="kind")]
    complete_acceptance_manifest: tuple[Present, ...] = Field(min_length=2)


class ReadOnlyAcceptedCall(RecoveryDTO):
    kind: Literal["READ_ONLY_ACCEPTED"] = "READ_ONLY_ACCEPTED"
    initialized: Present
    accepted: Present
    lineage_id: Identity
    ordinal: UInt64
    readonly_proof: Present
    snapshot_binding: Present
    lineage: ReadOnlyRetryLineage
    complete_ordered_attempts: tuple[ReadOnlyAttemptMember, ...] = Field(min_length=1)
    shared_counter: Present
    attempts_consumed: UInt64
    call_level_outcome: Present
    complete_lineage_reducer_batch: Present


AcceptanceBranch = Annotated[
    InitializedCall | ConsequentialAcceptedCall | ReadOnlyAcceptedCall, Field(discriminator="kind")
]


class SuccessResult(RecoveryDTO):
    kind: Literal["SUCCEEDED"] = "SUCCEEDED"
    terminal: Present
    result: Present
    recovered_outcome: NotApplicable


class DefiniteFailureResult(RecoveryDTO):
    kind: Literal["FAILED_DEFINITE"] = "FAILED_DEFINITE"
    terminal: Present
    result: Present
    recovered_outcome: NotApplicable


class UnknownResult(RecoveryDTO):
    kind: Literal["OUTCOME_UNKNOWN"] = "OUTCOME_UNKNOWN"
    terminal: Present
    result: Present
    no_retry_boundary: Present
    recovered_outcome: NotApplicable


class CancelledBeforeAcceptResult(RecoveryDTO):
    kind: Literal["CANCELLED_BEFORE_ACCEPT"] = "CANCELLED_BEFORE_ACCEPT"
    terminal: Present
    not_executed_result: Present
    initialized_predecessor: Present
    recovered_outcome: NotApplicable


class RecoveredOutcomeSubject(RecoveryDTO):
    original_call_id: Identity
    recovery_obligation_id: Identity
    outcome: Annotated[Absent | Present, Field(discriminator="kind")]


class RecoveryRequiredResult(RecoveryDTO):
    kind: Literal["RECOVERY_REQUIRED"] = "RECOVERY_REQUIRED"
    terminal: Present
    obligation: OriginalObligationBinding
    result: Absent
    recovered_outcome: RecoveredOutcomeSubject


TerminalResult = Annotated[
    SuccessResult
    | DefiniteFailureResult
    | UnknownResult
    | CancelledBeforeAcceptResult
    | RecoveryRequiredResult,
    Field(discriminator="kind"),
]


class TerminalCallFrontier(RecoveryDTO):
    kind: Literal["TERMINAL"] = "TERMINAL"
    original_call_id: Identity
    response_id: Identity
    acceptance: AcceptanceBranch
    disposition: TerminalResult


class ReadOnlyRetryLineage(RecoveryDTO):
    lineage_id: Identity
    original_call_id: Identity
    max_attempts: Annotated[int, Field(gt=0, le=2**64 - 1)]
    budget_version: Identity
    reducer_id: Identity
    reducer_version: Identity


class ReadOnlyAttemptMember(RecoveryDTO):
    ordinal: UInt64
    attempt_id: Identity
    initialized: Present
    accepted: Present
    outcome: HeadMarker
    result_or_obligation: HeadMarker
    predecessor: Annotated[Absent | Present, Field(discriminator="kind")]


class PendingCallFrontier(RecoveryDTO):
    kind: Literal["READ_ONLY_RETRY_PENDING"] = "READ_ONLY_RETRY_PENDING"
    original_call_id: Identity
    response_id: Identity
    pending: Present
    terminal: Absent
    call_outcome: Absent
    initialized: Present
    lineage: ReadOnlyRetryLineage
    ordered_attempts: tuple[ReadOnlyAttemptMember, ...] = Field(min_length=1)
    last_retryable_failure: Present
    counter: Present
    attempts_consumed: UInt64
    next_ordinal: UInt64
    readonly_proof: Present
    snapshot: Present
    execution_binding: HeadMarker
    crossed_binding_heads: tuple[Present, ...]
    closure_registry_id: Identity
    closure_registry_version: Identity


CallRecoveryFrontier = Annotated[
    TerminalCallFrontier | PendingCallFrontier, Field(discriminator="kind")
]


class RecoveryRegistryRow(RecoveryDTO):
    family: Literal[
        "RUN",
        "TURN",
        "SEALED_RESPONSE",
        "CALL",
        "EFFECT",
        "EVIDENCE",
        "DELIVERY",
        "AUTHORITY",
        "MANDATE",
        "POLICY",
        "PROMPT",
        "TOOL_SCHEMA",
        "RECIPIENT",
        "SEMANTIC_BINDING",
        "EXECUTION_LINEAGE",
        "OBLIGATION",
        "SEMANTIC_REDUCTION",
    ]
    ordinal: UInt64
    subject_extractor_id: Identity
    cardinality_rule: Identity
    terminal_conflict_rule: Identity
    serialization_rule: Identity
    canonicalization_version: Identity


class RecoveryFrontierRegistry(RecoveryDTO):
    registry_id: Identity
    version: Identity
    ordered_rows: tuple[RecoveryRegistryRow, ...] = Field(min_length=1)
    fingerprint: Digest


class FrontierMember(RecoveryDTO):
    family: Identity
    subject: Identity
    branch: Identity
    ordered_heads: tuple[HeadMarker, ...] = Field(min_length=1)
    fingerprint: Digest


class RecoveryFrontier(RecoveryDTO):
    tenant_id: Identity
    run_id: Identity
    tenant_commit_sequence: UInt64
    registry: RecoveryFrontierRegistry
    ordered_members: tuple[FrontierMember, ...] = Field(min_length=1)
    ordered_calls: tuple[CallRecoveryFrontier, ...]
    canonicalization_version: Literal["chiplog.recovery.frontier.v1"]
    fingerprint: Digest


class FrozenRunBindings(RecoveryDTO):
    objective: str
    requested_work: str
    prompt_artifact: Present
    ordered_tool_specs: tuple[Present, ...]
    generated_schema: Present
    semantic_bindings: tuple[Present, ...]
    recipient_effect_bindings: tuple[Present, ...]
    authority_scope: Present
    authority_mandate_heads: tuple[Present, ...]
    policy: Present
    no_retry_boundaries: tuple[Present, ...]


class SuspensionBaseline(RecoveryDTO):
    baseline_id: Identity
    suspended_run_head: Present
    frontier: RecoveryFrontier
    bindings: FrozenRunBindings
    original_obligations: tuple[OriginalObligationBinding, ...]
    activation_blocking_predicates: tuple[Present, ...]
    fingerprint: Digest


class EvidenceReduction(RecoveryDTO):
    kind: Literal["SEMANTIC_EVIDENCE_REDUCTION"] = "SEMANTIC_EVIDENCE_REDUCTION"
    stream_id: Identity
    reduction_id: Identity
    reducer_id: Identity
    reducer_version: Identity
    current_reduction: Present
    ordered_consumed_evidence: tuple[Present, ...] = Field(min_length=1)
    accepted_witness: Present
    accepted_outcome: Present
    obligation_closure: Present
    resolver_batch: Present
    semantic_class: Identity
    consumability: Literal["CONSUMABLE", "HOLD"]


class ContinuationMember(RecoveryDTO):
    original_call_id: Identity
    terminal_frontier: TerminalCallFrontier
    reduction: Annotated[NotApplicable | EvidenceReduction, Field(discriminator="kind")]


class ReferenceExternalObligation(RecoveryDTO):
    kind: Literal["REFERENCE_EXTERNAL"] = "REFERENCE_EXTERNAL"
    original: OriginalObligationBinding
    observation_frontier: UInt64


class ClosedBeforeTransition(RecoveryDTO):
    kind: Literal["CLOSED_BEFORE_TRANSITION"] = "CLOSED_BEFORE_TRANSITION"
    original: OriginalObligationBinding
    terminal: Present
    observation_frontier: UInt64


ObligationObservation = Annotated[
    ReferenceExternalObligation | ClosedBeforeTransition, Field(discriminator="kind")
]


class RecoveryCommandIdentity(RecoveryDTO):
    tenant_id: Identity
    command_id: Identity
    payload_fingerprint: Digest
    schema_version: Literal["chiplog.recovery.command.v1"]


class ResumeCommand(RecoveryDTO):
    kind: Literal["RESUME"] = "RESUME"
    identity: RecoveryCommandIdentity
    run_id: Identity
    expected_suspended_head: Present
    baseline: SuspensionBaseline
    observed_frontier: RecoveryFrontier
    disposition_version: Identity
    activation_payload: str
    fence: RunExecutionFence


class SuccessorCommand(RecoveryDTO):
    kind: Literal["SUCCESSOR"] = "SUCCESSOR"
    identity: RecoveryCommandIdentity
    predecessor_run_id: Identity
    expected_suspended_head: Present
    baseline: SuspensionBaseline
    observed_frontier: RecoveryFrontier
    disposition_version: Identity
    changed_binding_manifest: tuple[Present, ...] = Field(min_length=1)
    successor_run_id: Identity
    successor_initialization_manifest: tuple[Present, ...] = Field(min_length=1)
    original_obligation_observations: tuple[ObligationObservation, ...]
    inherited_no_retry_boundaries: tuple[Present, ...]
    fence: RunExecutionFence


RecoveryCommand = Annotated[ResumeCommand | SuccessorCommand, Field(discriminator="kind")]
RecoveryProofDisposition = Literal[
    "SAME_RUN", "SUCCESSOR_REQUIRED", "RECOVERY_HOLD", "TERMINAL_RECOVERY_FAULT"
]


class RecoveryPublished(RecoveryDTO):
    kind: Literal["PUBLISHED", "REPLAY"]
    command_id: Identity
    decision: Present
    complete_members: tuple[Present, ...] = Field(min_length=1)


class RecoveryRejected(RecoveryDTO):
    kind: Literal["HOLD", "STALE", "CONFLICT", "FAULT", "DENIED"]
    command_id: Identity
    reason: Identity


RecoveryResult = RecoveryPublished | RecoveryRejected


class RecoveryPort(Protocol):
    def frontier(self, run_id: str) -> RecoveryFrontier: ...

    async def resume(self, command: ResumeCommand) -> RecoveryResult: ...

    async def successor(self, command: SuccessorCommand) -> RecoveryResult: ...
