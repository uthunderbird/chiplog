"""Preparation-only call lifecycle boundary; values never grant publication authority.

Record references are content commitments, not evidence of durable publication.
Composition must authenticate the cut and atomically publish all owner participants.
"""

from __future__ import annotations

from typing import Annotated, Literal, Protocol

from pydantic import Field

from .recovery_contracts import (
    Absent,
    Digest,
    Identity,
    NotApplicable,
    Present,
    RecoveryDTO,
    RunExecutionFence,
    UInt64,
)
from .recovery_frontier_contracts import AcceptanceBranch, FanOutBound, ReadOnlyRetryLineage


class CallSubjectHead(RecoveryDTO):
    subject_id: Identity
    revision: Present


class OriginalCallKey(RecoveryDTO):
    """Subject ID is call:SHA256(canonical key); current Run is not in this key."""

    kind: Literal["chiplog.call.identity.v1"] = "chiplog.call.identity.v1"
    tenant_id: Identity
    original_run_id: Identity
    original_turn_id: Identity
    captured_response: CallSubjectHead
    ordinal: UInt64
    model_call_label: Identity


class InitializedReadOnlyLineage(RecoveryDTO):
    """Frozen at original initialization; never allocated lazily by a retry."""

    kind: Literal["READ_ONLY_RETRY_LINEAGE"] = "READ_ONLY_RETRY_LINEAGE"
    lineage: ReadOnlyRetryLineage


class SealedCallInput(RecoveryDTO):
    """Classification is an owner-verified registered schema result, not permission."""

    original: OriginalCallKey
    classification: Literal["PROPOSAL_ONLY", "CONSEQUENTIAL", "READ_ONLY"]
    tool_schema: CallSubjectHead
    tool_policy: CallSubjectHead
    canonical_call_base64: Identity
    retry_lineage: Annotated[
        NotApplicable | InitializedReadOnlyLineage, Field(discriminator="kind")
    ]


class CallAuthorityObservation(RecoveryDTO):
    """The registered operation requires complete source enumeration and live recheck."""

    source_id: Identity
    family: Literal[
        "ACTOR",
        "MANDATE",
        "TOOL_SCHEMA",
        "POLICY",
        "APPLICABILITY",
        "RECIPIENT",
        "CONSEQUENCE_SCOPE",
        "PLANNING",
        "DEPENDENCY",
        "EVIDENCE",
        "CONFLICT_ORDER",
    ]
    source: CallSubjectHead
    generation: Identity
    frontier: Identity
    canonical_value_base64: Identity
    observed_at_ns: UInt64
    valid_until_ns: UInt64


class InitializedCallRecord(RecoveryDTO):
    kind: Literal["CALL_INITIALIZED_V1"] = "CALL_INITIALIZED_V1"
    original_call_id: Identity
    call: SealedCallInput
    predecessor: Absent


class CallLifecycleObservation(RecoveryDTO):
    """Previously observed history only; never include a proposed output here."""

    original_call_id: Identity
    initialized: CallSubjectHead
    initialized_record: InitializedCallRecord
    acceptance: AcceptanceBranch
    terminal: Annotated[Absent | Present, Field(discriminator="kind")]


class CallInventorySnapshot(RecoveryDTO):
    kind: Literal["CALL_PREDECESSOR_INVENTORY_V1"] = "CALL_PREDECESSOR_INVENTORY_V1"
    tenant_id: Identity
    tenant_commit_sequence: UInt64
    ordered_calls: tuple[CallLifecycleObservation, ...]


class CallPreparationCut(RecoveryDTO):
    tenant_id: Identity
    current_run: CallSubjectHead
    run_state: Literal["ACTIVE"]
    tenant_commit_sequence: UInt64
    materialization_commitment: Digest
    complete_call_inventory: CallSubjectHead
    predecessor_inventory: CallInventorySnapshot
    authority_registry: CallSubjectHead
    sources: tuple[CallAuthorityObservation, ...] = Field(min_length=1)
    fence: RunExecutionFence


class FanOutPreparationRequest(RecoveryDTO):
    kind: Literal["PREPARE_CALL_FAN_OUT_V1"] = "PREPARE_CALL_FAN_OUT_V1"
    command_id: Identity
    original_run_id: Identity
    original_turn_id: Identity
    captured_response: CallSubjectHead
    canonical_response_base64: Identity
    ordered_calls: tuple[SealedCallInput, ...]
    bound: FanOutBound
    cut: CallPreparationCut


class SealedResponseRecord(RecoveryDTO):
    kind: Literal["MODEL_RESPONSE_RECEIVED_V1"] = "MODEL_RESPONSE_RECEIVED_V1"
    response_seal_id: Identity
    tenant_id: Identity
    original_run_id: Identity
    original_turn_id: Identity
    captured_response: CallSubjectHead
    complete_ordered_initialized: tuple[CallSubjectHead, ...]
    bound: FanOutBound


class CallDispatchSemantics(RecoveryDTO):
    """Consumer-owned references to all five effects semantics components."""

    normative_manifest: CallSubjectHead
    reducer: CallSubjectHead
    transition_registry: CallSubjectHead
    canonicalization: CallSubjectHead
    adapter_contract: CallSubjectHead


