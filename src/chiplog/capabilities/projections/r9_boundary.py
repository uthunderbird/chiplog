"""Immutable R9 application ports. Data is not authority."""

from __future__ import annotations

from typing import Literal, Protocol, Self, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .workspace_boundary import (
    DisclosureEnvelope,
    ProvenanceSubject,
    SourceReference,
    WorkspaceReadContext,
)


class Frozen(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, ser_json_bytes="base64", val_json_bytes="base64"
    )


class ScreenLocation(Frozen):
    dashboard_id: str = Field(pattern=r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
    screen_id: Literal["overview", "detail"] = "overview"
    item_id: str | None = None


class BudgetPolicy(Frozen):
    version: str = "r9.budget.v1"
    total: int = Field(gt=0)
    fixed: int = Field(ge=0)
    output: int = Field(ge=0)
    conversation_floor: int = Field(ge=32)


class BudgetAllocation(Frozen):
    dashboard_id: str
    requested: int
    allocated: int
    estimated: int
    truncated: bool


class ScreenSnapshotRef(Frozen):
    tenant_id: str
    snapshot_id: str
    location: ScreenLocation
    builder_id: str
    builder_version: str
    context: WorkspaceReadContext
    config_version: str
    budget_version: str
    token_budget: int
    budget_policy: BudgetPolicy
    renderer_version: Literal["agent-dashboard.1.0.0"] = "agent-dashboard.1.0.0"
    content_hash: str


class ScreenSnapshot(Frozen):
    ref: ScreenSnapshotRef
    screen_bytes: bytes
    rendered: str
    envelopes: tuple[DisclosureEnvelope, ...]
    disposition: Literal["CURRENT", "LAGGING"]


class ScreenSnapshotV2(ScreenSnapshot):
    schema_id: Literal["chiplog.workspace-screen.v2"] = "chiplog.workspace-screen.v2"
    subjects: tuple[ProvenanceSubject, ...]

    @model_validator(mode="after")
    def complete_subject_shape(self) -> Self:
        if len(self.subjects) != len(self.envelopes) or any(
            subject.tenant_id != self.ref.tenant_id for subject in self.subjects
        ):
            raise ValueError("screen subjects must cover every envelope in the same tenant")
        return self


class WorkspaceState(Frozen):
    tenant_id: str
    channel_id: str
    sequence: int = Field(ge=0)
    retained: tuple[str, ...]
    navigation: tuple[tuple[str, tuple[ScreenLocation, ...]], ...]
    screens: tuple[ScreenSnapshotV2 | ScreenSnapshot, ...]
    allocations: tuple[BudgetAllocation, ...]
    invalidations: tuple[str, ...]
    tool_availability: tuple[str, ...]


class NavigationCommand(Frozen):
    operation: Literal["focus", "open", "replace", "push", "pop", "back"]
    location: ScreenLocation


class ConversationEntry(Frozen):
    tenant_id: str
    conversation_id: str
    entry_id: str
    sequence: int = Field(gt=0)
    origin_channel_id: str
    visible_channels: tuple[str, ...] = Field(min_length=1)
    role: Literal["principal", "assistant"]
    accepted_bytes: bytes
    envelope: DisclosureEnvelope


class WorkspaceIntegrityError(ValueError):
    def __init__(self, operation: str, tenant_id: str, record_id: str) -> None:
        super().__init__(f"{operation}: tenant={tenant_id} record={record_id}: integrity failure")


class WorkspaceRejected(ValueError):
    """No content is released when validation fails."""


class SnapshotStore(Protocol):
    def save(self, state: WorkspaceState, expected_sequence: int) -> None: ...
    def load(self, tenant_id: str, channel_id: str, sequence: int) -> WorkspaceState: ...


class WorkspaceIssuancePort(Protocol):
    def select_verified(self, state: WorkspaceState, expected_sequence: int) -> None: ...
    def verify(self, state: WorkspaceState) -> None: ...


class DisclosureGuard(Protocol):
    def check(
        self, envelope: DisclosureEnvelope, context: WorkspaceReadContext, endpoint: str
    ) -> None: ...


@runtime_checkable
class SubjectDisclosureGuard(Protocol):
    def check_subject(
        self,
        subject: ProvenanceSubject,
        envelope: DisclosureEnvelope,
        context: WorkspaceReadContext,
        endpoint: str,
    ) -> None: ...


@runtime_checkable
class SubjectSourceHeadPort(Protocol):
    def validate_subject(
        self,
        subject: ProvenanceSubject,
        envelope: DisclosureEnvelope,
        context: WorkspaceReadContext,
    ) -> None: ...


class SourceHeadPort(Protocol):
    def validate_manifest(
        self, envelope: DisclosureEnvelope, context: WorkspaceReadContext
    ) -> None: ...

    def validate(self, source: SourceReference, context: WorkspaceReadContext) -> None: ...


class ReadContextPort(Protocol):
    def validate(self, context: WorkspaceReadContext) -> None: ...


class ConversationStore(Protocol):
    async def append(
        self, entry: ConversationEntry
    ) -> Literal["COMMITTED", "REPLAY", "CONFLICT"]: ...
    def entries(self, tenant_id: str, frontier: int) -> tuple[ConversationEntry, ...]: ...


class DerivativePort(Protocol):
    async def register(self, snapshot: ScreenSnapshot) -> None: ...
