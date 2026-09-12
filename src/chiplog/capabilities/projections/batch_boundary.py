"""Stage-1 public batch values. Possessing a value never authorizes a proposal."""

from typing import Literal, Protocol

from pydantic import Field

from .r9_boundary import Frozen, WorkspaceState
from .workspace_boundary import DisclosureLabel, WorkspaceReadContext, WorkspaceReadResult


class WorkspaceBatch(Frozen):
    batch_id: str = Field(min_length=1)
    context: WorkspaceReadContext
    policy_binding_digest: str = Field(min_length=1)
    history: WorkspaceReadResult
    planning: WorkspaceReadResult
    journal: WorkspaceReadResult
    calendar: WorkspaceReadResult
    external_context: WorkspaceReadContext
    dashboard: WorkspaceState
    label: DisclosureLabel
    history_complete: bool


class ProposalContext(Frozen):
    kind: Literal["WORKSPACE_EVIDENCE_ONLY"] = "WORKSPACE_EVIDENCE_ONLY"
    batch: WorkspaceBatch
    history_record_ids: tuple[str, ...]
    label: DisclosureLabel


class WorkspaceBatchPort(Protocol):
    async def read_batch(self, *, max_rows: int = 100) -> WorkspaceBatch: ...
    def proposal_context(self, batch: WorkspaceBatch) -> ProposalContext: ...
    async def history(
        self, batch: WorkspaceBatch, *, max_rows: int = 100, after_cursor: str | None = None
    ) -> WorkspaceReadResult: ...
    async def calendar_detail(
        self, batch: WorkspaceBatch, record_id: str
    ) -> WorkspaceReadResult: ...


__all__ = ["ProposalContext", "WorkspaceBatch", "WorkspaceBatchPort"]
