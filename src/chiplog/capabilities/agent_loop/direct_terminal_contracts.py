from typing import Literal

from pydantic import Field

from chiplog.capabilities.effects.contracts import DispatchSemanticBinding, ExactHead

from .call_acceptance_contracts import CallSubjectHead
from .direct_effect_terminal_contracts import DirectEffectTerminalClassificationV1
from .execution_recovery_observations import ExecutionRecoveryDTO
from .recovery_contracts import Digest, Identity, UInt64


class AcceptedDirectTerminalCutV1(ExecutionRecoveryDTO):
    """Exact selected R16 closure and accepted-call source for first R14 terminal."""

    schema_id: Literal["chiplog.execution.accepted-direct-terminal-cut.v1"] = (
        "chiplog.execution.accepted-direct-terminal-cut.v1"
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
    selected_effect_member_bytes: bytes = Field(min_length=1)
    selected_decision: CallSubjectHead
    semantic_binding: DispatchSemanticBinding
    outcome_commit_sequence: UInt64
    publication_ordinal: UInt64
    batch_record_ids: tuple[Identity, ...] = Field(min_length=1)
    prior_terminal: Literal["ABSENT"] = "ABSENT"
    cut_fingerprint: Digest
class PrepareAcceptedDirectTerminalV1(ExecutionRecoveryDTO):
    kind: Literal["PREPARE_ACCEPTED_DIRECT_TERMINAL_V1"] = "PREPARE_ACCEPTED_DIRECT_TERMINAL_V1"
    command_id: Identity
    cut: AcceptedDirectTerminalCutV1


class AcceptedDirectTerminalDispositionV1(ExecutionRecoveryDTO):
    schema_id: Literal["chiplog.execution.accepted-direct-terminal-disposition.v1"] = (
        "chiplog.execution.accepted-direct-terminal-disposition.v1"
    )
    kind: Literal["SUCCEEDED", "FAILED_DEFINITE"]
    terminal_id: Identity
    source_cut_fingerprint: Digest
    original_call_id: Identity
    response_id: Identity
    accepted: CallSubjectHead
    external_effect_intent: CallSubjectHead
    selected_effect_terminal: ExactHead
    mapping_id: Literal["R14_DIRECT_EFFECT_TERMINAL_V1"]
    mapping_version: Literal["r14-direct-effect-terminal.v1"]


class AcceptedDirectTerminalResultV1(ExecutionRecoveryDTO):
    schema_id: Literal["chiplog.execution.accepted-direct-terminal-result.v1"] = (
        "chiplog.execution.accepted-direct-terminal-result.v1"
    )
    result_id: Identity
    source_cut_fingerprint: Digest
    original_call_id: Identity
    terminal: CallSubjectHead
    classification: DirectEffectTerminalClassificationV1


class AcceptedDirectTerminalAccountingV1(ExecutionRecoveryDTO):
    schema_id: Literal["chiplog.execution.accepted-direct-terminal-accounting.v1"] = (
        "chiplog.execution.accepted-direct-terminal-accounting.v1"
    )
    accounting_id: Identity
    source_cut_fingerprint: Digest
    original_call_id: Identity
    response_id: Identity
    accepted: CallSubjectHead
    execution_intent: CallSubjectHead
    external_effect_intent: CallSubjectHead
    terminal: CallSubjectHead
    result: CallSubjectHead
    selected_outcome_record_id: Identity
    selected_outcome_fingerprint: Digest
    selected_decision: CallSubjectHead
    outcome_commit_sequence: UInt64
    publication_ordinal: UInt64
    batch_record_ids: tuple[Identity, ...] = Field(min_length=1)


class PreparedAcceptedDirectTerminalV1(ExecutionRecoveryDTO):
    kind: Literal["PREPARED_ACCEPTED_DIRECT_TERMINAL_V1"] = "PREPARED_ACCEPTED_DIRECT_TERMINAL_V1"
    source_request_fingerprint: Digest
    accounting: AcceptedDirectTerminalAccountingV1
    terminal: AcceptedDirectTerminalDispositionV1
    result: AcceptedDirectTerminalResultV1
