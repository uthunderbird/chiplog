"""Typed framing over the journal owner's public query port."""

import hashlib

from chiplog.capabilities.evidence_journal.commands import (
    Heads,
    JournalQuery,
    JournalQueryPort,
    Subject,
)
from chiplog.capabilities.projections.disclosure import join
from chiplog.capabilities.projections.r9_boundary import ReadContextPort, WorkspaceRejected
from chiplog.capabilities.projections.workspace_boundary import (
    DisclosureEnvelope,
    SourceReference,
    WorkspaceReadRequest,
    WorkspaceReadResult,
    WorkspaceRow,
)


class JournalWorkspaceQueries:
    def __init__(
        self, queries: JournalQueryPort, peer: str, heads: Heads, contexts: ReadContextPort
    ) -> None:
        self._queries, self._peer, self._heads, self._contexts = queries, peer, heads, contexts

    async def read(self, request: WorkspaceReadRequest) -> WorkspaceReadResult:
        self._contexts.validate(request.context)
        if request.query != "JOURNAL_CLAIMS" or any(
            value is not None for value in (request.range_start_ns, request.range_end_ns)
        ):
            raise WorkspaceRejected("unsupported journal query")
        view = self._queries.project(
            self._peer,
            JournalQuery(
                tenant=request.context.tenant_id,
                expected_heads=self._heads,
                max_rows=request.max_rows,
                after_id=request.after_cursor,
                subject=Subject(kind="occurrence", identity=request.detail_id)
                if request.detail_id is not None
                else None,
            ),
        )
        if view.disposition != "CURRENT" or view.head != request.context.snapshot_frontier:
            raise WorkspaceRejected("journal query did not retain the batch cut")
        rows = []
        for row in view.rows:
            # Keep the entire owner row, status, claim and lineage. Only the framing
            # digest changes; no candidate becomes a positive assertion here.
            payload = row.model_dump_json().encode()
            envelope = DisclosureEnvelope.model_validate_json(
                row.record.claim.envelope.model_dump_json()
            ).model_copy(update={"content_digest": hashlib.sha256(payload).hexdigest()})
            closure: dict[tuple[str, str], SourceReference] = {}
            labels = []
            for ancestor in (*row.lineage, row.record):
                inherited = DisclosureEnvelope.model_validate_json(
                    ancestor.claim.envelope.model_dump_json()
                )
                labels.append(inherited.label)
                for source in inherited.sources:
                    key = source.owner, source.record_id
                    if closure.setdefault(key, source) != source:
                        raise WorkspaceRejected("journal lineage has conflicting source identities")
            envelope = envelope.model_copy(
                update={
                    "sources": tuple(closure[key] for key in sorted(closure)),
                    "label": join(tuple(labels)),
                }
            )
            rows.append(
                WorkspaceRow(
                    row_id=row.record.record_id,
                    row_version=row.record.fingerprint,
                    order_key=row.record.record_id,
                    canonical_payload=payload,
                    envelope=envelope,
                )
            )
        self._contexts.validate(request.context)
        return WorkspaceReadResult(
            disposition="CURRENT",
            context=request.context,
            request_id=request.request_id,
            read_attempt_id=request.read_attempt_id,
            rows=tuple(rows),
            next_cursor=view.next_cursor,
            reason=None,
        )


__all__ = ["JournalWorkspaceQueries"]
