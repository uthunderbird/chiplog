"""Create executable Runs from original admitted or scheduler-owned inputs.

The broker retains and independently authenticates these source bytes. The owner
verifies the complete origin/creation relationship; construction authenticates
nothing. No broker-private DTO or legacy RunRecord crosses this owner port.
"""

from typing import Annotated, Literal, Protocol

from pydantic import ConfigDict, Field

from .call_acceptance_contracts import (
    CallAuthorityObservation,
    CallPreparationRejected,
    CallSubjectHead,
)
from .delivery_contracts import OriginSelection
from .execution_run_versions import CreateExecutionRunVersion, ExecutionRun
from .recovery_contracts import Absent, Digest, Identity, PreRootDecisionFence, RecoveryDTO, UInt64
from .scheduler_contracts import MaterializationCommitment


class ExecutionInputDTO(RecoveryDTO):
    model_config = ConfigDict(ser_json_bytes="base64", val_json_bytes="base64")


class SelectedAdmittedRunInput(ExecutionInputDTO):
    """A selected command inbox, not evidence feedback that implicitly starts a Run.

    Provider/reconciliation/tool-result ingress attaches to original subjects through
    its own evidence route. Only authenticated CLI/Telegram commands use this branch.
    Raw bytes and normalization are independently selected, never regenerated merely
    because their text is equal to a prior command.
    """

    kind: Literal["SELECTED_ADMITTED_RUN_INPUT_V1"] = "SELECTED_ADMITTED_RUN_INPUT_V1"
    tenant_id: Identity
    database_id: Identity
    source_class: Literal["CLI", "TELEGRAM_PUSH", "TELEGRAM_POLL"]
    source_contract: CallSubjectHead
    token: CallSubjectHead
    custody: CallSubjectHead
    inbox: CallSubjectHead
    selected_decision: CallSubjectHead
    physical_record: CallSubjectHead
    commit_sequence: UInt64
    raw_input_bytes: bytes
    custody_schema: Identity
    canonical_custody_record: bytes = Field(min_length=1)
    inbox_schema: Identity
    canonical_inbox_record: bytes = Field(min_length=1)
    source_authentication: CallSubjectHead
    authentication_schema: Identity
    canonical_authentication: bytes = Field(min_length=1)
    normalization: CallSubjectHead
    normalization_schema: Identity
    canonical_normalization_record: bytes = Field(min_length=1)
    normalized_prompt: Identity
    principal_id: Identity
    contour_head: Identity
    origin: OriginSelection


class ExecutionInitializationCut(RecoveryDTO):
    """Fresh creation cut; exact replay is looked up before owner preparation."""

    tenant_id: Identity
    database_id: Identity
    tenant_commit_sequence: UInt64
    materialization_commitment: Digest
    initial_run_absence: Absent
    worker_session_id: Identity
    runtime_generation: Identity
    authority_registry: CallSubjectHead
    sources: tuple[CallAuthorityObservation, ...] = Field(min_length=1)


class PrepareInboxExecution(ExecutionInputDTO):
    kind: Literal["PREPARE_INBOX_EXECUTION_V1"] = "PREPARE_INBOX_EXECUTION_V1"
    create: CreateExecutionRunVersion
    admitted: SelectedAdmittedRunInput
    cut: ExecutionInitializationCut


class SchedulerExecutionSource(ExecutionInputDTO):
    """Pre-root decision inputs; the complete scheduler/Run batch is selected once.

    First publication, exact-prefix materialization and selected replay retain their
    existing tagged dispositions. Source refs do not assert prior selection where the
    same atomic batch is about to create the decision and the initial Run together.
    """

    configuration: CallSubjectHead
    configuration_schema: Identity
    canonical_configuration: bytes = Field(min_length=1)
    decision: CallSubjectHead
    decision_schema: Identity
    canonical_decision: bytes = Field(min_length=1)
    materialization: MaterializationCommitment
    pre_root_fence: PreRootDecisionFence


class PrepareScheduledExecution(ExecutionInputDTO):
    kind: Literal["PREPARE_SCHEDULED_EXECUTION_V1"] = "PREPARE_SCHEDULED_EXECUTION_V1"
    create: CreateExecutionRunVersion
    source: SchedulerExecutionSource
    cut: ExecutionInitializationCut


ExecutionInitializationRequest = Annotated[
    PrepareInboxExecution | PrepareScheduledExecution, Field(discriminator="kind")
]


class AdmittedExecutionBinding(RecoveryDTO):
    kind: Literal["ADMITTED_INPUT"] = "ADMITTED_INPUT"
    original_inbox: CallSubjectHead
    original_custody: CallSubjectHead
    normalization: CallSubjectHead


class ScheduledExecutionBinding(RecoveryDTO):
    kind: Literal["SCHEDULER_MATERIALIZATION"] = "SCHEDULER_MATERIALIZATION"
    original_configuration: CallSubjectHead
    original_decision: CallSubjectHead
    materialization_id: Identity


class PreparedExecutionInitialization(RecoveryDTO):
    kind: Literal["PREPARED_EXECUTION_INITIALIZATION_V1"] = "PREPARED_EXECUTION_INITIALIZATION_V1"
    source_request_fingerprint: Digest
    run: ExecutionRun
    input_binding: Annotated[
        AdmittedExecutionBinding | ScheduledExecutionBinding, Field(discriminator="kind")
    ]
    proposal_fingerprint: Digest


ExecutionInitializationResult = Annotated[
    PreparedExecutionInitialization | CallPreparationRejected, Field(discriminator="kind")
]


class ExecutionInitializationPreparationPort(Protocol):
    async def prepare_initial_execution(
        self, request: ExecutionInitializationRequest
    ) -> ExecutionInitializationResult: ...
