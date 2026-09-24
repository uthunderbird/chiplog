"""Immutable original-workspace issuance wire values for the H1 recovery cut.

These values are evidence only.  Constructing them never authorizes a read,
an issuance, or an execution transition.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Literal

from pydantic import model_validator

from chiplog.capabilities.agent_loop.recovery_contracts import Digest, Identity, RecoveryDTO, UInt64


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class H1WorkspaceIssuanceRefV1(RecoveryDTO):
    schema_id: Literal["chiplog.execution.h1-workspace-issuance-ref.v1"] = (
        "chiplog.execution.h1-workspace-issuance-ref.v1"
    )
    tenant: Identity
    batch_id: Identity
    entry_id: Digest
    payload_digest: Digest


class H1PublicationRowV1(RecoveryDTO):
    tenant_id: Identity
    operation_kind: Identity
    idempotency_key: Identity
    request_fingerprint: Digest
    commit_sequence: UInt64
    record_ids_json: str


class H1RecordRowV1(RecoveryDTO):
    tenant_id: Identity
    record_id: Identity
    owner: Identity
    schema_id: Identity
    canonical_bytes_base64: str
    commit_sequence: UInt64

    def canonical_record_bytes(self) -> bytes:
        return base64.b64decode(self.canonical_bytes_base64.encode("ascii"), validate=True)


class H1WorkspaceSnapshotV1(RecoveryDTO):
    schema_id: Literal["chiplog.execution.h1-workspace-snapshot.v1"] = (
        "chiplog.execution.h1-workspace-snapshot.v1"
    )
    store_version: Literal[1] = 1
    tenant_head: UInt64
    deletion_generation: Identity
    deletion_frontier: UInt64
    publications: tuple[H1PublicationRowV1, ...]
    records: tuple[H1RecordRowV1, ...]
    release_fingerprint: Digest


class H1QueryProofV1(RecoveryDTO):
    slot: Literal["history", "planning", "journal", "calendar"]
    reader_id: Literal[
        "conversation.context_read.v1",
        "planning.workspace.read.v1",
        "journal.workspace.read.v1",
        "calendar.workspace.read.v1",
    ]
    request_json: str
    result_json: str


class H1CalendarOriginalReadV1(RecoveryDTO):
    provider_batch_json: str
    state_json: str
    operation_json: str
    result_json: str
    operation_fingerprint: Digest
    recipient_fingerprint: Digest
    proof_fingerprint: Digest
    result_digest: Digest
    cursor_token: str | None
    cursor_snapshot_id: str | None
    cursor_query_binding_base64: str | None
    cursor_last_order_key: str | None
    display_result_json: str


class H1WorkspaceSourcesV1(RecoveryDTO):
    """Exact independently selected source registry at the original R12 cut."""

    trusted_ingress_json: str
    conversation_bindings_json: tuple[str, ...]
    selected_loop_decisions: tuple[H1RetainedDecisionV1, ...]
    planning_sources: tuple[H1PlanningSourcesV1, ...]
    policy_record_id: Identity
    policy_payload_base64: str
    endpoint: Identity


class H1RetainedDecisionV1(RecoveryDTO):
    entry_id: Digest
    predecessor: str | None
    payload_base64: str


class H1PlanningSourcesV1(RecoveryDTO):
    intention_line_id: Identity
    source_reference_json: tuple[str, ...]


class H1DashboardIssuanceRefV1(RecoveryDTO):
    channel_id: Identity
    sequence: UInt64
    entry_id: Digest
    payload_digest: Digest


class H1OriginalWorkspaceIssuanceV1(RecoveryDTO):
    schema_id: Literal["chiplog.execution.h1-original-workspace-issuance.v1"] = (
        "chiplog.execution.h1-original-workspace-issuance.v1"
    )
    profile_id: Literal["chiplog.execution.h1-zero-call-recovery-frontier"] = (
        "chiplog.execution.h1-zero-call-recovery-frontier"
    )
    profile_version: Literal[2] = 2
    tenant: Identity
    run_id: Identity
    started_run_head: Identity
    turn_id: Identity
    worker_session: Identity
    workspace_member_json: str
    proposal_context_json: str
    snapshot: H1WorkspaceSnapshotV1
    queries: tuple[H1QueryProofV1, ...]
    calendar: H1CalendarOriginalReadV1
    sources: H1WorkspaceSourcesV1
    dashboard: H1DashboardIssuanceRefV1

    @model_validator(mode="after")
    def closed_query_slots(self) -> H1OriginalWorkspaceIssuanceV1:
        expected = (
            ("history", "conversation.context_read.v1"),
            ("planning", "planning.workspace.read.v1"),
            ("journal", "journal.workspace.read.v1"),
            ("calendar", "calendar.workspace.read.v1"),
        )
        if tuple((proof.slot, proof.reader_id) for proof in self.queries) != expected:
            raise ValueError("original workspace query slots differ")
        return self

    @property
    def payload_digest(self) -> str:
        return _digest(self.canonical_bytes())


class H1VerifiedWorkspaceClosure(RecoveryDTO):
    """Broker-private result of exact retained-byte verification."""

    issuance: H1WorkspaceIssuanceRefV1
    workspace_member_bytes: bytes
    proposal_context_bytes: bytes
    snapshot: H1WorkspaceSnapshotV1
    queries: tuple[H1QueryProofV1, ...]
    calendar: H1CalendarOriginalReadV1
    sources: H1WorkspaceSourcesV1
    dashboard: H1DashboardIssuanceRefV1
