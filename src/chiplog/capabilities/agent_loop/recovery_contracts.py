"""R14/R15 owner-local wire contracts. Construction does not grant commit authority.

These DTOs carry observations to an authenticated broker. Only a registered writer
can verify current heads, proof issuance and applicability at its transaction cut.
"""

from __future__ import annotations

import json
from typing import Annotated, Literal

from pydantic import Field

from chiplog.capabilities.agent_loop.contracts import Frozen

Identity = Annotated[str, Field(min_length=1)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
UInt64 = Annotated[int, Field(ge=0, le=2**64 - 1)]


def _ordered(value: object) -> object:
    if isinstance(value, dict):
        keys = sorted(value, key=lambda key: (key != "kind", key))
        return {key: _ordered(value[key]) for key in keys}
    if isinstance(value, list):
        return [_ordered(item) for item in value]
    return value


class RecoveryDTO(Frozen):
    """Tagged JSON v1: discriminant first; other keys lexical; arrays retain order."""

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            _ordered(self.model_dump(mode="json")), separators=(",", ":"), ensure_ascii=False
        ).encode()


class Absent(RecoveryDTO):
    kind: Literal["ABSENT"] = "ABSENT"


class NotApplicable(RecoveryDTO):
    kind: Literal["NOT_APPLICABLE"] = "NOT_APPLICABLE"


class Present(RecoveryDTO):
    kind: Literal["PRESENT"] = "PRESENT"
    head: Identity
    fingerprint: Digest


HeadMarker = Annotated[Absent | NotApplicable | Present, Field(discriminator="kind")]


class IndividualSubject(RecoveryDTO):
    kind: Literal["INDIVIDUAL"] = "INDIVIDUAL"
    identity_domain: Literal["chiplog.scheduler.individual.v1"] = "chiplog.scheduler.individual.v1"
    occurrence_id: Identity


class CoalescedSubject(RecoveryDTO):
    kind: Literal["COALESCED"] = "COALESCED"
    identity_domain: Literal["chiplog.scheduler.coalesced.v1"] = "chiplog.scheduler.coalesced.v1"
    aggregate_id: Identity
    manifest_fingerprint: Digest


ExecutionLineageSubject = Annotated[
    IndividualSubject | CoalescedSubject, Field(discriminator="kind")
]


class ExecutionLineageBinding(RecoveryDTO):
    root_id: Identity
    subject: ExecutionLineageSubject
    root_fingerprint: Digest
    lineage_head: Identity
    initial_run_id: Identity
    current_run_id: Identity
    schedule_id: Identity
    schedule_revision: Identity
    policy_revision: Identity


class PhysicalRootBinding(RecoveryDTO):
    selector_id: Identity
    selector_head: Identity
    selector_version: UInt64
    current_epoch_id: Identity
    current_epoch_head: Identity


class LeaseBinding(RecoveryDTO):
    lease_head: Identity
    holder_id: Identity
    holder_session_id: Identity
    lease_id: Identity
    generation: UInt64
    trusted_expiry: UInt64
    clock_contract_version: Identity


class TrustedClockProofRef(RecoveryDTO):
    """Reference to broker-issued proof; submitted values alone prove nothing.

    command_payload_fingerprint excludes this reference, avoiding a hash cycle.
    fence_fingerprint covers fence fields excluding clock_proof. The broker
    rederives both domains and checks issued proof bytes at the writer cut.
    The final command fingerprint includes the complete reference unchanged.
    """

    proof_id: Identity
    proof_fingerprint: Digest
    proof_version: Identity
    clock_contract_version: Identity
    fence_fingerprint: Digest
    command_id: Identity
    command_payload_fingerprint: Digest
    submission_id: Identity


class NonSchedulerFence(RecoveryDTO):
    kind: Literal["NON_SCHEDULER_NOT_APPLICABLE"] = "NON_SCHEDULER_NOT_APPLICABLE"
    lineage: NotApplicable
    physical_root: NotApplicable
    lease: NotApplicable
    clock_proof: NotApplicable
    run_id: Identity
    run_head: Identity
    worker_session_id: Identity
    runtime_generation: Identity


class SchedulerExecutionFence(RecoveryDTO):
    kind: Literal["EXECUTION_ROOT_LIVE_LEASE"] = "EXECUTION_ROOT_LIVE_LEASE"
    run_head: Identity
    lineage: ExecutionLineageBinding
    physical_root: PhysicalRootBinding
    lease: LeaseBinding
    clock_proof: TrustedClockProofRef


RunExecutionFence = Annotated[
    NonSchedulerFence | SchedulerExecutionFence, Field(discriminator="kind")
]


