"""Effect-free adapter consuming only the existing public planning query contract."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from chiplog.capabilities.projections import PlanningProjectionQueries
from chiplog.capabilities.projections.disclosure import join
from chiplog.capabilities.projections.r9_boundary import ReadContextPort, WorkspaceRejected
from chiplog.capabilities.projections.workspace_boundary import (
    DisclosureEnvelope,
    SourceReference,
    WorkspaceReadRequest,
    WorkspaceReadResult,
    WorkspaceRow,
)
from chiplog.domain_primitives import RecordId, TenantId


class PlanningWorkspaceQueries:
    def __init__(
        self,
        queries: PlanningProjectionQueries,
        contexts: ReadContextPort,
        sources: Mapping[str, tuple[SourceReference, ...]],
    ) -> None:
        self._queries, self._contexts, self._sources = queries, contexts, dict(sources)

    async def read(self, request: WorkspaceReadRequest) -> WorkspaceReadResult:
        context = request.context
        self._contexts.validate(context)
        if (
            request.query != "PLANNING_VIEW"
            or request.after_cursor is not None
            or request.range_start_ns is not None
            or request.range_end_ns is not None
        ):
            raise WorkspaceRejected("unsupported planning workspace query")
        identities = tuple(sorted(self._sources))
        if request.detail_id is not None:
            identities = tuple(item for item in identities if item == request.detail_id)
        if len(identities) > request.max_rows:
            return WorkspaceReadResult(
                disposition="STALE_OR_INDETERMINATE_READ",
                context=context,
                request_id=request.request_id,
                read_attempt_id=request.read_attempt_id,
                rows=(),
                next_cursor=None,
                reason="complete planning source closure exceeds bound",
            )
        rows = []
        lagging = False
        for identity in identities:
            view = self._queries.get(
                TenantId(context.tenant_id), RecordId(TenantId(context.tenant_id), identity)
            )
            if view is None:
                raise WorkspaceRejected("planning source manifest references absent view")
            sources = self._sources[identity]
            if (
                not sources
                or view.tenant_id.value != context.tenant_id
                or view.intention_line_id.value != identity
                or view.commit_sequence > context.snapshot_frontier
            ):
                raise WorkspaceRejected("planning view outside verified cut")
            lagging |= view.commit_sequence < context.snapshot_frontier
            payload = json.dumps(
                {
                    "intention_line_id": identity,
                    "purpose": view.purpose,
                    "activity": view.activity,
                    "personal_outcome": view.personal_outcome,
                    "revision_id": view.revision_id.value,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            rows.append(
                WorkspaceRow(
                    row_id=identity,
                    row_version=view.revision_id.value,
                    order_key=identity,
                    canonical_payload=payload,
                    envelope=DisclosureEnvelope(
                        tenant_id=context.tenant_id,
                        content_digest=hashlib.sha256(payload).hexdigest(),
                        sources=sources,
                        label=join(tuple(source.label for source in sources)),
                        policy_head=context.policy_head,
                        contour_head=context.principal_contour_head,
                        deletion_fence_head=context.deletion_fence_head,
                    ),
                )
            )
        self._contexts.validate(context)
        return WorkspaceReadResult(
            disposition="LAGGING" if lagging else "CURRENT",
            context=context,
            request_id=request.request_id,
            read_attempt_id=request.read_attempt_id,
            rows=tuple(rows),
            next_cursor=None,
            reason=None,
        )