class ConsequentialAcceptanceBinding(RecoveryDTO):
    """Immutable preimage excludes subsequently derived accepted/execution heads."""

    original_call_id: Identity
    original: OriginalCallKey
    initialized: CallSubjectHead
    tool_schema: CallSubjectHead
    tool_policy: CallSubjectHead
    external_intent: CallSubjectHead
    dispatch_semantics: CallDispatchSemantics
    cut: CallPreparationCut


class AcceptConsequentialCallRequest(RecoveryDTO):
    kind: Literal["PREPARE_CONSEQUENTIAL_ACCEPTANCE_V1"] = "PREPARE_CONSEQUENTIAL_ACCEPTANCE_V1"
    command_id: Identity
    binding: ConsequentialAcceptanceBinding
    initialized_record: InitializedCallRecord


class ToolCallAcceptedRecord(RecoveryDTO):
    kind: Literal["TOOL_CALL_ACCEPTED_V1"] = "TOOL_CALL_ACCEPTED_V1"
    accepted_id: Identity
    source_command_id: Identity
    binding: ConsequentialAcceptanceBinding
    execution_intent_id: Identity


class ToolExecutionIntentRecord(RecoveryDTO):
    kind: Literal["TOOL_EXECUTION_INTENT_V1"] = "TOOL_EXECUTION_INTENT_V1"
    execution_intent_id: Identity
    original_call_id: Identity
    initialized: CallSubjectHead
    accepted: CallSubjectHead
    external_intent: CallSubjectHead


class CancelBeforeAcceptRequest(RecoveryDTO):
    kind: Literal["PREPARE_CANCEL_BEFORE_ACCEPT_V1"] = "PREPARE_CANCEL_BEFORE_ACCEPT_V1"
    command_id: Identity
    original_call_id: Identity
    original: OriginalCallKey
    initialized: CallSubjectHead
    initialized_record: InitializedCallRecord
    cancellation_act: CallSubjectHead
    cut: CallPreparationCut


class CancelledBeforeAcceptRecord(RecoveryDTO):
    kind: Literal["CALL_CANCELLED_BEFORE_ACCEPT_V1"] = "CALL_CANCELLED_BEFORE_ACCEPT_V1"
    terminal_id: Identity
    source_command_id: Identity
    original_call_id: Identity
    original: OriginalCallKey
    initialized: CallSubjectHead
    cancellation_act: CallSubjectHead
    cut: CallPreparationCut
    not_executed_result_id: Identity


class NotExecutedCallResultRecord(RecoveryDTO):
    kind: Literal["CALL_NOT_EXECUTED_RESULT_V1"] = "CALL_NOT_EXECUTED_RESULT_V1"
    result_id: Identity
    original_call_id: Identity
    initialized: CallSubjectHead
    terminal: CallSubjectHead
    outcome: Literal["NOT_EXECUTED"]


class PreparedCallFanOut(RecoveryDTO):
    kind: Literal["PREPARED_CALL_FAN_OUT_V1"] = "PREPARED_CALL_FAN_OUT_V1"
    source_request_fingerprint: Digest
    response_seal: SealedResponseRecord
    initialized_records: tuple[InitializedCallRecord, ...]
    complete_ordered_record_manifest: tuple[CallSubjectHead, ...] = Field(min_length=1)
    proposal_fingerprint: Digest


class PreparedConsequentialAcceptance(RecoveryDTO):
    kind: Literal["PREPARED_CONSEQUENTIAL_ACCEPTANCE_V1"] = "PREPARED_CONSEQUENTIAL_ACCEPTANCE_V1"
    source_request_fingerprint: Digest
    accepted: ToolCallAcceptedRecord
    execution_intent: ToolExecutionIntentRecord
    # accepted record, execution record, immutable external intent CONTENT.
    # The effects-owned physical acceptance record is a separate batch member.
    complete_acceptance_manifest: tuple[CallSubjectHead, ...] = Field(min_length=3, max_length=3)
    proposal_fingerprint: Digest


class PreparedPreAcceptCancellation(RecoveryDTO):
    kind: Literal["PREPARED_PRE_ACCEPT_CANCELLATION_V1"] = "PREPARED_PRE_ACCEPT_CANCELLATION_V1"
    source_request_fingerprint: Digest
    terminal: CancelledBeforeAcceptRecord
    result: NotExecutedCallResultRecord
    complete_ordered_record_manifest: tuple[CallSubjectHead, ...] = Field(
        min_length=2, max_length=2
    )
    proposal_fingerprint: Digest


class CallPreparationRejected(RecoveryDTO):
    kind: Literal["CALL_PREPARATION_REJECTED_V1"] = "CALL_PREPARATION_REJECTED_V1"
    command_id: Identity
    code: Literal["DENIED", "STALE", "CONFLICT", "UNSUPPORTED", "INTEGRITY_FAULT", "HOLD"]
    reason: Identity


CallPreparationResult = Annotated[
    PreparedCallFanOut
    | PreparedConsequentialAcceptance
    | PreparedPreAcceptCancellation
    | CallPreparationRejected,
    Field(discriminator="kind"),
]


class CallPreparationPort(Protocol):
    async def prepare_fan_out(
        self, request: FanOutPreparationRequest
    ) -> PreparedCallFanOut | CallPreparationRejected: ...

    async def prepare_acceptance(
        self, request: AcceptConsequentialCallRequest
    ) -> PreparedConsequentialAcceptance | CallPreparationRejected: ...

    async def prepare_cancellation(
        self, request: CancelBeforeAcceptRequest
    ) -> PreparedPreAcceptCancellation | CallPreparationRejected: ...
