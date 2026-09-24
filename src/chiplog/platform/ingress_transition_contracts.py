"""Broker-private callable ingress transitions; prepared bytes never license a handoff.

These commands preserve complete observations for independent verification. They
are not exposed as authority-bearing capability inputs. Actual source acquisition,
owner authentication, sole-writer selection and last-boundary checks stay private.
"""

from typing import Annotated, Literal, Protocol

from pydantic import Field

from ._ingress_contracts import (
    AdmissionBound,
    CustodyState,
    Digest,
    Head,
    Identity,
    IngressDTO,
    ParserAttempt,
    PollMemberDisposition,
    PollPageManifest,
    ReadyGeneration,
    ReceiptToken,
    UInt64,
)
from .ingress_runtime_snapshot import AdmissionClass, IngressRuntimeSnapshot, ParserIdentity


class LossSlotAcquisition(IngressDTO):
    kind: Literal["LOSS_SLOT"] = "LOSS_SLOT"


class RetainedAcquisition(IngressDTO):
    kind: Literal["RETAINED_SOURCE"] = "RETAINED_SOURCE"
    retention_proof: Head


class AllocateIngressToken(IngressDTO):
    kind: Literal["ingress.allocate_receipt_token"] = "ingress.allocate_receipt_token"
    token: ReceiptToken
    acquisition: Annotated[LossSlotAcquisition | RetainedAcquisition, Field(discriminator="kind")]


class StageIngressRaw(IngressDTO):
    kind: Literal["ingress.stage_raw_bytes"] = "ingress.stage_raw_bytes"
    token: Head
    expected_token_state: Head
    raw_bytes: bytes


class PublishIngressCustody(IngressDTO):
    kind: Literal["ingress.publish_custody_successor"] = "ingress.publish_custody_successor"
    token: Head
    expected_token_state: Head
    custody: CustodyState


class AdmitReadyGeneration(IngressDTO):
    kind: Literal["ingress.admit_ready_generation"] = "ingress.admit_ready_generation"
    source_class: AdmissionClass
    generation: ReadyGeneration
    token: Head | None
    dependency_readiness: Head
    bound: AdmissionBound


class SelectReadyHead(IngressDTO):
    kind: Literal["ingress.select_ready_head"] = "ingress.select_ready_head"
    source_class: AdmissionClass
    generation: Head
    expected_deficit: Head
    scheduler_slot: UInt64
    dependency_readiness: Head


class BlockAndRebasePrefix(IngressDTO):
    kind: Literal["ingress.block_and_rebase_prefix"] = "ingress.block_and_rebase_prefix"
    source_class: AdmissionClass
    blocked_generation: Head
    blocked_head: Head
    expected_deficit: Head
    consumed_skip_slot: UInt64


class UnblockReadyTail(IngressDTO):
    kind: Literal["ingress.unblock_to_ready_tail"] = "ingress.unblock_to_ready_tail"
    source_class: AdmissionClass
    exact_blocked_head: Head
    new_ready_generation: ReadyGeneration
    dependency_readiness: Head
    new_bound: AdmissionBound


class QuiesceAdmission(IngressDTO):
    kind: Literal["ingress.quiesce_admission"] = "ingress.quiesce_admission"
    expected_epoch: Head
    expected_fence: UInt64
    quiescence_command: Head


class PublishDrainSnapshot(IngressDTO):
    kind: Literal["ingress.publish_drain_manifest"] = "ingress.publish_drain_manifest"
    expected_epoch: Head
    complete_inventory_fingerprint: Digest


class AdvanceDrainState(IngressDTO):
    kind: Literal["ingress.advance_drain_state"] = "ingress.advance_drain_state"
    expected_epoch: Head
    expected_fence: UInt64
    expected_drain_manifest: Head
    target: Literal["DRAINING", "CLOSED"]
    complete_inventory_fingerprint: Digest
    producer_quiescence: Head


