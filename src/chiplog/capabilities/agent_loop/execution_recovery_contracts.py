"""Executable suspension, continuation and terminal preparation; no writer authority."""

from typing import Annotated, Literal, Protocol

from pydantic import Field

from .call_acceptance_contracts import CallPreparationRejected, CallSubjectHead
from .execution_initialization_contracts import SelectedAdmittedRunInput
from .execution_recovery_observations import (
    ContinuationReadyRecord,
    ExecutionRecoveryCut,
    ExecutionRecoveryDTO,
    ExecutionSuspensionBaseline,
    ExecutionSuspensionPair,
    ExecutionTerminalManifest,
    RecoverySourceRecord,
    SealedAccountingRecord,
    SelectedExecutionSuspension,
)
from .execution_run_versions import CreateExecutionRunVersion, ExecutionRun
from .post_terminal_contracts import PreparedPostTerminalWork
from .recovery_contracts import (
    Digest,
    Identity,
    OriginalObligationBinding,
    RunExecutionFence,
    UInt64,
)
from .recovery_frontier_contracts import ObligationObservation, RecoveryProofDisposition


class PrepareExecutionAccounting(ExecutionRecoveryDTO):
    kind: Literal["PREPARE_EXECUTION_ACCOUNTING_V1"] = "PREPARE_EXECUTION_ACCOUNTING_V1"
    command_id: Identity
    cut: ExecutionRecoveryCut


class AcceptedOpenRecoveryCutV1(ExecutionRecoveryDTO):
    """Effects-specific R14 recovery cut; never projects R16 into generic bindings."""

    schema_id: Literal["chiplog.execution.accepted-open-recovery-cut.v1"] = (
        "chiplog.execution.accepted-open-recovery-cut.v1"
    )
    tenant_id: Identity
    database_id: Identity
    tenant_commit_sequence: UInt64
    materialization_commitment: Digest
    current_run: CallSubjectHead
    current_run_bytes: bytes = Field(min_length=1)
    response_id: Identity
    original_call_id: Identity
    initialized: CallSubjectHead
    accepted: CallSubjectHead
    accepted_record_bytes: bytes = Field(min_length=1)
    execution_intent: CallSubjectHead
    execution_intent_record_bytes: bytes = Field(min_length=1)
    external_effect_intent: CallSubjectHead
    selected_outcome_record_id: Identity
    selected_outcome_bytes: bytes = Field(min_length=1)
    selected_outcome_fingerprint: Digest
    selected_decision: CallSubjectHead
    outcome_commit_sequence: UInt64
    publication_ordinal: UInt64
    batch_record_ids: tuple[Identity, ...] = Field(min_length=1)
    cut_fingerprint: Digest


class PrepareAcceptedOpenAccounting(ExecutionRecoveryDTO):
    kind: Literal["PREPARE_ACCEPTED_OPEN_ACCOUNTING_V1"] = "PREPARE_ACCEPTED_OPEN_ACCOUNTING_V1"
    command_id: Identity
    cut: AcceptedOpenRecoveryCutV1


class AcceptedOpenAccountingRecordV1(ExecutionRecoveryDTO):
    """Durable R14 weak accounting for one exact selected R16 OPEN obligation."""

    schema_id: Literal["chiplog.execution.accepted-open-accounting.v1"] = (
        "chiplog.execution.accepted-open-accounting.v1"
    )
    kind: Literal["RECOVERY_REQUIRED"] = "RECOVERY_REQUIRED"
    accounting_id: Identity
    source_cut_fingerprint: Digest
    original_call_id: Identity
    accepted: CallSubjectHead
    execution_intent: CallSubjectHead
    external_effect_intent: CallSubjectHead
    selected_outcome_record_id: Identity
    selected_outcome_bytes: bytes = Field(min_length=1)
    selected_outcome_fingerprint: Digest
    selected_decision: CallSubjectHead
    outcome_commit_sequence: UInt64
    publication_ordinal: UInt64
    batch_record_ids: tuple[Identity, ...] = Field(min_length=1)


class PreparedAcceptedOpenAccounting(ExecutionRecoveryDTO):
    kind: Literal["PREPARED_ACCEPTED_OPEN_ACCOUNTING_V1"] = "PREPARED_ACCEPTED_OPEN_ACCOUNTING_V1"
    source_request_fingerprint: Digest
    accounting: AcceptedOpenAccountingRecordV1


