"""Serializable complete broker observations; neither completeness nor authority is self-proved."""

from typing import Annotated, Literal

from pydantic import Field

from ._ingress_contracts import (
    AdmissionBound,
    ClassReserve,
    CustodyState,
    Digest,
    DrainManifest,
    Head,
    Identity,
    IngressDTO,
    LossSlot,
    PollMemberDisposition,
    PollPageManifest,
    QuarantineCustody,
    QuarantineResult,
    RawStaged,
    ReadyGeneration,
    ReceiptToken,
    RetainedToken,
    SourceBinding,
    SourceClass,
    UInt64,
)

AdmissionClass = SourceClass | Literal["ORDINARY"]


class TokenRuntimeState(IngressDTO):
    token: ReceiptToken
    allocation: Annotated[LossSlot | RetainedToken, Field(discriminator="kind")]
    current_head: Head
    staged: RawStaged | None
    custody: CustodyState | None


class ParserIdentity(IngressDTO):
    attempt_id: Identity
    parser_id: Identity
    parser_version: Identity


class QuarantineRuntimeState(IngressDTO):
    custody_head: Head
    custody: QuarantineCustody
    current_head: Head
    selected_attempt: ParserIdentity | None
    result: QuarantineResult | None
    last_completed_attempt: ParserIdentity | None
    selected_retry_head: Head | None
    completed_attempt_ids: tuple[Identity, ...]


class AdmissionQueueEntry(IngressDTO):
    source_class: AdmissionClass
    generation: ReadyGeneration
    token_id: Identity | None
    blocked_head: Head | None


class AdmissionDeficit(IngressDTO):
    source_class: AdmissionClass
    head: Head
    value: UInt64


class PollPageRuntimeState(IngressDTO):
    page_head: Head
    page: PollPageManifest
    raw_page_custody: Head
    raw_page_bytes: bytes
    ordered_dispositions: tuple[PollMemberDisposition, ...]
    completion: Head | None
    cursor_authorization: Head | None
    applied_cursor: Head | None


class HandoffRuntimeState(IngressDTO):
    """Selected authorization and observed transport are separate durable facts."""

    handoff_id: Identity
    current_head: Head
    source_class: SourceClass
    original_token: Head
    original_custody: Head
    authorization: Head
    issued_attempt: Head | None
    issued_payload: bytes | None
    observation: Literal["UNATTEMPTED", "UNKNOWN", "LOCAL_COMPLETE", "AUTHENTICATED_RECEIPT"]
    observation_head: Head | None
    authenticated_source: Head | None


class PollCursorRuntimeState(IngressDTO):
    source: SourceBinding
    applied_cursor: Head
    cursor_bytes: bytes
    selected_authorization: Head | None
    latest_request: Head | None


class SelectedDrainSnapshot(IngressDTO):
    head: Head
    manifest: DrainManifest


class IngressRuntimeSnapshot(IngressDTO):
    """Complete writer-enumerated state, including non-ready and in-flight work.

    The broker must derive this cut independently and compare it again at selection.
    An empty tuple means observed empty, not an omitted family. Schema acceptance
    cannot establish that claim; omission/extra/duplicate paths need runtime checks.
    """

    schema_id: Literal["chiplog.ingress.runtime-snapshot.v1"] = (
        "chiplog.ingress.runtime-snapshot.v1"
    )
    tenant_id: Identity
    database_id: Identity
    tenant_commit_sequence: UInt64
    journal_head: Head
    materialization_commitment: Digest
    ingress_manifest: Head
    epoch: Head
    fence: UInt64
    state: Literal["OPEN", "QUIESCING", "DRAINING", "CLOSED"]
    tokens: tuple[TokenRuntimeState, ...]
    canonical_reserves: tuple[ClassReserve, ...]
    ready: tuple[AdmissionQueueEntry, ...]
    blocked: tuple[AdmissionQueueEntry, ...]
    bounds: tuple[AdmissionBound, ...]
    deficits: tuple[AdmissionDeficit, ...]
    scheduler_trace: tuple[Head, ...]
    quarantines: tuple[QuarantineRuntimeState, ...]
    parser_remainder_fences: tuple[tuple[Head, Head], ...]
    settled_tokens: tuple[tuple[Head, Head], ...]
    physical_occupancy: tuple[Head, ...]
    poll_pages: tuple[PollPageRuntimeState, ...]
    poll_cursors: tuple[PollCursorRuntimeState, ...]
    handoffs: tuple[HandoffRuntimeState, ...]
    drain: SelectedDrainSnapshot | None
    producer_quiescence: Head | None
    complete_inventory_fingerprint: Digest
