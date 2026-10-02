"""Broker-private closed publication seam, never a capability or transport port.

An envelope is an untrusted proposal. The broker resolves the operation registry,
authenticates its invocation and prepares outputs through every isolated semantic
owner before the writer transaction. Inside the transaction it reproduces exact
authoritative observations and validates issued output bytes and current fences.
No cross-owner call or await occurs while holding the writer. Changed observations
require fresh preparation. Only this path obtains a journal decision; DTO construction
proves none of it.
"""

import hashlib
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from chiplog.capabilities.agent_loop.delivery_work_contracts import (
    DELIVERY_WORK_CLAIM_COMMAND_SCHEMA,
    DELIVERY_WORK_GENESIS_COMMAND_SCHEMA,
    DELIVERY_WORK_OWNER,
    DELIVERY_WORK_SCHEMA,
    DeliveryWorkSourceV1,
    DeliveryWorkStateV1,
    PrepareDeliveryWorkClaimV1,
    PrepareDeliveryWorkGenesisV1,
    prepare_delivery_work_genesis,
)

from .recovery_diagnostic_refs import BrokerDiagnosticSourceRefV1

Identity = Annotated[str, Field(min_length=1)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
UInt64 = Annotated[int, Field(ge=0, le=2**64 - 1)]
Owner = Literal[
    "agent_loop",
    "effects",
    "planning",
    "broker_ingress",
    "broker_dispatch",
    "conversation",
    "broker_scheduler",
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
    "recovery.publish_terminal_fault",
    "recovery.replace_model_attempt",
    "scheduler.genesis",
    "scheduler.issue_mandate",
    "r15.adopt_lease_execution_policy",
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
    "effects.authorize_scoped_delivery_v3",
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


class BrokerRecoveryDiagnosticAuthenticationV1(BrokerDTO):
    """Broker issuance for the one terminal recovery-fault publication shape."""

    kind: Literal["BROKER_RECOVERY_DIAGNOSTIC"] = "BROKER_RECOVERY_DIAGNOSTIC"
    operation: Literal["recovery.publish_terminal_fault"] = "recovery.publish_terminal_fault"
    invocation: InvocationProofRef
    observer_registry: BrokerDiagnosticSourceRefV1
    diagnostic_observation: BrokerDiagnosticSourceRefV1
    issuance: BrokerDiagnosticSourceRefV1
    original_run: ExactRecordHead
    diagnostic_cut_fingerprint: Digest
    source_capture_inventory_fingerprint: Digest
    observation_subject_fingerprint: Digest
    final_request_fingerprint: Digest

    @model_validator(mode="after")
    def exact_broker_source_roles(self) -> BrokerRecoveryDiagnosticAuthenticationV1:
        if (
            self.observer_registry.source_kind != "FAULT_OBSERVER_REGISTRY"
            or self.diagnostic_observation.source_kind != "FAULT_OBSERVATION"
            or self.issuance.source_kind != "FAULT_ISSUANCE"
        ):
            raise ValueError("diagnostic authentication source kinds differ from fixed roles")
        if self.original_run.owner != "agent_loop" or self.original_run.record_kind != "Run":
            raise ValueError("diagnostic authentication original run differs from agent_loop Run")
        if self.invocation.operation_subject != self.observation_subject_fingerprint:
            raise ValueError(
                "diagnostic authentication invocation subject differs from observation"
            )
        return self


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
    | BrokerTransportObservationAuthentication
    | BrokerRecoveryDiagnosticAuthenticationV1,
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

    @model_validator(mode="after")
    def diagnostic_authentication_has_only_its_fixed_publication_shape(self) -> SingleOwnerBatch:
        if not isinstance(self.authentication, BrokerRecoveryDiagnosticAuthenticationV1):
            return self
        if self.operation != "recovery.publish_terminal_fault" or len(self.complete_records) != 1:
            raise ValueError("diagnostic authentication requires its singleton fault publication")
        if (
            self.command.owner != "agent_loop"
            or self.command.schema_id != "chiplog.execution.prepare-terminal-recovery-fault.v1"
        ):
            raise ValueError("diagnostic authentication requires the fixed terminal fault command")
        record = self.complete_records[0]
        if (
            record.owner != "agent_loop"
            or record.record_kind != "TERMINAL_RECOVERY_FAULT"
            or record.schema_id != "chiplog.execution.terminal-recovery-fault.v1"
        ):
            raise ValueError("diagnostic authentication requires the fixed terminal fault record")
        if (
            self.identity.command_fingerprint != self.command.fingerprint
            or self.command.fingerprint != self.authentication.final_request_fingerprint
        ):
            raise ValueError("diagnostic authentication final request fingerprint differs")
        return self


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


class SelectedPlanEffectBatchV2(BrokerDTO):
    """Original six PlanEffect records followed by selected ORDINARY admission rows."""

    kind: Literal["PLAN_EFFECT_ATOMIC_V2"] = "PLAN_EFFECT_ATOMIC_V2"
    schema_id: Literal["chiplog.owner-publication.plan-effect.v2"] = (
        "chiplog.owner-publication.plan-effect.v2"
    )
    operation: Literal["effects.publish_plan_effect.v2"] = "effects.publish_plan_effect.v2"
    identity: PublicationIdentity
    authentication: WorkerAuthentication
    expected: AuthoritativeReadManifest
    planning_command: OwnerCommandBytes
    effects_command: OwnerCommandBytes
    original_records: tuple[OwnerRecordBytes, ...] = Field(min_length=6, max_length=6)
    admission_records: tuple[OwnerRecordBytes, ...] = Field(min_length=1, max_length=2)
    complete_records: tuple[OwnerRecordBytes, ...] = Field(min_length=7, max_length=8)
    complete_batch_fingerprint: Digest

    @model_validator(mode="after")
    def exact_member_order(self) -> SelectedPlanEffectBatchV2:
        if self.complete_records != self.original_records + self.admission_records:
            raise ValueError("selected PlanEffect v2 member order differs")
        schemas = tuple(row.schema_id for row in self.admission_records)
        if schemas not in (
            ("chiplog.ingress.admission-capacity.v1",),
            (
                "chiplog.ingress.admission-epoch.v2",
                "chiplog.ingress.admission-capacity.v1",
            ),
        ):
            raise ValueError("selected PlanEffect v2 requires optional epoch and ordinary claim")
        return self


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


class DeliveryWorkGenesisBatchV1(BrokerDTO):
    """Closed one-record genesis derived from an already selected V4 completion."""

    kind: Literal["DELIVERY_WORK_GENESIS_V1"] = "DELIVERY_WORK_GENESIS_V1"
    schema_id: Literal["chiplog.owner-publication.delivery-work-genesis.v1"] = (
        "chiplog.owner-publication.delivery-work-genesis.v1"
    )
    operation: Literal["agent_loop.delivery_work.genesis.v1"] = (
        "agent_loop.delivery_work.genesis.v1"
    )
    identity: PublicationIdentity
    authentication: WorkerAuthentication
    expected: AuthoritativeReadManifest
    selected_decision_id: Identity
    selected_decision_fingerprint: Digest
    selected_source: DeliveryWorkSourceV1
    genesis_command: OwnerCommandBytes
    complete_records: tuple[OwnerRecordBytes, ...] = Field(min_length=1, max_length=1)
    complete_batch_fingerprint: Digest

    @model_validator(mode="after")
    def exact_genesis(self) -> DeliveryWorkGenesisBatchV1:
        if (
            len(
                {
                    self.identity.tenant_id,
                    self.expected.tenant_id,
                    self.selected_source.tenant_id,
                }
            )
            != 1
            or self.selected_decision_id != self.selected_source.selected_decision_id
            or self.selected_decision_fingerprint
            != self.selected_source.selected_decision_fingerprint
            or self.genesis_command.owner != DELIVERY_WORK_OWNER
            or self.genesis_command.schema_id != DELIVERY_WORK_GENESIS_COMMAND_SCHEMA
        ):
            raise ValueError("delivery work genesis selected source or command differs")
        command = PrepareDeliveryWorkGenesisV1.model_validate_json(
            self.genesis_command.canonical_bytes
        )
        if (
            command.canonical_bytes() != self.genesis_command.canonical_bytes
            or self.genesis_command.fingerprint
            != hashlib.sha256(self.genesis_command.canonical_bytes).hexdigest()
            or command
            != PrepareDeliveryWorkGenesisV1(
                selected_decision_id=self.selected_decision_id,
                selected_decision_fingerprint=self.selected_decision_fingerprint,
                selected_source=self.selected_source,
            )
        ):
            raise ValueError("delivery work genesis command bytes differ")
        expected = prepare_delivery_work_genesis(self.selected_source)
        record = self.complete_records[0]
        if (
            record.owner != DELIVERY_WORK_OWNER
            or record.record_kind != "DELIVERY_WORK_STATE"
            or record.record_id != expected.record_id
            or record.schema_id != DELIVERY_WORK_SCHEMA
            or record.canonical_bytes != expected.canonical_bytes()
            or record.fingerprint != hashlib.sha256(record.canonical_bytes).hexdigest()
        ):
            raise ValueError("delivery work genesis record differs")
        return self


DELIVERY_WORK_CLAIM_OPERATION = "agent_loop.delivery_work.claim.v1"


def decode_delivery_work_claim_command(
    operation: str, command: OwnerCommandBytes
) -> PrepareDeliveryWorkClaimV1:
    """Accept only the canonical agent-loop command on its fixed operation route."""
    if (
        operation != DELIVERY_WORK_CLAIM_OPERATION
        or command.owner != DELIVERY_WORK_OWNER
        or command.schema_id != DELIVERY_WORK_CLAIM_COMMAND_SCHEMA
        or command.fingerprint != hashlib.sha256(command.canonical_bytes).hexdigest()
    ):
        raise ValueError("delivery work claim command route, role, schema, or digest differs")
    try:
        decoded = PrepareDeliveryWorkClaimV1.model_validate_json(command.canonical_bytes)
    except ValidationError as error:
        raise ValueError("delivery work claim command bytes are invalid") from error
    if decoded.canonical_bytes() != command.canonical_bytes:
        raise ValueError("delivery work claim command bytes are not canonical")
    return decoded


class DeliveryWorkClaimBatchV1(BrokerDTO):
    """One descriptive g1 record; the broker binding authenticates its lease facts."""

    kind: Literal["DELIVERY_WORK_CLAIM_V1"] = "DELIVERY_WORK_CLAIM_V1"
    schema_id: Literal["chiplog.owner-publication.delivery-work-claim.v1"] = (
        "chiplog.owner-publication.delivery-work-claim.v1"
    )
    operation: Literal["agent_loop.delivery_work.claim.v1"] = DELIVERY_WORK_CLAIM_OPERATION
    identity: PublicationIdentity
    authentication: WorkerAuthentication
    expected: AuthoritativeReadManifest
    claim_command: OwnerCommandBytes
    complete_records: tuple[OwnerRecordBytes, ...] = Field(min_length=1, max_length=1)
    complete_batch_fingerprint: Digest

    @model_validator(mode="after")
    def descriptive_record_has_exact_authority_free_projection(self) -> DeliveryWorkClaimBatchV1:
        projection = decode_delivery_work_claim_command(
            self.operation, self.claim_command
        ).projection
        record = self.complete_records[0]
        if (
            self.identity.tenant_id != projection.selected_source.tenant_id
            or self.expected.tenant_id != self.identity.tenant_id
            or self.identity.command_fingerprint != self.claim_command.fingerprint
            or record.owner != DELIVERY_WORK_OWNER
            or record.record_kind != "DELIVERY_WORK_STATE"
            or record.record_id != projection.record_id
            or record.schema_id != DELIVERY_WORK_SCHEMA
            or record.fingerprint != hashlib.sha256(record.canonical_bytes).hexdigest()
        ):
            raise ValueError("delivery work claim batch projection differs")
        try:
            state = DeliveryWorkStateV1.model_validate_json(record.canonical_bytes)
        except ValidationError as error:
            raise ValueError("delivery work claim state bytes are invalid") from error
        if (
            state.canonical_bytes() != record.canonical_bytes
            or state.selected_source != projection.selected_source
            or state.predecessor_head != projection.predecessor_head
            or state.subject_id != projection.subject_id
            or state.record_id != projection.record_id
            or state.generation != 1
            or state.state != "CLAIMED"
        ):
            raise ValueError("delivery work claim state differs from command projection")
        return self


RegisteredPublication = Annotated[
    SingleOwnerBatch
    | PlanEffectBatch
    | SelectedPlanEffectBatchV2
    | CallEffectBatch
    | CompleteDeliveryBatch
    | CompleteDeliveryBatchV2
    | RejectedCompletionBatchV1
    | DeliveryWorkGenesisBatchV1
    | DeliveryWorkClaimBatchV1,
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
            "effects.publish_plan_effect.v2",
            "agent_loop.complete_acceptance",
            "agent_loop.complete_acceptance.v2",
            "agent_loop.reject_completion.v1",
            "agent_loop.delivery_work.genesis.v1",
            "agent_loop.delivery_work.claim.v1",
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