class AcceptedOpenRecoveryOpeningV1(ExecutionRecoveryDTO):
    """Loop-owned original obligation opened for one accepted uncertain call."""

    schema_id: Literal["chiplog.execution.accepted-open-recovery-opening.v1"] = (
        "chiplog.execution.accepted-open-recovery-opening.v1"
    )
    opening_id: Identity
    source_cut_fingerprint: Digest
    original: OriginalObligationBinding
    evidence_sought: tuple[CallSubjectHead, ...]
    safe_actions: tuple[Identity, ...]
    denied_actions: tuple[Identity, ...]


class AcceptedOpenTerminalDispositionV1(ExecutionRecoveryDTO):
    """Immutable terminal fact; it never implies model continuation."""

    schema_id: Literal["chiplog.execution.accepted-open-terminal-disposition.v1"] = (
        "chiplog.execution.accepted-open-terminal-disposition.v1"
    )
    kind: Literal["RECOVERY_REQUIRED"] = "RECOVERY_REQUIRED"
    terminal_id: Identity
    source_cut_fingerprint: Digest
    original_call_id: Identity
    response_id: Identity
    accepted: CallSubjectHead
    external_effect_intent: CallSubjectHead
    obligation_opening: CallSubjectHead


class PrepareAcceptedOpenRecoveryV1(ExecutionRecoveryDTO):
    kind: Literal["PREPARE_ACCEPTED_OPEN_RECOVERY_V1"] = "PREPARE_ACCEPTED_OPEN_RECOVERY_V1"
    command_id: Identity
    cut: AcceptedOpenRecoveryCutV1


class PreparedAcceptedOpenRecoveryV1(ExecutionRecoveryDTO):
    kind: Literal["PREPARED_ACCEPTED_OPEN_RECOVERY_V1"] = "PREPARED_ACCEPTED_OPEN_RECOVERY_V1"
    source_request_fingerprint: Digest
    accounting: AcceptedOpenAccountingRecordV1
    opening: AcceptedOpenRecoveryOpeningV1
    terminal: AcceptedOpenTerminalDispositionV1


class PrepareExecutionContinuation(ExecutionRecoveryDTO):
    kind: Literal["PREPARE_EXECUTION_CONTINUATION_V1"] = "PREPARE_EXECUTION_CONTINUATION_V1"
    command_id: Identity
    cut: ExecutionRecoveryCut
    complete_accounting: tuple[SealedAccountingRecord, ...]


class PrepareExecutionSuspension(ExecutionRecoveryDTO):
    kind: Literal["PREPARE_EXECUTION_SUSPENSION_V2"] = "PREPARE_EXECUTION_SUSPENSION_V2"
    command_id: Identity
    run: ExecutionRun
    cut: ExecutionRecoveryCut
    activation_blocking_predicates: tuple[CallSubjectHead, ...]
    fence: RunExecutionFence


class PrepareExecutionResume(ExecutionRecoveryDTO):
    kind: Literal["PREPARE_EXECUTION_RESUME_V2"] = "PREPARE_EXECUTION_RESUME_V2"
    command_id: Identity
    run: ExecutionRun
    original_suspension: SelectedExecutionSuspension
    cut: ExecutionRecoveryCut
    disposition_version: Identity
    activation_payload: bytes
    fence: RunExecutionFence


class PrepareExecutionSuccessor(ExecutionRecoveryDTO):
    """Inert successor proposal; admitted_input is a consistency hint only.

    The owner must independently reselect and authenticate the ingress and must
    derive the scheduler branch from trusted predecessor/job state before publish.
    """

    kind: Literal["PREPARE_EXECUTION_SUCCESSOR_V2"] = "PREPARE_EXECUTION_SUCCESSOR_V2"
    command_id: Identity
    run: ExecutionRun
    original_suspension: SelectedExecutionSuspension
    cut: ExecutionRecoveryCut
    disposition_version: Identity
    successor: CreateExecutionRunVersion
    fence: RunExecutionFence
    admitted_input: SelectedAdmittedRunInput | None = None


class PrepareExecutionSuccessorStart(ExecutionRecoveryDTO):
    """Evidence-bound first Turn transition for one selected CREATED successor."""

    kind: Literal["PREPARE_EXECUTION_SUCCESSOR_START_V1"] = (
        "PREPARE_EXECUTION_SUCCESSOR_START_V1"
    )
    command_id: Identity
    source_request: PrepareExecutionSuccessor
    created_run: ExecutionRun
    edge_bytes: bytes = Field(min_length=1)
    initialization_bytes: tuple[bytes, ...] = Field(min_length=1)


