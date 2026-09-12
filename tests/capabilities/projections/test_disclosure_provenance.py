from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from chiplog.adapters.driven.r9_fence import (
    R3SourceHeads,
)
from chiplog.capabilities.projections.disclosure import CurrentDisclosureGuard, label
from chiplog.capabilities.projections.r9_boundary import (
    WorkspaceRejected,
)
from chiplog.platform._sqlite import (
    EventAppender,
    FenceAdvanceCommand,
    PhysicalPublicationCommand,
    PhysicalRecord,
    SQLiteMaterializer,
)
from tests.support.workspace import IssuedContext, context, envelope, source


async def test_complete_provenance_cannot_drop_unrestricted_member(tmp_path: Path) -> None:
    with SQLiteMaterializer(
        tmp_path / "sources.db", record_contracts={"ingress": "ingress.v1"}
    ) as store:
        appender = EventAppender(store, capacity=2)
        await appender.advance_fence(FenceAdvanceCommand("tenant", "fence", 2))
        second = source().model_copy(
            update={
                "record_id": "second",
                "content_digest": hashlib.sha256(b"other").hexdigest(),
                "label": label("UNRESTRICTED"),
            }
        )
        await appender.submit(
            PhysicalPublicationCommand(
                "tenant",
                "ingress",
                "both",
                "both-fingerprint",
                0,
                "fence",
                2,
                0,
                (
                    PhysicalRecord(
                        "source", "ingress", "ingress.v1", b"secret", source().content_digest
                    ),
                    PhysicalRecord(
                        "second", "ingress", "ingress.v1", b"other", second.content_digest
                    ),
                ),
            )
        )
        contexts = IssuedContext()
        contexts.current = context().model_copy(update={"snapshot_frontier": 1})
        digest = hashlib.sha256(b"secret and other").hexdigest()
        closure = (second, source())
        heads = R3SourceHeads(
            store,
            contexts,
            {("ingress", "source"): source(), ("ingress", "second"): second},
            {digest: closure},
        )
        guard = CurrentDisclosureGuard(contexts, heads)
        derived = envelope().model_copy(update={"content_digest": digest, "sources": closure})
        guard.check(derived, contexts.current, "local")
        with pytest.raises(WorkspaceRejected, match="incomplete/substituted provenance"):
            guard.check(
                derived.model_copy(update={"sources": (source(),)}), contexts.current, "local"
            )
        await appender.close()
