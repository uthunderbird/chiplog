"""Broker-private closed publication seam, never a capability or transport port.

An envelope is an untrusted proposal. The broker resolves the operation registry,
authenticates its invocation and prepares outputs through every isolated semantic
owner before the writer transaction. Inside the transaction it reproduces exact
authoritative observations and validates issued output bytes and current fences.
No cross-owner call or await occurs while holding the writer. Changed observations
require fresh preparation. Only this path obtains a journal decision; DTO construction
proves none of it.
"""

from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

Identity = Annotated[str, Field(min_length=1)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
UInt64 = Annotated[int, Field(ge=0, le=2**64 - 1)]
Owner = Literal[
    "agent_loop", "effects", "planning", "broker_ingress", "broker_dispatch", "conversation"
]

BrokerOperation = Literal[
    "dispatch.consume_effect_send",
    "recovery.seal_response",
    "recovery.accept_call",
    "recovery.cancel_call",
    "recovery.record_result",
    "recovery.resolve_obligation",
    "recovery.reduce_evidence",
    "recovery.suspend",
    "recovery.resume",
    "recovery.successor",
    "recovery.readonly_attempt",
    "recovery.readonly_pending",
    "recovery.readonly_reduce",
    "recovery.terminalize",
    "recovery.work_claim",
    "recovery.work_renew",
    "recovery.work_takeover",
    "recovery.work_rollover",
    "recovery.work_close",
    "recovery.replace_model_attempt",
    "scheduler.genesis",
    "scheduler.amend_schedule",
    "scheduler.amend_policy",
    "scheduler.replace_bound",
    "scheduler.decide_interval",
    "scheduler.resolve_interval",
    "scheduler.acquire",
    "scheduler.renew",
    "scheduler.takeover",
    "scheduler.rollover",
    "effects.publish_recovery_intent",
    "effects.authorize",
    "effects.before_send",
    "effects.commit_send",
    "effects.record_evidence",
    "effects.reconcile",
    "ingress.allocate_receipt_token",
    "ingress.stage_raw_bytes",
    "ingress.publish_custody_successor",
    "ingress.admit_ready_generation",
    "ingress.select_ready_head",
    "ingress.block_and_rebase_prefix",
    "ingress.unblock_to_ready_tail",
    "ingress.quiesce_admission",
    "ingress.publish_drain_manifest",
    "ingress.advance_drain_state",
    "ingress.create_poll_page_manifest",
    "ingress.publish_poll_member_disposition",
    "ingress.authorize_local_ack",
    "ingress.issue_push_response_attempt",
    "ingress.observe_push_local_completion",
    "ingress.observe_authenticated_provider_receipt",
    "ingress.issue_cli_response_attempt",
    "ingress.observe_cli_local_completion",
    "ingress.authorize_poll_cursor",
    "ingress.apply_poll_cursor",
    "ingress.issue_poll_request",
    "ingress.authorize_reconciliation_release",
    "ingress.complete_reconciliation_release",
    "ingress.select_quarantine_parser_attempt",
    "ingress.publish_quarantine_result",
]


class BrokerDTO(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, ser_json_bytes="base64", val_json_bytes="base64"
    )


class ExactRecordHead(BrokerDTO):
    owner: Owner
    record_kind: Identity
    subject_id: Identity
    record_id: Identity
    fingerprint: Digest


class ObservedAbsence(BrokerDTO):
    kind: Literal["ABSENT"] = "ABSENT"
    owner: Owner
    record_kind: Identity
    subject_id: Identity


class ObservedPresence(BrokerDTO):
    kind: Literal["PRESENT"] = "PRESENT"
    head: ExactRecordHead


ObservedHead = Annotated[ObservedAbsence | ObservedPresence, Field(discriminator="kind")]


class AuthoritativeReadManifest(BrokerDTO):
    tenant_id: Identity
    tenant_frontier: UInt64
    expected_materialization_commitment: Digest
    registry_head: Identity
    registry_fingerprint: Digest
    ordered_heads: tuple[ObservedHead, ...]
    complete_manifest_fingerprint: Digest


class InvocationProofRef(BrokerDTO):
    """Broker-issued identity, never inferred from request owner or payload tenant."""

    issuance_id: Identity
    issuance_fingerprint: Digest
    broker_epoch: Identity
    broker_session: Identity
    runtime_generation: Identity
    operation_subject: Identity


class WorkerAuthentication(BrokerDTO):
    kind: Literal["WORKER"] = "WORKER"
    invocation: InvocationProofRef
    applicability_schema: Identity
    applicability_bytes: bytes = Field(min_length=1)
    applicability_fingerprint: Digest


class IndependentEvidenceAuthentication(BrokerDTO):
    kind: Literal["INDEPENDENT_EVIDENCE"] = "INDEPENDENT_EVIDENCE"
    invocation: InvocationProofRef
    ingress_row: Identity
    receipt_token_id: Identity
    custody_head: Identity
    authenticated_source_proof_id: Identity
    authenticated_source_proof_fingerprint: Digest
    original_subject_id: Identity


class BrokerIngressAuthentication(BrokerDTO):
    kind: Literal["BROKER_INGRESS"] = "BROKER_INGRESS"
    invocation: InvocationProofRef
    ingress_row: Identity
    source_contract_head: Identity
    admission_epoch_head: Identity
    admission_fence: UInt64


class BrokerTransportObservationAuthentication(BrokerDTO):
    kind: Literal["BROKER_TRANSPORT_OBSERVATION"] = "BROKER_TRANSPORT_OBSERVATION"
    invocation: InvocationProofRef
    issued_operation: ExactRecordHead
    transmission: ExactRecordHead
    originating_broker_epoch: Identity
    recipient_binding_fingerprint: Digest
    adapter_contract_version: Identity
    observation_fingerprint: Digest


PublicationAuthentication = Annotated[
    WorkerAuthentication
    | IndependentEvidenceAuthentication
    | BrokerIngressAuthentication
    | BrokerTransportObservationAuthentication,
    Field(discriminator="kind"),
]


class OwnerRecordBytes(BrokerDTO):
    owner: Owner
    record_kind: Identity
    record_id: Identity
    schema_id: Identity
    canonical_bytes: bytes = Field(min_length=1)
    fingerprint: Digest


class OwnerCommandBytes(BrokerDTO):
    owner: Owner
    schema_id: Identity
    canonical_bytes: bytes = Field(min_length=1)
    fingerprint: Digest


class PublicationIdentity(BrokerDTO):
    tenant_id: Identity
    command_id: Identity
    command_fingerprint: Digest
    canonicalization_version: Literal["chiplog.owner-publication.v1"]


class SingleOwnerBatch(BrokerDTO):
    kind: Literal["SINGLE_OWNER"] = "SINGLE_OWNER"
    operation: BrokerOperation
    identity: PublicationIdentity
    authentication: PublicationAuthentication
    expected: AuthoritativeReadManifest
    command: OwnerCommandBytes
    complete_records: tuple[OwnerRecordBytes, ...] = Field(min_length=1)
    complete_batch_fingerprint: Digest


class PlanEffectBatch(BrokerDTO):
    kind: Literal["PLAN_EFFECT_ATOMIC"] = "PLAN_EFFECT_ATOMIC"
    operation: Literal["effects.publish_plan_effect"] = "effects.publish_plan_effect"
    identity: PublicationIdentity
    authentication: WorkerAuthentication
    expected: AuthoritativeReadManifest
    planning_command: OwnerCommandBytes
    effects_command: OwnerCommandBytes
    complete_records: tuple[OwnerRecordBytes, ...] = Field(min_length=2)
    complete_batch_fingerprint: Digest


class NoPlanningParticipant(BrokerDTO):
    kind: Literal["NOT_APPLICABLE"] = "NOT_APPLICABLE"


class PlanningParticipant(BrokerDTO):
    kind: Literal["PLANNING_PUBLICATION"] = "PLANNING_PUBLICATION"
    command: OwnerCommandBytes


class CallEffectBatch(BrokerDTO):
    kind: Literal["CALL_EFFECT_ATOMIC"] = "CALL_EFFECT_ATOMIC"
    operation: Literal["effects.accept_call"] = "effects.accept_call"
    identity: PublicationIdentity
    authentication: WorkerAuthentication
    expected: AuthoritativeReadManifest
    loop_command: OwnerCommandBytes
    effects_command: OwnerCommandBytes
    planning: Annotated[NoPlanningParticipant | PlanningParticipant, Field(discriminator="kind")]
    complete_records: tuple[OwnerRecordBytes, ...] = Field(min_length=3)
    complete_batch_fingerprint: Digest


class CompleteDeliveryBatch(BrokerDTO):
    kind: Literal["COMPLETE_DELIVERY_ATOMIC"] = "COMPLETE_DELIVERY_ATOMIC"
    operation: Literal["agent_loop.complete_acceptance"] = "agent_loop.complete_acceptance"
    identity: PublicationIdentity
    authentication: WorkerAuthentication
    expected: AuthoritativeReadManifest
    loop_command: OwnerCommandBytes
    prepared_effects_commands: tuple[OwnerCommandBytes, ...] = Field(min_length=1)
    complete_records: tuple[OwnerRecordBytes, ...] = Field(min_length=2)
    complete_batch_fingerprint: Digest


class CompleteDeliveryBatchV2(BrokerDTO):
    """Versioned completion assembly; v1 remains an immutable historical wire."""

    kind: Literal["COMPLETE_DELIVERY_ATOMIC_V2"] = "COMPLETE_DELIVERY_ATOMIC_V2"
    schema_id: Literal["chiplog.owner-publication.complete-delivery.v2"] = (
        "chiplog.owner-publication.complete-delivery.v2"
    )
    operation: Literal["agent_loop.complete_acceptance.v2"] = "agent_loop.complete_acceptance.v2"
    identity: PublicationIdentity
    authentication: WorkerAuthentication
    expected: AuthoritativeReadManifest
    loop_command: OwnerCommandBytes
    conversation_command: OwnerCommandBytes
    terminal_work_command: OwnerCommandBytes
    prepared_effects_commands: tuple[OwnerCommandBytes, ...] = Field(min_length=1)
    complete_records: tuple[OwnerRecordBytes, ...] = Field(min_length=1)
    complete_batch_fingerprint: Digest

    @model_validator(mode="after")
    def exact_owner_roles(self) -> CompleteDeliveryBatchV2:
        if (
            self.loop_command.owner != "agent_loop"
            or self.conversation_command.owner != "conversation"
            or self.terminal_work_command.owner != "agent_loop"
            or any(command.owner != "effects" for command in self.prepared_effects_commands)
        ):
            raise ValueError("complete delivery v2 command owners differ from fixed roles")
        return self


class RejectedCompletionBatchV1(BrokerDTO):
    kind: Literal["REJECTED_COMPLETION_ATOMIC_V1"] = "REJECTED_COMPLETION_ATOMIC_V1"
    schema_id: Literal["chiplog.owner-publication.rejected-completion.v1"] = (
        "chiplog.owner-publication.rejected-completion.v1"
    )
    operation: Literal["agent_loop.reject_completion.v1"] = "agent_loop.reject_completion.v1"
    identity: PublicationIdentity
    authentication: WorkerAuthentication
    expected: AuthoritativeReadManifest
    loop_rejection_command: OwnerCommandBytes
    rejected_terminalization_command: OwnerCommandBytes
    conversation_no_change_command: OwnerCommandBytes
    terminal_work_command: OwnerCommandBytes
    complete_records: tuple[OwnerRecordBytes, ...] = Field(min_length=1)
    complete_batch_fingerprint: Digest

    @model_validator(mode="after")
    def exact_owner_roles(self) -> RejectedCompletionBatchV1:
        if (
            self.loop_rejection_command.owner != "agent_loop"
            or self.rejected_terminalization_command.owner != "agent_loop"
            or self.conversation_no_change_command.owner != "conversation"
            or self.terminal_work_command.owner != "agent_loop"
        ):
            raise ValueError("rejected completion command owners differ from fixed roles")
        return self


RegisteredPublication = Annotated[
    SingleOwnerBatch
    | PlanEffectBatch
    | CallEffectBatch
    | CompleteDeliveryBatch
    | CompleteDeliveryBatchV2
    | RejectedCompletionBatchV1,
    Field(discriminator="kind"),
]


class JournalSelectedPublication(BrokerDTO):
    kind: Literal["COMMITTED", "EXACT_REPLAY"]
    tenant_id: Identity
    command_id: Identity
    decision_id: Identity
    decision_head: Identity
    decision_fingerprint: Digest
    predecessor_commitment: Digest
    resulting_commitment: Digest
    tenant_commit_sequence: UInt64
    complete_records: tuple[OwnerRecordBytes, ...] = Field(min_length=1)


class PublicationRejected(BrokerDTO):
    kind: Literal["HOLD", "CONFLICT", "STALE", "DENIED", "INTEGRITY_FAULT"]
    tenant_id: Identity
    command_id: Identity
    reason: Identity


BrokerPublicationResult = JournalSelectedPublication | PublicationRejected


class ExactReplayQuery(BrokerDTO):
    """Lookup occurs before fresh owner preparation; no historical output is rebuilt.

    Current invocation authenticates the caller. Original command bytes preserve
    the complete historical authority/fence/proof domain, including expired leases.
    Only a byte-identical selected journal decision can return historical success.
    """

    identity: PublicationIdentity
    operation: (
        BrokerOperation
        | Literal[
            "effects.accept_call",
            "effects.publish_plan_effect",
            "agent_loop.complete_acceptance",
            "agent_loop.complete_acceptance.v2",
            "agent_loop.reject_completion.v1",
        ]
    )
    current_invocation: InvocationProofRef
    original_commands: tuple[OwnerCommandBytes, ...] = Field(min_length=1)


class NoSelectedDecision(BrokerDTO):
    kind: Literal["NO_DECISION"] = "NO_DECISION"
    tenant_id: Identity
    command_id: Identity


class RegisteredOwnerCommitter(Protocol):
    """Internal adapter-to-broker port; no raw writer/guard callable is exposed."""

    async def commit(self, request: RegisteredPublication) -> BrokerPublicationResult: ...

    def lookup_exact(
        self, query: ExactReplayQuery
    ) -> JournalSelectedPublication | NoSelectedDecision | PublicationRejected: ...