class CreatePollPage(IngressDTO):
    kind: Literal["ingress.create_poll_page_manifest"] = "ingress.create_poll_page_manifest"
    manifest: PollPageManifest
    raw_page_custody: Head
    raw_page_bytes: bytes


class PublishPollDisposition(IngressDTO):
    kind: Literal["ingress.publish_poll_member_disposition"] = (
        "ingress.publish_poll_member_disposition"
    )
    page: Head
    disposition: PollMemberDisposition


class AuthorizeCustodyHandoff(IngressDTO):
    kind: Literal["ingress.authorize_local_ack", "ingress.authorize_reconciliation_release"]
    token: Head
    custody: Head
    source_boundary: Head


class IssueIngressResponse(IngressDTO):
    kind: Literal["ingress.issue_push_response_attempt", "ingress.issue_cli_response_attempt"]
    authorization: Head
    attempt_id: Identity
    exact_endpoint: Head
    payload: bytes


class ObserveLocalIngressResponse(IngressDTO):
    kind: Literal["ingress.observe_push_local_completion", "ingress.observe_cli_local_completion"]
    issued_attempt: Head
    transport_observation: Head
    result: Literal["LOCAL_COMPLETE", "FAILED", "UNKNOWN"]
    raw_observation_bytes: bytes


class ObserveProviderIngressReceipt(IngressDTO):
    kind: Literal["ingress.observe_authenticated_provider_receipt"] = (
        "ingress.observe_authenticated_provider_receipt"
    )
    issued_attempt: Head
    selected_raw_custody: Head
    authenticated_source: Head
    raw_receipt: bytes = Field(min_length=1)


class CompleteReconciliationRelease(IngressDTO):
    kind: Literal["ingress.complete_reconciliation_release"] = (
        "ingress.complete_reconciliation_release"
    )
    authorization: Head
    source_release_observation: Head


class AuthorizePollCursor(IngressDTO):
    kind: Literal["ingress.authorize_poll_cursor"] = "ingress.authorize_poll_cursor"
    page: Head
    expected_applied_cursor: Head
    complete_ordered_dispositions: tuple[PollMemberDisposition, ...]


class ApplyPollCursor(IngressDTO):
    kind: Literal["ingress.apply_poll_cursor"] = "ingress.apply_poll_cursor"
    authorization: Head
    expected_applied_cursor: Head


class IssuePollRequest(IngressDTO):
    kind: Literal["ingress.issue_poll_request"] = "ingress.issue_poll_request"
    applied_cursor: Head
    source_contract: Head
    request_id: Identity


class SelectQuarantineParser(IngressDTO):
    kind: Literal["ingress.select_quarantine_parser_attempt"] = (
        "ingress.select_quarantine_parser_attempt"
    )
    custody: Head
    expected_retry_head: Head
    parser: ParserIdentity


class PublishQuarantineResult(IngressDTO):
    kind: Literal["ingress.publish_quarantine_result"] = "ingress.publish_quarantine_result"
    custody: Head
    selected_attempt_head: Head
    attempt: ParserAttempt


IngressTransitionCommand = Annotated[
    AllocateIngressToken
    | StageIngressRaw
    | PublishIngressCustody
    | AdmitReadyGeneration
    | SelectReadyHead
    | BlockAndRebasePrefix
    | UnblockReadyTail
    | QuiesceAdmission
    | PublishDrainSnapshot
    | AdvanceDrainState
    | CreatePollPage
    | PublishPollDisposition
    | AuthorizeCustodyHandoff
    | IssueIngressResponse
    | ObserveLocalIngressResponse
    | ObserveProviderIngressReceipt
    | CompleteReconciliationRelease
    | AuthorizePollCursor
    | ApplyPollCursor
    | IssuePollRequest
    | SelectQuarantineParser
    | PublishQuarantineResult,
    Field(discriminator="kind"),
]


class IngressCommandIdentity(IngressDTO):
    tenant_id: Identity
    database_id: Identity
    command_id: Identity
    canonicalization_version: Literal["chiplog.ingress.transition.v1"] = (
        "chiplog.ingress.transition.v1"
    )


