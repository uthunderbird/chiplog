from __future__ import annotations

import hashlib

from chiplog.capabilities.projections.disclosure import label
from chiplog.capabilities.projections.r9_boundary import (
    WorkspaceRejected,
)
from chiplog.capabilities.projections.workspace_boundary import (
    DisclosureEnvelope,
    SourceReference,
    WorkspaceReadContext,
    WorkspaceReadRequest,
    WorkspaceReadResult,
    WorkspaceRow,
)


def context() -> WorkspaceReadContext:
    return WorkspaceReadContext(
        tenant_id="tenant",
        database_instance_id="db",
        principal_id="principal",
        principal_contour_head="contour",
        channel_id="channel",
        endpoint_binding_head="binding",
        broker_epoch=1,
        owner_id="projections",
        generation_id="generation",
        session_id="session",
        snapshot_id="snapshot",
        snapshot_frontier=2,
        verified_snapshot_bytes=b"issued-cut",
        invalidator_registry_digest="registry",
        policy_head="policy",
        deletion_fence_head="fence",
        observed_at_ns=10,
    )


def request() -> WorkspaceReadRequest:
    return WorkspaceReadRequest(
        context=context(),
        query="CONVERSATION_HISTORY",
        request_id="r",
        read_attempt_id="attempt",
        response_slot_id="slot",
        max_rows=2,
        after_cursor=None,
        detail_id=None,
        range_start_ns=None,
        range_end_ns=None,
    )


def source() -> SourceReference:
    return SourceReference(
        tenant_id="tenant",
        owner="ingress",
        record_id="source",
        record_version="1",
        content_digest=hashlib.sha256(b"secret").hexdigest(),
        label_head="label-head",
        label=label("ENDPOINT_RESTRICTED", ("local",)),
    )


def envelope(payload: bytes = b"secret") -> DisclosureEnvelope:
    return DisclosureEnvelope(
        tenant_id="tenant",
        content_digest=hashlib.sha256(payload).hexdigest(),
        sources=(source(),),
        label=source().label,
        policy_head="policy",
        contour_head="contour",
        deletion_fence_head="fence",
    )


class IssuedContext:
    """Independent hermetic authenticated-session leaf; payload cannot register itself."""

    def __init__(self) -> None:
        self.current = context()
        self.revoked = False

    def validate(self, candidate: WorkspaceReadContext) -> None:
        if self.revoked or candidate != self.current:
            raise WorkspaceRejected("not the independently issued peer/session cut")


class Heads:
    def validate_manifest(self, candidate: DisclosureEnvelope, cut: WorkspaceReadContext) -> None:
        if candidate.sources != (source(),):
            raise WorkspaceRejected("incomplete independently issued provenance")

    def validate(self, candidate: SourceReference, cut: WorkspaceReadContext) -> None:
        if candidate != source() or cut.tenant_id != candidate.tenant_id:
            raise WorkspaceRejected("not an independently current source head")


class Queries:
    def __init__(self) -> None:
        self.lagging = False
        self.other_cut = False

    async def read(self, query: WorkspaceReadRequest) -> WorkspaceReadResult:
        return WorkspaceReadResult(
            disposition="LAGGING" if self.lagging else "CURRENT",
            context=query.context.model_copy(update={"session_id": "foreign"})
            if self.other_cut
            else query.context,
            request_id=query.request_id,
            read_attempt_id=query.read_attempt_id,
            rows=(
                WorkspaceRow(
                    row_id="source",
                    row_version="1",
                    order_key="1",
                    canonical_payload=b"secret",
                    envelope=envelope(),
                ),
            ),
            next_cursor=None,
            reason=None,
        )
