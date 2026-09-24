"""Direct executable CompleteAcceptance preparation, distinct from a prior Turn join.

This owner prepares loop/delivery semantics. Composition obtains conversation and
effects members from their owners and selects the complete atomic batch. All
source and readiness values are independently re-enumerated at publication.
"""

from typing import Annotated, Literal, Protocol

from pydantic import Field

from .call_acceptance_contracts import CallPreparationRejected, CallSubjectHead
from .delivery_preparation import DeliveryAcceptanceProposal, DeliveryObservation
from .execution_contracts import ExecutionRunRecord
from .execution_recovery_observations import (
    ContinuationReadyRecord,
    ExecutionRecoveryCut,
    ExecutionRecoveryDTO,
    ExecutionTerminalManifest,
)
from .recovery_contracts import Digest, Identity, RunExecutionFence, UInt64


class PrepareExecutionCompletion(ExecutionRecoveryDTO):
    kind: Literal["PREPARE_EXECUTION_COMPLETION_V1"] = "PREPARE_EXECUTION_COMPLETION_V1"
    command_id: Identity
    run: ExecutionRunRecord
    selected_attempt: CallSubjectHead
    selector_generation: UInt64
    visibility_manifest: CallSubjectHead
    exact_captured_response: bytes = Field(min_length=1)
    cut: ExecutionRecoveryCut
    complete_earlier_continuations: tuple[ContinuationReadyRecord, ...]
    delivery: DeliveryObservation
    fence: RunExecutionFence


class PreparedExecutionCompletion(ExecutionRecoveryDTO):
    """One owner result; not the whole publication and not permission to deliver.

    Derive delivery acceptance from the original command/cut, then render/manifest,
    terminal accounting and final Run. Downstream owner members reference these
    final primitives; their aggregate fingerprint never feeds back into them.
    """

    kind: Literal["PREPARED_EXECUTION_COMPLETION_V1"] = "PREPARED_EXECUTION_COMPLETION_V1"
    source_request_fingerprint: Digest
    complete_earlier_continuations: tuple[ContinuationReadyRecord, ...]
    delivery: DeliveryAcceptanceProposal
    terminal_manifest: ExecutionTerminalManifest
    run: ExecutionRunRecord
    complete_owner_commitment: Digest


class PreparedExecutionCompletionReject(ExecutionRecoveryDTO):
    """Definite semantic rejection preserves labeled raw trace and has no delivery."""

    kind: Literal["PREPARED_EXECUTION_COMPLETION_REJECT_V1"] = (
        "PREPARED_EXECUTION_COMPLETION_REJECT_V1"
    )
    source_request_fingerprint: Digest
    original_captured_attempt: CallSubjectHead
    preserved_trace: CallSubjectHead
    visibility_manifest: CallSubjectHead
    reasons: tuple[
        Literal[
            "SCHEMA",
            "ASSERTION_EVIDENCE",
            "AUTHORITY",
            "POLICY",
            "DISCLOSURE",
            "ENDPOINT",
        ],
        ...,
    ] = Field(min_length=1)
    run: ExecutionRunRecord
    complete_owner_commitment: Digest


ExecutionCompletionResult = Annotated[
    PreparedExecutionCompletion | PreparedExecutionCompletionReject | CallPreparationRejected,
    Field(discriminator="kind"),
]


class ExecutionCompletionPreparationPort(Protocol):
    async def prepare_completion(
        self, request: PrepareExecutionCompletion
    ) -> ExecutionCompletionResult: ...