class RetainedIngressSource(IngressDTO):
    """Full independently obtained source preimage, not just a caller's digest."""

    source: Head
    reader_id: Identity
    schema_id: Identity
    canonical_source_bytes: bytes = Field(min_length=1)


class IngressTransitionRequest(IngressDTO):
    schema_id: Literal["chiplog.ingress.transition-request.v1"] = (
        "chiplog.ingress.transition-request.v1"
    )
    identity: IngressCommandIdentity
    command: IngressTransitionCommand
    observed: IngressRuntimeSnapshot
    source_observations: tuple[RetainedIngressSource, ...]


class IngressCanonicalMember(IngressDTO):
    record_id: Identity
    record_kind: Literal[
        "TOKEN",
        "STAGED_RAW",
        "CUSTODY",
        "INBOX",
        "ADMISSION",
        "BOUND",
        "DEFICIT",
        "EPOCH",
        "DRAIN",
        "POLL_PAGE",
        "POLL_DISPOSITION",
        "HANDOFF_AUTHORIZATION",
        "HANDOFF_ATTEMPT",
        "HANDOFF_OBSERVATION",
        "APPLIED_CURSOR",
        "POLL_REQUEST",
        "PARSER_SELECTION",
        "PARSER_RESULT",
    ]
    schema_id: Identity
    canonical_record_bytes: bytes = Field(min_length=1)
    fingerprint: Digest


class PreparedIngressTransition(IngressDTO):
    kind: Literal["PREPARED_INGRESS_TRANSITION_V1"] = "PREPARED_INGRESS_TRANSITION_V1"
    source_request_fingerprint: Digest
    complete_records: tuple[IngressCanonicalMember, ...] = Field(min_length=1)
    complete_commitment: Digest


class IngressTransitionRejected(IngressDTO):
    kind: Literal["INGRESS_TRANSITION_REJECTED_V1"] = "INGRESS_TRANSITION_REJECTED_V1"
    code: Literal["HOLD", "STALE", "CONFLICT", "DENIED", "INVALID_INPUT", "CAPACITY"]
    reason: Identity


IngressPreparationResult = Annotated[
    PreparedIngressTransition | IngressTransitionRejected, Field(discriminator="kind")
]


class SelectedIngressPublication(IngressDTO):
    kind: Literal["SELECTED_INGRESS_PUBLICATION_V1"] = "SELECTED_INGRESS_PUBLICATION_V1"
    disposition: Literal["COMMITTED", "EXACT_REPLAY"]
    identity: IngressCommandIdentity
    original_request_fingerprint: Digest
    selected_decision: Head
    complete_records: tuple[Head, ...] = Field(min_length=1)
    complete_commitment: Digest


class UncertainIngressPublication(IngressDTO):
    kind: Literal["UNCERTAIN_INGRESS_PUBLICATION_V1"] = "UNCERTAIN_INGRESS_PUBLICATION_V1"
    identity: IngressCommandIdentity
    original_request_fingerprint: Digest
    reason: Identity


IngressPublicationResult = Annotated[
    SelectedIngressPublication | UncertainIngressPublication | IngressTransitionRejected,
    Field(discriminator="kind"),
]


class IngressTransitionPreparationPort(Protocol):
    def prepare_ingress(self, request: IngressTransitionRequest) -> IngressPreparationResult: ...


class IngressTransitionPublicationPort(Protocol):
    """Internal broker boundary: source capture/issuance are not supplied by callers.

    Prepared output never crosses this port as authority. Exact replay authenticates
    the current invocation and recovers original selected members. Durable corruption
    raises a typed integrity error with its cause; uncertainty requires original-key
    lookup and grants no ack/cursor/release permission.
    """

    async def publish_ingress(
        self, identity: IngressCommandIdentity, command: IngressTransitionCommand
    ) -> IngressPublicationResult: ...

    def selected_ingress(
        self, identity: IngressCommandIdentity
    ) -> SelectedIngressPublication | None: ...