class DecisionMaterializationRef(RecoveryDTO):
    decision_id: Identity
    decision_head: Identity
    decision_fingerprint: Digest
    command_fingerprint: Digest
    expected_complete_commitment: Digest
    predecessor_commitment: Digest
    journal_authority_head: Identity


class FirstPublication(RecoveryDTO):
    kind: Literal["FIRST_PUBLICATION"] = "FIRST_PUBLICATION"
    decision: Absent
    expected_canonical_absence_manifest: Identity


class MaterializeExactDecision(RecoveryDTO):
    kind: Literal["MATERIALIZE_EXACT_DECISION"] = "MATERIALIZE_EXACT_DECISION"
    decision: DecisionMaterializationRef
    exact_prefix_manifest: Identity


class ReturnExactReplay(RecoveryDTO):
    kind: Literal["RETURN_EXACT_REPLAY"] = "RETURN_EXACT_REPLAY"
    decision: DecisionMaterializationRef
    complete_materialization_manifest: Identity


PreRootDisposition = Annotated[
    FirstPublication | MaterializeExactDecision | ReturnExactReplay, Field(discriminator="kind")
]


class PreRootDecisionFence(RecoveryDTO):
    kind: Literal["PRE_ROOT_SCHEDULER_DECISION"] = "PRE_ROOT_SCHEDULER_DECISION"
    command_id: Identity
    disposition: PreRootDisposition
    scheduler_authority_head: Identity
    broker_generation: Identity
    runtime_generation: Identity


class OriginalObligationBinding(RecoveryDTO):
    original_run_id: Identity
    original_call_id: Identity
    obligation_id: Identity
    obligation_stream_id: Identity
    obligation_head: Identity
    closure_predicate_id: Identity
    closure_predicate_version: Identity
    resolver_id: Identity
    resolver_version: Identity
    reducer_id: Identity
    reducer_version: Identity
    evidence_stream_id: Identity
    evidence_head: Annotated[Absent | Present, Field(discriminator="kind")]


class WorkSubjectBinding(RecoveryDTO):
    work_id: Identity
    work_subject_head: Identity
    work_subject_fingerprint: Digest
    terminal_manifest_head: Identity
    terminal_manifest_member_fingerprint: Digest
    original_obligation: OriginalObligationBinding


class WorkEpochBinding(RecoveryDTO):
    selector_id: Identity
    selector_head: Identity
    selector_version: UInt64
    current_epoch_id: Identity
    current_epoch_head: Identity


class PostTerminalWorkFence(RecoveryDTO):
    kind: Literal["POST_TERMINAL_RECOVERY_WORK"] = "POST_TERMINAL_RECOVERY_WORK"
    subject: WorkSubjectBinding
    work_epoch: WorkEpochBinding
    lease: LeaseBinding
    clock_proof: TrustedClockProofRef
    work_command_id: Identity


class ExhaustionBinding(RecoveryDTO):
    hold_head: Identity
    exhausted_command_id: Identity
    lease: LeaseBinding
    authority_epoch: Identity


class RolloverPredecessor(RecoveryDTO):
    kind: Literal["PRESENT"] = "PRESENT"
    decision: Present
    edge: Present


class RolloverAuthorityRef(RecoveryDTO):
    """Proof payload retains authority head, command ID and predecessor pair.

    Only proof_id, proof_fingerprint and command_payload_fingerprint are excluded
    as self-covering proof slots. The full fence, including exhaustion authority
    epoch, remains in that payload; the final command includes every proof field.
    """

    proof_id: Identity
    proof_fingerprint: Digest
    authority_head: Identity
    command_id: Identity
    command_payload_fingerprint: Digest
    predecessor_rollover: Annotated[Absent | RolloverPredecessor, Field(discriminator="kind")]


class PhysicalRootRolloverFence(RecoveryDTO):
    kind: Literal["PHYSICAL_ROOT_ROLLOVER"] = "PHYSICAL_ROOT_ROLLOVER"
    lineage: ExecutionLineageBinding
    physical_root: PhysicalRootBinding
    exhaustion: ExhaustionBinding
    authority: RolloverAuthorityRef


class WorkEpochRolloverFence(RecoveryDTO):
    kind: Literal["WORK_EPOCH_ROLLOVER"] = "WORK_EPOCH_ROLLOVER"
    subject: WorkSubjectBinding
    work_epoch: WorkEpochBinding
    exhaustion: ExhaustionBinding
    authority: RolloverAuthorityRef


WorkerCommitApplicability = Annotated[
    NonSchedulerFence
    | SchedulerExecutionFence
    | PreRootDecisionFence
    | PostTerminalWorkFence
    | PhysicalRootRolloverFence
    | WorkEpochRolloverFence,
    Field(discriminator="kind"),
]
