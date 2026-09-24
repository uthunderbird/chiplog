"""Closed, additive ingress record wires.

These DTOs only preserve and interpret selected record bytes.  They neither
authenticate source evidence nor establish journal selection or CAS outcomes.
"""

from __future__ import annotations

import hashlib
import json
from typing import ClassVar, Literal, cast

from pydantic import model_validator

from ._ingress_contracts import (
    AdmissionBound,
    AuthenticationBinding,
    BlockedRebase,
    CustodyState,
    Digest,
    DrainManifest,
    Head,
    Identity,
    IngressDTO,
    LossSlot,
    ParserAttempt,
    PollMemberDisposition,
    PollPageManifest,
    RawStaged,
    ReadyGeneration,
    ReceiptToken,
    RetainedToken,
    SourceBinding,
    SourceClass,
    UInt64,
)
from .ingress_runtime_snapshot import AdmissionClass, AdmissionDeficit, ParserIdentity
from .ingress_transition_contracts import IngressCanonicalMember, IngressCommandIdentity


def canonical_ingress_record_bytes(record: IngressDTO) -> bytes:
    """Canonical JSON v1: Pydantic JSON-mode (base64 bytes), sorted compact UTF-8."""
    return json.dumps(
        record.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


class IngressRecordBase(IngressDTO):
    schema_id: str
    record_kind: str
    record_id: Identity
    tenant_id: Identity
    database_id: Identity
    command: IngressCommandIdentity
    command_kind: str
    command_fingerprint: Digest
    predecessor: Head | None
    source_heads: tuple[Head, ...]

    allowed_command_kinds: ClassVar[frozenset[str]] = frozenset()

    @model_validator(mode="after")
    def _bind_command_identity(self) -> IngressRecordBase:
        if (self.tenant_id, self.database_id) != (self.command.tenant_id, self.command.database_id):
            raise ValueError("record tenant/database differ from command identity")
        if self.command_kind not in self.allowed_command_kinds:
            raise ValueError("record command kind is not registered for this row")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_ingress_record_bytes(self)

    def fingerprint(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


class TokenRecord(IngressRecordBase):
    schema_id: Literal["chiplog.ingress.token-record.v1"] = "chiplog.ingress.token-record.v1"
    record_kind: Literal["TOKEN"] = "TOKEN"
    command_kind: Literal["ingress.allocate_receipt_token"]
    token: ReceiptToken
    acquisition: LossSlot | RetainedToken
    allowed_command_kinds: ClassVar[frozenset[str]] = frozenset({"ingress.allocate_receipt_token"})


class StagedRawRecord(IngressRecordBase):
    schema_id: Literal["chiplog.ingress.staged-raw-record.v1"] = (
        "chiplog.ingress.staged-raw-record.v1"
    )
    record_kind: Literal["STAGED_RAW"] = "STAGED_RAW"
    command_kind: Literal["ingress.stage_raw_bytes"]
    staged: RawStaged
    allowed_command_kinds: ClassVar[frozenset[str]] = frozenset({"ingress.stage_raw_bytes"})


class CustodySuccessorRecord(IngressRecordBase):
    schema_id: Literal["chiplog.ingress.custody-successor-record.v1"] = (
        "chiplog.ingress.custody-successor-record.v1"
    )
    record_kind: Literal["CUSTODY"] = "CUSTODY"
    command_kind: Literal["ingress.publish_custody_successor"]
    custody: CustodyState
    token: Head
    expected_token_state: Head
    allowed_command_kinds: ClassVar[frozenset[str]] = frozenset(
        {"ingress.publish_custody_successor"}
    )


class InboxRecord(IngressRecordBase):
    schema_id: Literal["chiplog.ingress.inbox-record.v1"] = "chiplog.ingress.inbox-record.v1"
    record_kind: Literal["INBOX"] = "INBOX"
    command_kind: Literal["ingress.publish_custody_successor"]
    inbox: Head
    custody: Head
    token: Head
    raw_bytes: bytes
    authentication: AuthenticationBinding
    allowed_command_kinds: ClassVar[frozenset[str]] = frozenset(
        {"ingress.publish_custody_successor"}
    )


class AdmissionRecord(IngressRecordBase):
    schema_id: Literal["chiplog.ingress.admission-record.v1"] = (
        "chiplog.ingress.admission-record.v1"
    )
    record_kind: Literal["ADMISSION"] = "ADMISSION"
    command_kind: Literal["ingress.admit_ready_generation", "ingress.unblock_to_ready_tail"]
    source_class: AdmissionClass
    generation: ReadyGeneration
    token: Head | None
    dependency_readiness: Head
    bound: AdmissionBound
    allowed_command_kinds: ClassVar[frozenset[str]] = frozenset(
        {"ingress.admit_ready_generation", "ingress.unblock_to_ready_tail"}
    )


class BoundRecord(IngressRecordBase):
    schema_id: Literal["chiplog.ingress.bound-record.v1"] = "chiplog.ingress.bound-record.v1"
    record_kind: Literal["BOUND"] = "BOUND"
    command_kind: Literal["ingress.admit_ready_generation", "ingress.unblock_to_ready_tail"]
    bound: AdmissionBound
    allowed_command_kinds: ClassVar[frozenset[str]] = frozenset(
        {"ingress.admit_ready_generation", "ingress.unblock_to_ready_tail"}
    )


class BoundRebaseRecord(IngressRecordBase):
    schema_id: Literal["chiplog.ingress.bound-rebase-record.v1"] = (
        "chiplog.ingress.bound-rebase-record.v1"
    )
    record_kind: Literal["BOUND"] = "BOUND"
    command_kind: Literal["ingress.block_and_rebase_prefix"]
    rebase: BlockedRebase
    expected_deficit: Head
    allowed_command_kinds: ClassVar[frozenset[str]] = frozenset({"ingress.block_and_rebase_prefix"})


class DeficitRecord(IngressRecordBase):
    schema_id: Literal["chiplog.ingress.deficit-record.v1"] = "chiplog.ingress.deficit-record.v1"
    record_kind: Literal["DEFICIT"] = "DEFICIT"
    command_kind: Literal["ingress.select_ready_head", "ingress.block_and_rebase_prefix"]
    deficit: AdmissionDeficit
    expected_deficit: Head
    scheduler_slot: UInt64
    selected_generation: Head | None
    blocked_generation: Head | None
    allowed_command_kinds: ClassVar[frozenset[str]] = frozenset(
        {"ingress.select_ready_head", "ingress.block_and_rebase_prefix"}
    )


class EpochRecord(IngressRecordBase):
    schema_id: Literal["chiplog.ingress.epoch-record.v1"] = "chiplog.ingress.epoch-record.v1"
    record_kind: Literal["EPOCH"] = "EPOCH"
    command_kind: Literal["ingress.quiesce_admission", "ingress.advance_drain_state"]
    epoch: Head
    fence: UInt64
    state: Literal["OPEN", "QUIESCING", "DRAINING", "CLOSED"]
    expected_epoch: Head
    expected_fence: UInt64
    quiescence_command: Head | None
    allowed_command_kinds: ClassVar[frozenset[str]] = frozenset(
        {"ingress.quiesce_admission", "ingress.advance_drain_state"}
    )


class DrainRecord(IngressRecordBase):
    schema_id: Literal["chiplog.ingress.drain-record.v1"] = "chiplog.ingress.drain-record.v1"
    record_kind: Literal["DRAIN"] = "DRAIN"
    command_kind: Literal["ingress.publish_drain_manifest", "ingress.advance_drain_state"]
    manifest: DrainManifest
    expected_epoch: Head
    expected_drain_manifest: Head | None
    allowed_command_kinds: ClassVar[frozenset[str]] = frozenset(
        {"ingress.publish_drain_manifest", "ingress.advance_drain_state"}
    )


class PollPageRecord(IngressRecordBase):
    schema_id: Literal["chiplog.ingress.poll-page-record.v1"] = (
        "chiplog.ingress.poll-page-record.v1"
    )
    record_kind: Literal["POLL_PAGE"] = "POLL_PAGE"
    command_kind: Literal["ingress.create_poll_page_manifest"]
    page: PollPageManifest
    raw_page_custody: Head
    raw_page_bytes: bytes
    allowed_command_kinds: ClassVar[frozenset[str]] = frozenset(
        {"ingress.create_poll_page_manifest"}
    )


class PollDispositionRecord(IngressRecordBase):
    schema_id: Literal["chiplog.ingress.poll-disposition-record.v1"] = (
        "chiplog.ingress.poll-disposition-record.v1"
    )
    record_kind: Literal["POLL_DISPOSITION"] = "POLL_DISPOSITION"
    command_kind: Literal["ingress.publish_poll_member_disposition"]
    page: Head
    member_index: UInt64
    disposition: PollMemberDisposition
    allowed_command_kinds: ClassVar[frozenset[str]] = frozenset(
        {"ingress.publish_poll_member_disposition"}
    )


class HandoffAuthorizationRecord(IngressRecordBase):
    schema_id: Literal["chiplog.ingress.handoff-authorization-record.v1"] = (
        "chiplog.ingress.handoff-authorization-record.v1"
    )
    record_kind: Literal["HANDOFF_AUTHORIZATION"] = "HANDOFF_AUTHORIZATION"
    command_kind: Literal["ingress.authorize_local_ack", "ingress.authorize_reconciliation_release"]
    handoff_id: Identity
    source_class: SourceClass
    authorization_kind: Literal[
        "ingress.authorize_local_ack", "ingress.authorize_reconciliation_release"
    ]
    token: Head
    custody: Head
    source_boundary: Head
    allowed_command_kinds: ClassVar[frozenset[str]] = frozenset(
        {"ingress.authorize_local_ack", "ingress.authorize_reconciliation_release"}
    )

    @model_validator(mode="after")
    def _same_authorization_kind(self) -> HandoffAuthorizationRecord:
        if self.command_kind != self.authorization_kind:
            raise ValueError("handoff authorization kind differs from command kind")
        return self


class CursorAuthorizationRecord(IngressRecordBase):
    schema_id: Literal["chiplog.ingress.cursor-authorization-record.v1"] = (
        "chiplog.ingress.cursor-authorization-record.v1"
    )
    record_kind: Literal["HANDOFF_AUTHORIZATION"] = "HANDOFF_AUTHORIZATION"
    command_kind: Literal["ingress.authorize_poll_cursor"]
    page: Head
    expected_applied_cursor: Head
    complete_ordered_dispositions: tuple[PollMemberDisposition, ...]
    allowed_command_kinds: ClassVar[frozenset[str]] = frozenset({"ingress.authorize_poll_cursor"})


class HandoffAttemptRecord(IngressRecordBase):
    schema_id: Literal["chiplog.ingress.handoff-attempt-record.v1"] = (
        "chiplog.ingress.handoff-attempt-record.v1"
    )
    record_kind: Literal["HANDOFF_ATTEMPT"] = "HANDOFF_ATTEMPT"
    command_kind: Literal[
        "ingress.issue_push_response_attempt", "ingress.issue_cli_response_attempt"
    ]
    handoff_id: Identity
    attempt_id: Identity
    authorization: Head
    endpoint: Head
    payload: bytes
    allowed_command_kinds: ClassVar[frozenset[str]] = frozenset(
        {"ingress.issue_push_response_attempt", "ingress.issue_cli_response_attempt"}
    )


class HandoffObservationRecord(IngressRecordBase):
    schema_id: Literal["chiplog.ingress.handoff-observation-record.v1"] = (
        "chiplog.ingress.handoff-observation-record.v1"
    )
    record_kind: Literal["HANDOFF_OBSERVATION"] = "HANDOFF_OBSERVATION"
    command_kind: Literal[
        "ingress.observe_push_local_completion",
        "ingress.observe_cli_local_completion",
        "ingress.observe_authenticated_provider_receipt",
        "ingress.complete_reconciliation_release",
    ]
    handoff_id: Identity
    issued_attempt: Head | None
    authorization: Head | None
    result: Literal["LOCAL_COMPLETE", "FAILED", "UNKNOWN", "AUTHENTICATED_RECEIPT", "RELEASED"]
    observation: Head
    raw_observation_bytes: bytes
    authenticated_source: Head | None
    selected_raw_custody: Head | None = None
    allowed_command_kinds: ClassVar[frozenset[str]] = frozenset(
        {
            "ingress.observe_push_local_completion",
            "ingress.observe_cli_local_completion",
            "ingress.observe_authenticated_provider_receipt",
            "ingress.complete_reconciliation_release",
        }
    )

    @model_validator(mode="after")
    def _bind_handoff_observation_shape(self) -> HandoffObservationRecord:
        local_commands = {
            "ingress.observe_push_local_completion",
            "ingress.observe_cli_local_completion",
        }
        if self.command_kind in local_commands:
            if (
                self.result not in {"LOCAL_COMPLETE", "FAILED", "UNKNOWN"}
                or self.issued_attempt is None
                or self.authorization is not None
                or self.authenticated_source is not None
                or self.selected_raw_custody is not None
            ):
                raise ValueError("local handoff observation shape differs from command")
        elif self.command_kind == "ingress.observe_authenticated_provider_receipt":
            if (
                self.result != "AUTHENTICATED_RECEIPT"
                or self.issued_attempt is None
                or self.authorization is not None
                or self.authenticated_source is None
                or self.selected_raw_custody is None
                or self.observation != self.authenticated_source
                or not self.raw_observation_bytes
            ):
                raise ValueError("provider receipt observation shape differs from command")
        elif (
            self.result != "RELEASED"
            or self.issued_attempt is not None
            or self.authorization is None
            or self.authenticated_source is not None
            or self.selected_raw_custody is not None
            or self.raw_observation_bytes != b""
        ):
            raise ValueError("reconciliation release observation shape differs from command")
        return self


class AppliedCursorRecord(IngressRecordBase):
    schema_id: Literal["chiplog.ingress.applied-cursor-record.v1"] = (
        "chiplog.ingress.applied-cursor-record.v1"
    )
    record_kind: Literal["APPLIED_CURSOR"] = "APPLIED_CURSOR"
    command_kind: Literal["ingress.apply_poll_cursor"]
    source: SourceBinding
    page: Head
    authorization: Head
    expected_applied_cursor: Head
    cursor_bytes: bytes
    allowed_command_kinds: ClassVar[frozenset[str]] = frozenset({"ingress.apply_poll_cursor"})


class PollRequestRecord(IngressRecordBase):
    schema_id: Literal["chiplog.ingress.poll-request-record.v1"] = (
        "chiplog.ingress.poll-request-record.v1"
    )
    record_kind: Literal["POLL_REQUEST"] = "POLL_REQUEST"
    command_kind: Literal["ingress.issue_poll_request"]
    request_id: Identity
    applied_cursor: Head
    source_contract: Head
    allowed_command_kinds: ClassVar[frozenset[str]] = frozenset({"ingress.issue_poll_request"})


class ParserSelectionRecord(IngressRecordBase):
    schema_id: Literal["chiplog.ingress.parser-selection-record.v1"] = (
        "chiplog.ingress.parser-selection-record.v1"
    )
    record_kind: Literal["PARSER_SELECTION"] = "PARSER_SELECTION"
    command_kind: Literal["ingress.select_quarantine_parser_attempt"]
    custody: Head
    expected_retry_head: Head
    parser: ParserIdentity
    allowed_command_kinds: ClassVar[frozenset[str]] = frozenset(
        {"ingress.select_quarantine_parser_attempt"}
    )


class ParserResultRecord(IngressRecordBase):
    schema_id: Literal["chiplog.ingress.parser-result-record.v1"] = (
        "chiplog.ingress.parser-result-record.v1"
    )
    record_kind: Literal["PARSER_RESULT"] = "PARSER_RESULT"
    command_kind: Literal["ingress.publish_quarantine_result"]
    custody: Head
    selected_attempt_head: Head
    attempt: ParserAttempt
    allowed_command_kinds: ClassVar[frozenset[str]] = frozenset(
        {"ingress.publish_quarantine_result"}
    )


type IngressRecord = (
    TokenRecord
    | StagedRawRecord
    | CustodySuccessorRecord
    | InboxRecord
    | AdmissionRecord
    | BoundRecord
    | BoundRebaseRecord
    | DeficitRecord
    | EpochRecord
    | DrainRecord
    | PollPageRecord
    | PollDispositionRecord
    | HandoffAuthorizationRecord
    | CursorAuthorizationRecord
    | HandoffAttemptRecord
    | HandoffObservationRecord
    | AppliedCursorRecord
    | PollRequestRecord
    | ParserSelectionRecord
    | ParserResultRecord
)

RECORD_ROWS: tuple[tuple[str, str, type[IngressRecordBase]], ...] = (
    ("TOKEN", "chiplog.ingress.token-record.v1", TokenRecord),
    ("STAGED_RAW", "chiplog.ingress.staged-raw-record.v1", StagedRawRecord),
    ("CUSTODY", "chiplog.ingress.custody-successor-record.v1", CustodySuccessorRecord),
    ("INBOX", "chiplog.ingress.inbox-record.v1", InboxRecord),
    ("ADMISSION", "chiplog.ingress.admission-record.v1", AdmissionRecord),
    ("BOUND", "chiplog.ingress.bound-record.v1", BoundRecord),
    ("BOUND", "chiplog.ingress.bound-rebase-record.v1", BoundRebaseRecord),
    ("DEFICIT", "chiplog.ingress.deficit-record.v1", DeficitRecord),
    ("EPOCH", "chiplog.ingress.epoch-record.v1", EpochRecord),
    ("DRAIN", "chiplog.ingress.drain-record.v1", DrainRecord),
    ("POLL_PAGE", "chiplog.ingress.poll-page-record.v1", PollPageRecord),
    ("POLL_DISPOSITION", "chiplog.ingress.poll-disposition-record.v1", PollDispositionRecord),
    (
        "HANDOFF_AUTHORIZATION",
        "chiplog.ingress.handoff-authorization-record.v1",
        HandoffAuthorizationRecord,
    ),
    (
        "HANDOFF_AUTHORIZATION",
        "chiplog.ingress.cursor-authorization-record.v1",
        CursorAuthorizationRecord,
    ),
    ("HANDOFF_ATTEMPT", "chiplog.ingress.handoff-attempt-record.v1", HandoffAttemptRecord),
    (
        "HANDOFF_OBSERVATION",
        "chiplog.ingress.handoff-observation-record.v1",
        HandoffObservationRecord,
    ),
    ("APPLIED_CURSOR", "chiplog.ingress.applied-cursor-record.v1", AppliedCursorRecord),
    ("POLL_REQUEST", "chiplog.ingress.poll-request-record.v1", PollRequestRecord),
    ("PARSER_SELECTION", "chiplog.ingress.parser-selection-record.v1", ParserSelectionRecord),
    ("PARSER_RESULT", "chiplog.ingress.parser-result-record.v1", ParserResultRecord),
)
RECORD_REGISTRY = {(kind, schema): cls for kind, schema, cls in RECORD_ROWS}


def decode_ingress_record(kind: str, schema_id: str, canonical_bytes: bytes) -> IngressRecord:
    cls = RECORD_REGISTRY.get((kind, schema_id))
    if cls is None:
        raise ValueError("unregistered ingress record kind/schema")
    record = cls.model_validate_json(canonical_bytes)
    if record.canonical_bytes() != canonical_bytes:
        raise ValueError("noncanonical ingress record bytes")
    return cast(IngressRecord, record)


def encode_ingress_member(record: IngressRecord) -> IngressCanonicalMember:
    raw = record.canonical_bytes()
    return IngressCanonicalMember(
        record_id=record.record_id,
        record_kind=record.record_kind,
        schema_id=record.schema_id,
        canonical_record_bytes=raw,
        fingerprint=hashlib.sha256(raw).hexdigest(),
    )


def decode_ingress_member(member: IngressCanonicalMember) -> IngressRecord:
    if hashlib.sha256(member.canonical_record_bytes).hexdigest() != member.fingerprint:
        raise ValueError("ingress member fingerprint differs from bytes")
    record = decode_ingress_record(
        member.record_kind, member.schema_id, member.canonical_record_bytes
    )
    if (record.record_id, record.record_kind, record.schema_id) != (
        member.record_id,
        member.record_kind,
        member.schema_id,
    ):
        raise ValueError("ingress member envelope differs from decoded record")
    return record
