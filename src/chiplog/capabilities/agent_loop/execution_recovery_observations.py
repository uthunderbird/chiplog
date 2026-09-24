"""Complete inert recovery observations and separate accounting/readiness records.

The broker enumerates and authenticates these values at one cut; the writer must
re-enumerate them at selection. Their construction cannot prove completeness.
"""

from typing import Annotated, Literal

from pydantic import ConfigDict, Field

from .call_acceptance_contracts import CallSubjectHead, SealedResponseRecord
from .execution_run_versions import ExecutionRun
from .recovery_contracts import (
    Absent,
    Digest,
    Identity,
    OriginalObligationBinding,
    Present,
    RecoveryDTO,
    UInt64,
)
from .recovery_frontier_contracts import (
    CallRecoveryFrontier,
    EvidenceReduction,
    FrozenRunBindings,
    RecoveryFrontier,
    TerminalCallFrontier,
)


class ExecutionRecoveryDTO(RecoveryDTO):
    model_config = ConfigDict(ser_json_bytes="base64", val_json_bytes="base64")


class RecoverySourceRecord(ExecutionRecoveryDTO):
    owner: Identity
    subject: CallSubjectHead
    schema_id: Identity
    canonical_record_bytes: bytes = Field(min_length=1)
    selected_decision: CallSubjectHead
    physical_record: CallSubjectHead


class RecoveryCausalChange(ExecutionRecoveryDTO):
    """Registered transitions only; the owner verifies ancestry and complete coverage."""

    subject_id: Identity
    source_family: Identity
    predecessor: Annotated[Absent | Present, Field(discriminator="kind")]
    successor: CallSubjectHead
    causal_parents: tuple[CallSubjectHead, ...] = Field(min_length=1)
    transition_registry: CallSubjectHead
    transition_id: Identity
    source: RecoverySourceRecord


class SealedResponseRecoveryView(ExecutionRecoveryDTO):
    original_run: CallSubjectHead
    original_turn: CallSubjectHead
    selected_seal: CallSubjectHead
    seal: SealedResponseRecord
    ordered_calls: tuple[CallRecoveryFrontier, ...]
    frontier_head: CallSubjectHead


class OriginalRecoveredClosure(ExecutionRecoveryDTO):
    original: OriginalObligationBinding
    terminal_disposition: Present
    recovered_outcome: Present
    accepted_evidence: Present
    closure: Present
    resolver_batch: Present
    reduction_id: Identity
    accepted_semantic_class: Identity


class ExecutionRecoveryCut(ExecutionRecoveryDTO):
    schema_id: Literal["chiplog.execution.recovery-cut.v1"] = "chiplog.execution.recovery-cut.v1"
    tenant_id: Identity
    database_id: Identity
    tenant_commit_sequence: UInt64
    materialization_commitment: Digest
    current_run: CallSubjectHead
    complete_ordered_run_lineage: tuple[ExecutionRun, ...] = Field(min_length=1)
    complete_ordered_responses: tuple[SealedResponseRecoveryView, ...]
    frontier: RecoveryFrontier
    current_bindings: FrozenRunBindings
    original_closures: tuple[OriginalRecoveredClosure, ...]
    current_reductions: tuple[EvidenceReduction, ...]
    complete_sources: tuple[RecoverySourceRecord, ...] = Field(min_length=1)
    complete_causal_changes: tuple[RecoveryCausalChange, ...]
    complete_inventory_fingerprint: Digest


class SealedAccountingRecord(ExecutionRecoveryDTO):
    kind: Literal["SEALED_ACCOUNTING_V1"] = "SEALED_ACCOUNTING_V1"
    accounting_id: Identity
    source_cut_fingerprint: Digest
    sealed_response: CallSubjectHead
    sealed_manifest_fingerprint: Digest
    registry: CallSubjectHead
    frontier: CallSubjectHead
    complete_ordered_calls: tuple[TerminalCallFrontier, ...]


class ContinuationReadyRecord(ExecutionRecoveryDTO):
    kind: Literal["CONTINUATION_READY_V1"] = "CONTINUATION_READY_V1"
    readiness_id: Identity
    source_cut_fingerprint: Digest
    accounting: SealedAccountingRecord
    complete_original_closures: tuple[OriginalRecoveredClosure, ...]
    complete_current_reductions: tuple[EvidenceReduction, ...]


class ExecutionSuspensionBaseline(ExecutionRecoveryDTO):
    """V2 primitive: no back-reference to the not-yet-hashed suspended Run.

    Exact suspended-head binding lives in the mandatory selected pair. Hash this
    baseline first, then the Run referencing it, then that pair. V1 is unchanged.
    """

    schema_id: Literal["chiplog.execution.suspension-baseline.v2"] = (
        "chiplog.execution.suspension-baseline.v2"
    )
    baseline_id: Identity
    suspension_command_id: Identity
    run_id: Identity
    predecessor_run: CallSubjectHead
    source_cut_fingerprint: Digest
    frontier: RecoveryFrontier
    bindings: FrozenRunBindings
    original_obligations: tuple[OriginalObligationBinding, ...]
    activation_blocking_predicates: tuple[Present, ...]


class ExecutionSuspensionPair(ExecutionRecoveryDTO):
    schema_id: Literal["chiplog.execution.suspension-pair.v2"] = (
        "chiplog.execution.suspension-pair.v2"
    )
    suspension_command_id: Identity
    source_cut_fingerprint: Digest
    predecessor_run: CallSubjectHead
    baseline: CallSubjectHead
    suspended_run: CallSubjectHead


class SelectedExecutionSuspension(ExecutionRecoveryDTO):
    baseline: ExecutionSuspensionBaseline
    pair: ExecutionSuspensionPair
    selected_pair: CallSubjectHead
    selected_decision: CallSubjectHead
    canonical_pair_bytes: bytes = Field(min_length=1)


class ExecutionTerminalManifest(ExecutionRecoveryDTO):
    """Hash before terminal Run/work outputs; references original streams only."""

    schema_id: Literal["chiplog.execution.terminal-manifest.v1"] = (
        "chiplog.execution.terminal-manifest.v1"
    )
    manifest_id: Identity
    command_id: Identity
    prior_run: CallSubjectHead
    target: Literal["SUCCEEDED", "ABORTED", "CANCELLED"]
    source_cut_fingerprint: Digest
    complete_accounting: tuple[SealedAccountingRecord, ...]
    complete_open_original_obligations: tuple[OriginalObligationBinding, ...]