class SelectedContinuationResultStamp(ExecutionRecoveryDTO):
    """Inert claimed result of one selected accounting/readiness publication."""

    selected_decision: CallSubjectHead
    predecessor_tenant_commit_sequence: UInt64
    predecessor_materialization_commitment: Digest
    resulting_tenant_commit_sequence: UInt64
    resulting_materialization_commitment: Digest
    complete_batch_fingerprint: Digest


class SelectedContinuationPublication(ExecutionRecoveryDTO):
    """Exact historical C and selected pair; the broker authenticates this bridge."""

    schema_id: Literal["chiplog.execution.selected-continuation-publication.v1"] = (
        "chiplog.execution.selected-continuation-publication.v1"
    )
    source_cut: ExecutionRecoveryCut | None = None
    accounting_source: RecoverySourceRecord
    readiness_source: RecoverySourceRecord
    selected_decision: CallSubjectHead
    result_stamp: SelectedContinuationResultStamp


class PrepareNextExecutionTurn(ExecutionRecoveryDTO):
    """The writer directly recomputes readiness in the TurnStarted transaction."""

    kind: Literal["PREPARE_NEXT_EXECUTION_TURN_V1"] = "PREPARE_NEXT_EXECUTION_TURN_V1"
    command_id: Identity
    run: ExecutionRun
    cut: ExecutionRecoveryCut
    immediately_preceding_response: CallSubjectHead
    expected_current_turn: CallSubjectHead
    next_ordinal: int = Field(strict=True, gt=0, le=2**64 - 1)
    continuation: ContinuationReadyRecord
    selected_publication: SelectedContinuationPublication | None = None
    fence: RunExecutionFence


class PrepareAbortCancelExecution(ExecutionRecoveryDTO):
    """Weak accounting may include open obligations; work is mandatory in the result."""

    kind: Literal["PREPARE_ABORT_CANCEL_EXECUTION_V1"] = "PREPARE_ABORT_CANCEL_EXECUTION_V1"
    command_id: Identity
    run: ExecutionRun
    target: Literal["ABORTED", "CANCELLED"]
    authenticated_cause: RecoverySourceRecord
    cut: ExecutionRecoveryCut
    complete_accounting: tuple[SealedAccountingRecord, ...]
    fence: RunExecutionFence


ExecutionRecoveryRequest = Annotated[
    PrepareExecutionAccounting
    | PrepareAcceptedOpenAccounting
    | PrepareAcceptedOpenRecoveryV1
    | PrepareExecutionContinuation
    | PrepareExecutionSuspension
    | PrepareExecutionResume
    | PrepareExecutionSuccessor
    | PrepareExecutionSuccessorStart
    | PrepareNextExecutionTurn
    | PrepareAbortCancelExecution,
    Field(discriminator="kind"),
]


class PreparedExecutionAccounting(ExecutionRecoveryDTO):
    kind: Literal["PREPARED_EXECUTION_ACCOUNTING_V1"] = "PREPARED_EXECUTION_ACCOUNTING_V1"
    source_request_fingerprint: Digest
    complete_accounting: tuple[SealedAccountingRecord, ...]


class PreparedExecutionContinuation(ExecutionRecoveryDTO):
    kind: Literal["PREPARED_EXECUTION_CONTINUATION_V1"] = "PREPARED_EXECUTION_CONTINUATION_V1"
    source_request_fingerprint: Digest
    complete_continuations: tuple[ContinuationReadyRecord, ...]


class PreparedExecutionSuspension(ExecutionRecoveryDTO):
    kind: Literal["PREPARED_EXECUTION_SUSPENSION_V2"] = "PREPARED_EXECUTION_SUSPENSION_V2"
    source_request_fingerprint: Digest
    baseline: ExecutionSuspensionBaseline
    run: ExecutionRun
    pair: ExecutionSuspensionPair
    complete_batch_fingerprint: Digest


class PreparedExecutionResume(ExecutionRecoveryDTO):
    kind: Literal["PREPARED_EXECUTION_RESUME_V2"] = "PREPARED_EXECUTION_RESUME_V2"
    source_request_fingerprint: Digest
    original_pair: CallSubjectHead
    run: ExecutionRun
    complete_batch_fingerprint: Digest


class NonSchedulerSuccessor(ExecutionRecoveryDTO):
    kind: Literal["NON_SCHEDULER"] = "NON_SCHEDULER"


