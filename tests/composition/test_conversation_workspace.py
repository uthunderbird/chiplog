from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from chiplog.adapters.driven.r9_fence import (
    CONVERSATION_OWNER,
    CONVERSATION_SCHEMA,
    SCREEN_SINK,
    R3ConversationStore,
    R3ScreenDerivatives,
    R3SourceHeads,
)
from chiplog.capabilities.projections.conversation import ConversationHistory
from chiplog.capabilities.projections.disclosure import CurrentDisclosureGuard
from chiplog.capabilities.projections.provenance import ProvenanceBinding, ProvenanceSubject
from chiplog.capabilities.projections.r9_boundary import (
    BudgetPolicy,
    ConversationEntry,
)
from chiplog.composition.r9 import build_r9_component
from chiplog.platform._sqlite import (
    EventAppender,
    FenceAdvanceCommand,
    PhysicalPublicationCommand,
    PhysicalRecord,
    SQLiteMaterializer,
)
from tests.support.planning_workspace import empty_planning
from tests.support.workspace import IssuedContext, context, envelope, request, source


@pytest.mark.parametrize("bound", (False, True))
async def test_canonical_appender_history_cursor_replay_and_fence(
    tmp_path: Path, bound: bool
) -> None:
    with SQLiteMaterializer(
        tmp_path / "canonical.db",
        record_contracts={"ingress": "ingress.v1", CONVERSATION_OWNER: CONVERSATION_SCHEMA},
        derivative_contracts=(SCREEN_SINK,),
        managed_derivative_sinks=(SCREEN_SINK,),
    ) as materializer:
        appender = EventAppender(materializer, capacity=4)
        await appender.advance_fence(FenceAdvanceCommand("tenant", "fence", 10))
        latest_source = source().model_copy(
            update={
                "record_id": "latest",
                "content_digest": hashlib.sha256(b"latest-message").hexdigest(),
            }
        )
        latest_envelope = envelope(b"latest-message").model_copy(
            update={"sources": (latest_source,)}
        )
        await appender.submit(
            PhysicalPublicationCommand(
                "tenant",
                "ingress",
                "source",
                "fingerprint",
                0,
                "fence",
                10,
                0,
                (
                    PhysicalRecord(
                        "source",
                        "ingress",
                        "ingress.v1",
                        b"secret",
                        hashlib.sha256(b"secret").hexdigest(),
                    ),
                    PhysicalRecord(
                        "latest",
                        "ingress",
                        "ingress.v1",
                        b"latest-message",
                        latest_source.content_digest,
                    ),
                ),
            )
        )
        contexts = IssuedContext()
        contexts.current = context().model_copy(update={"snapshot_frontier": 1})
        store = R3ConversationStore(materializer, appender, contexts, contexts.current, 10)
        guard = CurrentDisclosureGuard(
            contexts,
            R3SourceHeads(
                materializer,
                contexts,
                {("ingress", "source"): source(), ("ingress", "latest"): latest_source},
                bindings=tuple(
                    ProvenanceBinding(
                        subject=ProvenanceSubject(
                            tenant_id="tenant",
                            producer="core.conversation",
                            record_id=entry_id,
                            revision=revision,
                        ),
                        content_digest=item.content_digest,
                        sources=(item,),
                    )
                    for entry_id, revision, item in (
                        ("entry", "1", source()),
                        ("entry2", "2", latest_source),
                    )
                ),
            ),
        )
        history = ConversationHistory(store, contexts, guard, "local", subject_bound=bound)
        ingress_request = request().model_copy(update={"context": contexts.current})
        entry = ConversationEntry(
            tenant_id="tenant",
            conversation_id="conversation:tenant",
            entry_id="entry",
            sequence=1,
            origin_channel_id="channel",
            visible_channels=("channel",),
            role="principal",
            accepted_bytes=b"secret",
            envelope=envelope(),
        )
        assert await history.accept(entry, ingress_request) == "COMMITTED"
        assert await history.accept(entry, ingress_request) == "REPLAY"
        assert (
            await history.accept(entry.model_copy(update={"role": "assistant"}), ingress_request)
            == "CONFLICT"
        )
        assert (
            await history.accept(
                entry.model_copy(
                    update={
                        "entry_id": "entry2",
                        "sequence": 2,
                        "accepted_bytes": b"latest-message",
                        "envelope": latest_envelope,
                    }
                ),
                ingress_request,
            )
            == "COMMITTED"
        )
        contexts.current = context().model_copy(update={"snapshot_frontier": 3})
        store = R3ConversationStore(materializer, appender, contexts, contexts.current, 10)
        direct = ConversationHistory(store, contexts, guard, "local", subject_bound=bound)
        direct_history = await direct.context_read(
            request().model_copy(update={"context": contexts.current})
        )
        assert tuple(row.row_id for row in direct_history.rows) == ("entry", "entry2")
        component = build_r9_component(
            tmp_path / "screens.db",
            store,
            contexts,
            guard,
            empty_planning(contexts),
            "local",
            R3ScreenDerivatives(appender, contexts, 10),
        )
        read_request = request().model_copy(update={"context": contexts.current, "max_rows": 1})
        first = await component.history(read_request)
        assert [row.row_id for row in first.rows] == ["entry"]
        second = await component.history(
            read_request.model_copy(update={"after_cursor": first.next_cursor})
        )
        assert [row.row_id for row in second.rows] == ["entry2"]
        assert second.next_cursor is None
        with pytest.raises(ValueError, match="unknown/stale history cursor"):
            await component.history(read_request.model_copy(update={"after_cursor": "invented"}))
        # Human presentation remains scoped; canonical agent context crosses channels.
        contexts.current = contexts.current.model_copy(update={"channel_id": "other"})
        other_store = R3ConversationStore(materializer, appender, contexts, contexts.current, 10)
        component = build_r9_component(
            tmp_path / "other.db",
            other_store,
            contexts,
            guard,
            empty_planning(contexts),
            "local",
            R3ScreenDerivatives(appender, contexts, 10),
        )
        read_request = read_request.model_copy(update={"context": contexts.current})
        assert (await component.history(read_request)).rows == ()
        recent = await component.agent_history(read_request)
        assert [row.row_id for row in recent.rows] == ["entry2"]
        assert recent.next_cursor is None
        assert recent.rows[0].canonical_payload == b"latest-message"
        recent_pair = await component.agent_history(read_request.model_copy(update={"max_rows": 2}))
        assert [row.row_id for row in recent_pair.rows] == ["entry", "entry2"]
        state = await component.refresh(
            read_request, BudgetPolicy(total=100, fixed=0, output=0, conversation_floor=32)
        )
        assert "latest-message" in component.context_text(state, contexts.current)
        assert "secret" not in component.context_text(state, contexts.current)
        refreshed = await component.refresh(
            read_request, BudgetPolicy(total=100, fixed=0, output=0, conversation_floor=32), state
        )
        assert refreshed.screens[0].ref.snapshot_id == state.screens[0].ref.snapshot_id
        await appender.advance_fence(FenceAdvanceCommand("tenant", "new-fence", 11))
        with pytest.raises(ValueError, match="fence generation mismatch"):
            await component.agent_history(read_request)
        with pytest.raises(ValueError, match="fence generation mismatch"):
            component.replay("tenant", "other", 1, contexts.current)
        await appender.close()
