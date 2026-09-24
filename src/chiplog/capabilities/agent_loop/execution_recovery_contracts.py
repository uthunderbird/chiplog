"""Executable suspension, continuation and terminal preparation; no writer authority."""

from typing import Annotated, Literal, Protocol

from pydantic import Field

from .call_acceptance_contracts import CallPreparationRejected, CallSubjectHead
from .execution_contracts import ExecutionRunRecord
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
from .execution_transition_contracts import CreateExecutionRun
from .post_terminal_contracts import PreparedPostTerminalWork
from .recovery_contracts import Digest, Identity, RunExecutionFence, UInt64
from .recovery_frontier_contracts import ObligationObservation, RecoveryProofDisposition


class PrepareExecutionAccounting(ExecutionRecoveryDTO):
    kind: Literal["PREPARE_EXECUTION_ACCOUNTING_V1"] = "PREPARE_EXECUTION_ACCOUNTING_V1"
    command_id: Identity
    cut: ExecutionRecoveryCut


class PrepareExecutionContinuation(ExecutionRecoveryDTO):
    kind: Literal["PREPARE_EXECUTION_CONTINUATION_V1"] = "PREPARE_EXECUTION_CONTINUATION_V1"
    command_id: Identity
    cut: ExecutionRecoveryCut
    complete_accounting: tuple[SealedAccountingRecord, ...]


class PrepareExecutionSuspension(ExecutionRecoveryDTO):
    kind: Literal["PREPARE_EXECUTION_SUSPENSION_V2"] = "PREPARE_EXECUTION_SUSPENSION_V2"
    command_id: Identity
    run: ExecutionRunRecord
    cut: ExecutionRecoveryCut
    activation_blocking_predicates: tuple[CallSubjectHead, ...]
    fence: RunExecutionFence


class PrepareExecutionResume(ExecutionRecoveryDTO):
    kind: Literal["PREPARE_EXECUTION_RESUME_V2"] = "PREPARE_EXECUTION_RESUME_V2"
    command_id: Identity
    run: ExecutionRunRecord
    original_suspension: SelectedExecutionSuspension
    cut: ExecutionRecoveryCut
    disposition_version: Identity
    activation_payload: bytes
    fence: RunExecutionFence


class PrepareExecutionSuccessor(ExecutionRecoveryDTO):
    kind: Literal["PREPARE_EXECUTION_SUCCESSOR_V2"] = "PREPARE_EXECUTION_SUCCESSOR_V2"
    command_id: Identity
    run: ExecutionRunRecord
    original_suspension: SelectedExecutionSuspension
    cut: ExecutionRecoveryCut
    disposition_version: Identity
    successor: CreateExecutionRun
    fence: RunExecutionFence


class PrepareNextExecutionTurn(ExecutionRecoveryDTO):
    """The writer directly recomputes readiness in the TurnStarted transaction."""

    kind: Literal["PREPARE_NEXT_EXECUTION_TURN_V1"] = "PREPARE_NEXT_EXECUTION_TURN_V1"
    command_id: Identity
    run: ExecutionRunRecord
    cut: ExecutionRecoveryCut
    immediately_preceding_response: CallSubjectHead
    expected_current_turn: CallSubjectHead
    next_ordinal: int = Field(strict=True, gt=0, le=2**64 - 1)
    continuation: ContinuationReadyRecord
    fence: RunExecutionFence


class PrepareAbortCancelExecution(ExecutionRecoveryDTO):
    """Weak accounting may include open obligations; work is mandatory in the result."""

    kind: Literal["PREPARE_ABORT_CANCEL_EXECUTION_V1"] = "PREPARE_ABORT_CANCEL_EXECUTION_V1"
    command_id: Identity
    run: ExecutionRunRecord
    target: Literal["ABORTED", "CANCELLED"]
    authenticated_cause: RecoverySourceRecord
    cut: ExecutionRecoveryCut
    complete_accounting: tuple[SealedAccountingRecord, ...]
    fence: RunExecutionFence


ExecutionRecoveryRequest = Annotated[
    PrepareExecutionAccounting
    | PrepareExecutionContinuation
    | PrepareExecutionSuspension
    | PrepareExecutionResume
    | PrepareExecutionSuccessor
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
    run: ExecutionRunRecord
    pair: ExecutionSuspensionPair
    complete_batch_fingerprint: Digest


class PreparedExecutionResume(ExecutionRecoveryDTO):
    kind: Literal["PREPARED_EXECUTION_RESUME_V2"] = "PREPARED_EXECUTION_RESUME_V2"
    source_request_fingerprint: Digest
    original_pair: CallSubjectHead
    run: ExecutionRunRecord
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
    superseded_run: ExecutionRunRecord
    successor_run: ExecutionRunRecord
    edge: ExecutionSuccessorEdge
    lineage_advance: Annotated[
        NonSchedulerSuccessor | SchedulerSuccessorAdvance, Field(discriminator="kind")
    ]
    complete_initialization_bytes: tuple[bytes, ...] = Field(min_length=1)
    complete_batch_fingerprint: Digest


class PreparedNextExecutionTurn(ExecutionRecoveryDTO):
    kind: Literal["PREPARED_NEXT_EXECUTION_TURN_V1"] = "PREPARED_NEXT_EXECUTION_TURN_V1"
    source_request_fingerprint: Digest
    run: ExecutionRunRecord
    continuation: ContinuationReadyRecord
    complete_batch_fingerprint: Digest


class PreparedExecutionTerminal(ExecutionRecoveryDTO):
    kind: Literal["PREPARED_EXECUTION_TERMINAL_V1"] = "PREPARED_EXECUTION_TERMINAL_V1"
    source_request_fingerprint: Digest
    manifest: ExecutionTerminalManifest
    run: ExecutionRunRecord
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
    | PreparedExecutionContinuation
    | PreparedExecutionSuspension
    | PreparedExecutionResume
    | PreparedExecutionSuccessor
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