class SchedulerSuccessorAdvance(ExecutionRecoveryDTO):
    """Complete owner-produced lineage/selected-epoch records; never broker reconstruction."""

    kind: Literal["SCHEDULER_LINEAGE_ADVANCE"] = "SCHEDULER_LINEAGE_ADVANCE"
    original_lineage: CallSubjectHead
    current_selector: CallSubjectHead
    selected_epoch: CallSubjectHead
    lineage_schema: Identity
    canonical_lineage_bytes: bytes = Field(min_length=1)
    epoch_observation_schema: Identity
    canonical_epoch_observation_bytes: bytes = Field(min_length=1)


class ExecutionSuccessorEdge(ExecutionRecoveryDTO):
    command_id: Identity
    original_pair: CallSubjectHead
    predecessor_before: CallSubjectHead
    predecessor_superseded: CallSubjectHead
    successor_created: CallSubjectHead
    source_cut_fingerprint: Digest
    disposition_version: Identity
    changed_binding_manifest: tuple[CallSubjectHead, ...] = Field(min_length=1)
    complete_initialization: tuple[CallSubjectHead, ...] = Field(min_length=1)
    original_obligations: tuple[ObligationObservation, ...]
    inherited_no_retry_boundaries: tuple[CallSubjectHead, ...]
    inherited_pending_branches: tuple[CallSubjectHead, ...]
    observation_frontier: UInt64
    fence: RunExecutionFence


class PreparedExecutionSuccessor(ExecutionRecoveryDTO):
    kind: Literal["PREPARED_EXECUTION_SUCCESSOR_V2"] = "PREPARED_EXECUTION_SUCCESSOR_V2"
    source_request_fingerprint: Digest
    superseded_run: ExecutionRun
    successor_run: ExecutionRun
    edge: ExecutionSuccessorEdge
    lineage_advance: Annotated[
        NonSchedulerSuccessor | SchedulerSuccessorAdvance, Field(discriminator="kind")
    ]
    complete_initialization_bytes: tuple[bytes, ...] = Field(min_length=1)
    complete_batch_fingerprint: Digest


class PreparedExecutionSuccessorStart(ExecutionRecoveryDTO):
    kind: Literal["PREPARED_EXECUTION_SUCCESSOR_START_V1"] = (
        "PREPARED_EXECUTION_SUCCESSOR_START_V1"
    )
    source_request_fingerprint: Digest
    run: ExecutionRun
    complete_batch_fingerprint: Digest


class PreparedNextExecutionTurn(ExecutionRecoveryDTO):
    kind: Literal["PREPARED_NEXT_EXECUTION_TURN_V1"] = "PREPARED_NEXT_EXECUTION_TURN_V1"
    source_request_fingerprint: Digest
    run: ExecutionRun
    continuation: ContinuationReadyRecord
    complete_batch_fingerprint: Digest


class PreparedExecutionTerminal(ExecutionRecoveryDTO):
    kind: Literal["PREPARED_EXECUTION_TERMINAL_V1"] = "PREPARED_EXECUTION_TERMINAL_V1"
    source_request_fingerprint: Digest
    manifest: ExecutionTerminalManifest
    run: ExecutionRun
    work: PreparedPostTerminalWork
    complete_batch_fingerprint: Digest


class ExecutionRecoveryClassified(ExecutionRecoveryDTO):
    """Classification is an observation; it does not authorize a writer transition."""

    kind: Literal["EXECUTION_RECOVERY_CLASSIFIED_V1"] = "EXECUTION_RECOVERY_CLASSIFIED_V1"
    source_request_fingerprint: Digest
    disposition: RecoveryProofDisposition
    reason: Identity


ExecutionRecoveryResult = Annotated[
    PreparedExecutionAccounting
    | PreparedAcceptedOpenAccounting
    | PreparedAcceptedOpenRecoveryV1
    | PreparedExecutionContinuation
    | PreparedExecutionSuspension
    | PreparedExecutionResume
    | PreparedExecutionSuccessor
    | PreparedExecutionSuccessorStart
    | PreparedNextExecutionTurn
    | PreparedExecutionTerminal
    | ExecutionRecoveryClassified
    | CallPreparationRejected,
    Field(discriminator="kind"),
]


class ExecutionRecoveryPreparationPort(Protocol):
    async def prepare_recovery(
        self, request: ExecutionRecoveryRequest
    ) -> ExecutionRecoveryResult: ...
