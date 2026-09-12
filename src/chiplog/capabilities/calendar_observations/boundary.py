"""Owner-local workspace wire DTOs; data alone grants no read or mutation authority."""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class _Wire(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        ser_json_bytes="base64",
        val_json_bytes="base64",
    )


class DisclosureLabel(_Wire):
    lattice_version: Literal["chiplog.disclosure.v1"]
    value: Literal["UNRESTRICTED", "ENDPOINT_RESTRICTED", "DENY_ALL"]
    allowed_endpoints: tuple[str, ...]


class SourceReference(_Wire):
    tenant_id: str = Field(min_length=1)
    owner: str = Field(min_length=1)
    record_id: str = Field(min_length=1)
    record_version: str = Field(min_length=1)
    content_digest: str = Field(min_length=1)
    label_head: str = Field(min_length=1)
    label: DisclosureLabel


class DisclosureEnvelope(_Wire):
    tenant_id: str = Field(min_length=1)
    content_digest: str = Field(min_length=1)
    sources: tuple[SourceReference, ...] = Field(min_length=1)
    label: DisclosureLabel
    policy_head: str = Field(min_length=1)
    contour_head: str = Field(min_length=1)
    deletion_fence_head: str = Field(min_length=1)


class WorkspaceReadContext(_Wire):
    tenant_id: str = Field(min_length=1)
    database_instance_id: str = Field(min_length=1)
    principal_id: str = Field(min_length=1)
    principal_contour_head: str = Field(min_length=1)
    channel_id: str = Field(min_length=1)
    endpoint_binding_head: str = Field(min_length=1)
    broker_epoch: int = Field(gt=0)
    owner_id: str = Field(min_length=1)
    generation_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=1)
    snapshot_frontier: int = Field(ge=0)
    verified_snapshot_bytes: bytes = Field(min_length=1)
    invalidator_registry_digest: str = Field(min_length=1)
    policy_head: str = Field(min_length=1)
    deletion_fence_head: str = Field(min_length=1)
    observed_at_ns: int = Field(ge=0)


WorkspaceQuery = Literal[
    "CONVERSATION_HISTORY",
    "PLANNING_VIEW",
    "JOURNAL_CLAIMS",
    "CALENDAR_AGENDA",
    "CALENDAR_DETAIL",
]


class WorkspaceReadRequest(_Wire):
    context: WorkspaceReadContext
    query: WorkspaceQuery
    request_id: str = Field(min_length=1)
    read_attempt_id: str = Field(min_length=1)
    response_slot_id: str = Field(min_length=1)
    max_rows: int = Field(gt=0, le=1000)
    after_cursor: str | None
    detail_id: str | None
    range_start_ns: int | None
    range_end_ns: int | None


class WorkspaceRow(_Wire):
    row_id: str = Field(min_length=1)
    row_version: str = Field(min_length=1)
    order_key: str = Field(min_length=1)
    canonical_payload: bytes
    envelope: DisclosureEnvelope


class WorkspaceReadResult(_Wire):
    disposition: Literal[
        "CURRENT",
        "LAGGING",
        "STALE_OR_INDETERMINATE_READ",
        "DENIED",
        "NOT_FOUND",
    ]
    context: WorkspaceReadContext
    request_id: str = Field(min_length=1)
    read_attempt_id: str = Field(min_length=1)
    rows: tuple[WorkspaceRow, ...]
    next_cursor: str | None
    reason: str | None


class WorkspaceQueryPort(Protocol):
    async def read(self, request: WorkspaceReadRequest) -> WorkspaceReadResult: ...


__all__ = [
    "DisclosureEnvelope",
    "DisclosureLabel",
    "SourceReference",
    "WorkspaceQuery",
    "WorkspaceQueryPort",
    "WorkspaceReadContext",
    "WorkspaceReadRequest",
    "WorkspaceReadResult",
    "WorkspaceRow",
]
