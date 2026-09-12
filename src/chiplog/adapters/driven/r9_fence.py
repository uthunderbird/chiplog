"""R9 bridge to the existing sole EventAppender and R3 guarded provenance store."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from types import MappingProxyType
from typing import Literal

from chiplog.capabilities.projections.r9_boundary import (
    ConversationEntry,
    ReadContextPort,
    ScreenSnapshot,
    WorkspaceIntegrityError,
    WorkspaceRejected,
)
from chiplog.capabilities.projections.workspace_boundary import (
    DisclosureEnvelope,
    SourceReference,
    WorkspaceReadContext,
)
from chiplog.platform._sqlite import (
    DerivativeRegistration,
    DerivativeRegistrationCommand,
    EventAppender,
    PhysicalPublicationCommand,
    PhysicalRecord,
    SQLiteMaterializer,
)

SCREEN_SINK = "chiplog.projections.workspace.v1"
CONVERSATION_OWNER = "conversation"
CONVERSATION_SCHEMA = "chiplog.conversation.v1"


class R3SourceHeads:
    def __init__(
        self,
        materializer: SQLiteMaterializer,
        contexts: ReadContextPort,
        current_sources: Mapping[tuple[str, str], SourceReference],
        derivations: Mapping[str, tuple[SourceReference, ...]] | None = None,
    ) -> None:
        self._materializer, self._contexts = materializer, contexts
        self._sources = MappingProxyType(dict(current_sources))
        self._derivations = MappingProxyType(dict(derivations or {}))

    def validate_manifest(
        self, envelope: DisclosureEnvelope, context: WorkspaceReadContext
    ) -> None:
        self._contexts.validate(context)
        expected = self._derivations.get(envelope.content_digest)
        if expected is None:
            direct = tuple(
                source
                for source in self._sources.values()
                if source.content_digest == envelope.content_digest
            )
            if len(direct) != 1:
                raise WorkspaceRejected("missing independently issued complete provenance")
            expected = direct
        if envelope.sources != expected:
            raise WorkspaceRejected("incomplete/substituted provenance manifest")

    def validate(self, source: SourceReference, context: WorkspaceReadContext) -> None:
        self._contexts.validate(context)
        if self._sources.get((source.owner, source.record_id)) != source:
            raise WorkspaceRejected("unverified source or label head")
        records = self._materializer.guarded_records(
            context.tenant_id, context.deletion_fence_head, context.snapshot_frontier
        )
        matches = [row for row in records if row[1] == source.record_id and row[2] == source.owner]
        try:
            if len(matches) != 1:
                raise ValueError("missing/foreign physical provenance")
            row = matches[0]
            if (
                str(row[5]) != source.record_version
                or not isinstance(row[4], bytes)
                or hashlib.sha256(row[4]).hexdigest() != source.content_digest
                or int(str(row[5])) > context.snapshot_frontier
            ):
                raise ValueError("physical provenance identity/digest mismatch")
        except ValueError as exc:
            raise WorkspaceIntegrityError(
                "provenance.read", context.tenant_id, source.record_id
            ) from exc


class R3ConversationStore:
    def __init__(
        self,
        materializer: SQLiteMaterializer,
        appender: EventAppender,
        contexts: ReadContextPort,
        context: WorkspaceReadContext,
        fence_frontier: int,
    ) -> None:
        self._materializer, self._appender = materializer, appender
        self._contexts, self._context = contexts, context
        self._fence_frontier = fence_frontier

    async def append(self, entry: ConversationEntry) -> Literal["COMMITTED", "REPLAY", "CONFLICT"]:
        self._contexts.validate(self._context)
        if (
            entry.tenant_id != self._context.tenant_id
            or entry.conversation_id != f"conversation:{entry.tenant_id}"
        ):
            raise WorkspaceRejected("canonical conversation tenant mismatch")
        rows = self._materializer.guarded_records(
            entry.tenant_id, self._context.deletion_fence_head, self._context.snapshot_frontier
        )
        entry_bytes = entry.model_dump_json().encode()
        encoded = json.dumps(
            {
                "entry_json": entry_bytes.decode(),
                "fingerprint": hashlib.sha256(entry_bytes).hexdigest(),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        history = self.entries(entry.tenant_id, 2**63 - 1)
        existing = [row for row in rows if row[1] == entry.entry_id]
        if existing:
            return (
                "REPLAY"
                if (existing[0][2] == CONVERSATION_OWNER and existing[0][4] == encoded)
                else "CONFLICT"
            )
        if entry.sequence != (history[-1].sequence if history else 0) + 1:
            return "CONFLICT"
        head = max((int(str(row[5])) for row in rows), default=0)

        def admission_guard() -> Literal["DENIED", "STALE", "INDETERMINATE"] | None:
            try:
                self._contexts.validate(self._context)
            except WorkspaceRejected:
                return "STALE"
            return None

        result = await self._appender.submit(
            PhysicalPublicationCommand(
                entry.tenant_id,
                "conversation.accept",
                entry.entry_id,
                hashlib.sha256(encoded).hexdigest(),
                head,
                self._context.deletion_fence_head,
                self._fence_frontier,
                self._context.snapshot_frontier,
                (
                    PhysicalRecord(
                        entry.entry_id,
                        CONVERSATION_OWNER,
                        CONVERSATION_SCHEMA,
                        encoded,
                        hashlib.sha256(encoded).hexdigest(),
                    ),
                ),
                admission_guard=admission_guard,
            )
        )
        if result.disposition in ("COMMITTED", "REPLAY", "CONFLICT"):
            return result.disposition
        raise WorkspaceRejected(f"conversation admission {result.disposition}")

    def entries(self, tenant_id: str, frontier: int) -> tuple[ConversationEntry, ...]:
        self._contexts.validate(self._context)
        if tenant_id != self._context.tenant_id:
            raise WorkspaceRejected("foreign canonical conversation")
        rows = self._materializer.guarded_records(
            tenant_id, self._context.deletion_fence_head, self._context.snapshot_frontier
        )
        result = []
        for row in rows:
            if row[2] != CONVERSATION_OWNER:
                continue  # Another canonical owner; it is not conversation input.
            try:
                if not isinstance(row[4], bytes) or row[3] != CONVERSATION_SCHEMA:
                    raise ValueError("conversation physical schema mismatch")
                record = json.loads(row[4])
                if (
                    not isinstance(record, dict)
                    or set(record) != {"entry_json", "fingerprint"}
                    or not isinstance(record["entry_json"], str)
                    or hashlib.sha256(record["entry_json"].encode()).hexdigest()
                    != record["fingerprint"]
                ):
                    raise ValueError("conversation canonical fingerprint mismatch")
                entry = ConversationEntry.model_validate_json(record["entry_json"])
                if (
                    entry.tenant_id != tenant_id
                    or entry.entry_id != row[1]
                    or entry.conversation_id != f"conversation:{tenant_id}"
                ):
                    raise ValueError("conversation physical/logical identity mismatch")
                if int(str(row[5])) <= frontier:
                    result.append(entry)
            except ValueError as exc:
                raise WorkspaceIntegrityError("conversation.read", tenant_id, str(row[1])) from exc
        result.sort(key=lambda entry: entry.sequence)
        if tuple(entry.sequence for entry in result) != tuple(range(1, len(result) + 1)):
            raise WorkspaceRejected("canonical conversation sequence gap/duplicate")
        return tuple(result)


class R3ScreenDerivatives:
    def __init__(
        self, appender: EventAppender, contexts: ReadContextPort, fence_frontier: int
    ) -> None:
        self._appender, self._contexts = appender, contexts
        self._frontier = fence_frontier

    async def register(self, snapshot: ScreenSnapshot) -> None:
        context = snapshot.ref.context
        self._contexts.validate(context)
        sources = tuple(
            sorted(
                {source.record_id for envelope in snapshot.envelopes for source in envelope.sources}
            )
        )
        if not sources:
            # Empty screen has only static registry labels, no source-bearing derivative.
            return
        canonical = json.dumps(
            {
                "tenant_id": context.tenant_id,
                "sink": SCREEN_SINK,
                "derivative_id": snapshot.ref.snapshot_id,
                "source_record_ids": sources,
                "source_epoch": self._frontier,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        await self._appender.register_derivative(
            DerivativeRegistrationCommand(
                context.tenant_id,
                DerivativeRegistration(
                    SCREEN_SINK,
                    snapshot.ref.snapshot_id,
                    sources,
                    self._frontier,
                    hashlib.sha256(canonical).hexdigest(),
                ),
                context.deletion_fence_head,
            )
        )
        self._contexts.validate(context)
