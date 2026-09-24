"""Canonical accepted conversation history through an independently authenticated cut."""

from __future__ import annotations

import hashlib

from .disclosure import verify_payload
from .r9_boundary import (
    ConversationEntry,
    ConversationStore,
    DisclosureGuard,
    ReadContextPort,
    SubjectDisclosureGuard,
    WorkspaceRejected,
)
from .workspace_boundary import (
    ProvenanceSubject,
    WorkspaceReadContext,
    WorkspaceReadRequest,
    WorkspaceReadResult,
    WorkspaceRow,
)


class ConversationHistory:
    def __init__(
        self,
        store: ConversationStore,
        contexts: ReadContextPort,
        guard: DisclosureGuard,
        endpoint: str,
        *,
        subject_bound: bool = False,
    ) -> None:
        self._store, self._contexts, self._guard, self._endpoint = store, contexts, guard, endpoint
        self._subject_bound = subject_bound

    def _check_entry(self, entry: ConversationEntry, context: WorkspaceReadContext) -> None:
        if self._subject_bound:
            if not isinstance(self._guard, SubjectDisclosureGuard):
                raise WorkspaceRejected("conversation requires subject-bound disclosure")
            self._guard.check_subject(
                ProvenanceSubject(
                    tenant_id=entry.tenant_id,
                    producer="core.conversation",
                    record_id=entry.entry_id,
                    revision=str(entry.sequence),
                ),
                entry.envelope,
                context,
                self._endpoint,
            )
        else:
            self._guard.check(entry.envelope, context, self._endpoint)

    async def accept(self, entry: ConversationEntry, request: WorkspaceReadRequest) -> str:
        """Trusted accepted-ingress port; never included in model tool inventory."""
        self._contexts.validate(request.context)
        if (
            entry.tenant_id != request.context.tenant_id
            or entry.origin_channel_id != request.context.channel_id
            or entry.visible_channels != tuple(sorted(set(entry.visible_channels)))
            or entry.origin_channel_id not in entry.visible_channels
        ):
            raise WorkspaceRejected("conversation ingress identity/visibility mismatch")
        verify_payload(entry.accepted_bytes, entry.envelope)
        self._check_entry(entry, request.context)
        return await self._store.append(entry)

    async def read(self, request: WorkspaceReadRequest) -> WorkspaceReadResult:
        return await self._read(request, channel_scoped=True)

    async def context_read(self, request: WorkspaceReadRequest) -> WorkspaceReadResult:
        """Agent context port: same principal contour; disclosure still gates every row."""
        return await self._read(request, channel_scoped=False)

    async def _read(
        self, request: WorkspaceReadRequest, *, channel_scoped: bool
    ) -> WorkspaceReadResult:
        context = request.context
        self._contexts.validate(context)
        if (
            request.query != "CONVERSATION_HISTORY"
            or request.detail_id is not None
            or request.range_start_ns is not None
            or request.range_end_ns is not None
        ):
            raise WorkspaceRejected("unsupported history query")
        entries = self._store.entries(context.tenant_id, context.snapshot_frontier)
        visible = tuple(
            entry
            for entry in entries
            if not channel_scoped or context.channel_id in entry.visible_channels
        )
        if not channel_scoped and request.after_cursor is not None:
            raise WorkspaceRejected("agent context uses the recent tail, not a history cursor")
        after = 0
        if request.after_cursor is not None:
            matching = [
                entry.sequence
                for entry in visible
                if self._cursor(request, entry.sequence) == request.after_cursor
            ]
            if len(matching) != 1:
                raise WorkspaceRejected("unknown/stale history cursor")
            after = matching[0]
        candidates = tuple(entry for entry in visible if entry.sequence > after)
        page = candidates[: request.max_rows] if channel_scoped else candidates[-request.max_rows :]
        rows = []
        for entry in page:
            verify_payload(entry.accepted_bytes, entry.envelope)
            if self._subject_bound:
                # Original conversation bytes stay immutable. Legacy accepted
                # assistants retained attempt order; the new projection sorts
                # without dropping any member, then checks the independently
                # reconstructed complete closure under the original entry ID.
                entry = entry.model_copy(
                    update={
                        "envelope": entry.envelope.model_copy(
                            update={
                                "sources": tuple(
                                    sorted(
                                        entry.envelope.sources,
                                        key=lambda s: (s.owner, s.record_id, s.record_version),
                                    )
                                )
                            }
                        )
                    }
                )
            self._check_entry(entry, context)
            rows.append(
                WorkspaceRow(
                    row_id=entry.entry_id,
                    row_version=str(entry.sequence),
                    order_key=f"{entry.sequence:020d}",
                    canonical_payload=entry.accepted_bytes,
                    envelope=entry.envelope,
                )
            )
        # Revalidate after owner data acquisition; the external broker owns enqueue ordering.
        self._contexts.validate(context)
        return WorkspaceReadResult(
            disposition="CURRENT",
            context=context,
            request_id=request.request_id,
            read_attempt_id=request.read_attempt_id,
            rows=tuple(rows),
            next_cursor=self._cursor(request, page[-1].sequence)
            if channel_scoped and len(candidates) > len(page)
            else None,
            reason=None,
        )

    @staticmethod
    def _cursor(request: WorkspaceReadRequest, sequence: int) -> str:
        return hashlib.sha256(
            request.context.model_dump_json().encode() + f":history:{sequence}".encode()
        ).hexdigest()
