import asyncio
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from chiplog.capabilities.calendar_observations.boundary import (
    WorkspaceReadRequest as CalendarRequest,
)
from chiplog.capabilities.calendar_observations.boundary import (
    WorkspaceReadResult as CalendarResult,
)
from chiplog.capabilities.projections.r9_boundary import ConversationEntry, WorkspaceRejected
from chiplog.capabilities.projections.workspace_boundary import SourceReference
from chiplog.platform.calendar_read_ledger import CalendarReadLedger
from tests.support.workspace_batch import workspace


async def test_all_families_share_internal_cut_and_calendar_keeps_external_provenance(
    tmp_path: Path,
) -> None:
    async with workspace(tmp_path) as (runtime, _):
        batch = await runtime.read_batch()
        assert batch.history.rows[0].canonical_payload.startswith("Я выполнил".encode())
        assert (
            batch.context
            == batch.history.context
            == batch.planning.context
            == batch.journal.context
        )
        assert batch.external_context == batch.calendar.context != batch.context
        assert {s.ref.location.dashboard_id for s in batch.dashboard.screens} == {
            "core.conversation",
            "core.planning",
            "core.journal",
            "core.calendar",
        }
        assert batch.dashboard.retained == ("core.planning", "core.journal", "core.calendar")
        assert batch.dashboard.screens[-1].disposition == "LAGGING"
        proposal = runtime.proposal_context(batch)
        assert proposal.history_record_ids == ("ingress-1",)
        assert proposal.label == batch.label
        second = await runtime.read_batch()
        assert second.context.snapshot_id != batch.context.snapshot_id
        with pytest.raises(WorkspaceRejected):
            runtime.proposal_context(batch)


async def test_changed_batch_or_peer_cannot_authorize_proposal(tmp_path: Path) -> None:
    async with workspace(tmp_path) as (runtime, _):
        batch = await runtime.read_batch()
        with pytest.raises(WorkspaceRejected):
            runtime.proposal_context(batch.model_copy(update={"history_complete": False}))
        runtime.ingress.revoke("peer")
        with pytest.raises(WorkspaceRejected):
            runtime.proposal_context(batch)


async def test_concurrent_journal_commit_invalidates_batch_without_reacquisition(
    tmp_path: Path,
) -> None:
    async with workspace(tmp_path) as (runtime, command):
        writer = asyncio.create_task(runtime.journal.execute("peer", command))
        with pytest.raises(WorkspaceRejected, match="invalidated before release"):
            await runtime.read_batch()
        assert (await writer).disposition == "COMMITTED"
        assert runtime._issued is None
        fresh = await runtime.read_batch()
        assert fresh.journal.rows


async def test_history_bound_requires_all_committed_history(
    tmp_path: Path,
) -> None:
    async with workspace(tmp_path) as (runtime, _):
        first = await runtime.read_batch(max_rows=1)
        entry = ConversationEntry(
            tenant_id="tenant",
            conversation_id="conversation:tenant",
            entry_id="entry-2",
            sequence=2,
            origin_channel_id="channel1",
            visible_channels=("channel1",),
            role="principal",
            accepted_bytes=first.history.rows[0].canonical_payload,
            envelope=first.history.rows[0].envelope,
        )
        assert await runtime.accept(entry) == "COMMITTED"
        with pytest.raises(WorkspaceRejected, match="history exceeds"):
            await runtime.read_batch(max_rows=1)
        second = await runtime.read_batch(max_rows=2)
        assert runtime.proposal_context(second).history_record_ids == ("ingress-1", "entry-2")


@pytest.mark.parametrize("mutation", ["frontier", "history", "label", "sources", "external"])
async def test_forged_or_narrowed_batch_never_replaces_issued_evidence(
    tmp_path: Path, mutation: str
) -> None:
    async with workspace(tmp_path) as (runtime, _):
        batch = await runtime.read_batch()
        changes: dict[str, dict[str, object]] = {
            "frontier": {"context": batch.context.model_copy(update={"snapshot_frontier": 999})},
            "history": {"history": batch.history.model_copy(update={"rows": ()})},
            "label": {"label": batch.label.model_copy(update={"value": "DENY_ALL"})},
            "sources": {
                "history": batch.history.model_copy(
                    update={
                        "rows": (
                            batch.history.rows[0].model_copy(
                                update={
                                    "envelope": batch.history.rows[0].envelope.model_copy(
                                        update={"sources": ()}
                                    )
                                }
                            ),
                        )
                    }
                )
            },
            "external": {"external_context": batch.context},
        }
        with pytest.raises(WorkspaceRejected):
            runtime.proposal_context(batch.model_copy(update=changes[mutation]))


async def test_calendar_invalidation_rejects_previously_released_proposal_context(
    tmp_path: Path,
) -> None:
    async with workspace(tmp_path) as (runtime, _):
        batch = await runtime.read_batch()
        ledger = CalendarReadLedger(tmp_path / "calendar.db")
        ledger.invalidate(ledger.current_state("tenant"), provider_revision="next")
        with pytest.raises(WorkspaceRejected, match="calendar invalidated"):
            runtime.proposal_context(batch)


async def test_dynamic_owner_substitution_fails_before_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with workspace(tmp_path) as (runtime, _):
        monkeypatch.setattr(runtime._journal, "_store", object())
        with pytest.raises(WorkspaceRejected, match="owner/storage graph"):
            await runtime.read_batch()


@pytest.mark.parametrize("target", ["instance", "class", "broker", "peer", "journal"])
async def test_every_bound_public_port_is_in_closed_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
) -> None:
    async with workspace(tmp_path) as (runtime, command):
        original = runtime._calendar_port.read

        async def omit(request: CalendarRequest) -> CalendarResult:
            result = await original(request)
            return result.model_copy(update={"rows": ()})

        if target == "instance":
            monkeypatch.setattr(runtime._calendar_port, "read", omit)
        elif target == "class":
            monkeypatch.setattr(type(runtime._calendar_port), "read", omit)
        elif target == "broker":
            monkeypatch.setattr(runtime._calendar_port, "_broker", object())
        elif target == "peer":
            monkeypatch.setattr(runtime._calendar_port, "_peer", object())
        else:
            monkeypatch.setattr(runtime._journal, "execute", omit)
        with pytest.raises(WorkspaceRejected):
            await runtime.read_batch()
        with pytest.raises(WorkspaceRejected):
            await runtime.journal.execute("peer", command)


@pytest.mark.parametrize(
    "mutation", ["foreign", "omit", "duplicate", "reorder", "digest", "version"]
)
async def test_planning_sources_bind_complete_actual_owner_publication(
    tmp_path: Path, mutation: str
) -> None:
    async with workspace(tmp_path, with_plan=True) as (runtime, _):
        await runtime.read_batch()
        sources = runtime._planning_sources["pool-plan"]
        if mutation == "foreign":
            sources = (
                SourceReference.model_validate_json(runtime._identity.sources[0].model_dump_json()),
            )
        elif mutation == "omit":
            sources = sources[:-1]
        elif mutation == "duplicate":
            sources = (*sources, sources[0])
        elif mutation == "reorder":
            sources = tuple(reversed(sources))
        else:
            field = "content_digest" if mutation == "digest" else "record_version"
            sources = (sources[0].model_copy(update={field: "changed"}), *sources[1:])
        runtime._planning_sources["pool-plan"] = sources
        with pytest.raises(WorkspaceRejected, match="planning"):
            await runtime.read_batch()


async def test_restriction_survives_history_journal_dashboard_and_proposal(tmp_path: Path) -> None:
    async with workspace(tmp_path, restricted=True) as (runtime, command):
        assert (await runtime.journal.execute("peer", command)).disposition == "COMMITTED"
        batch = await runtime.read_batch()
        assert batch.label.value == "ENDPOINT_RESTRICTED"
        assert batch.label.allowed_endpoints == ("channel1", "cli")
        assert (
            batch.history.rows[0].envelope.label
            == batch.journal.rows[0].envelope.label
            == batch.label
        )
        for screen in batch.dashboard.screens:
            if screen.ref.location.dashboard_id in {"core.conversation", "core.journal"}:
                assert all(envelope.label == batch.label for envelope in screen.envelopes)
        assert runtime.proposal_context(batch).label == batch.label
        narrowed = batch.model_copy(
            update={
                "label": batch.label.model_copy(
                    update={
                        "value": "UNRESTRICTED",
                        "allowed_endpoints": (),
                    }
                )
            }
        )
        with pytest.raises(WorkspaceRejected):
            runtime.proposal_context(narrowed)


async def test_restart_continues_immutable_dashboard_sequence_but_rejects_old_batch(
    tmp_path: Path,
) -> None:
    async with workspace(tmp_path) as (runtime, _):
        old = await runtime.read_batch()
    with pytest.raises(WorkspaceRejected):
        runtime.proposal_context(old)
    async with workspace(tmp_path) as (current, _):
        new = await current.read_batch()
        assert new.dashboard.sequence > old.dashboard.sequence
        assert new.context != old.context
        with pytest.raises(WorkspaceRejected):
            current.proposal_context(old)


async def test_history_and_calendar_detail_tools_retain_the_issued_batch(tmp_path: Path) -> None:
    async with workspace(tmp_path) as (runtime, _):
        batch = await runtime.read_batch()
        history = await runtime.history(batch, max_rows=1)
        assert history.rows == batch.history.rows
        detail = await runtime.calendar_detail(batch, batch.calendar.rows[0].row_id)
        assert detail.context == batch.external_context
        assert detail.rows == batch.calendar.rows
        assert not batch.journal.rows
        with pytest.raises(WorkspaceRejected):
            await runtime.history(batch, after_cursor="unknown")


async def test_dashboard_detail_and_background_refresh_preserve_navigation_and_budget(
    tmp_path: Path,
) -> None:
    from chiplog.capabilities.projections.r9_boundary import (
        BudgetPolicy,
        NavigationCommand,
        ScreenLocation,
    )

    async with workspace(tmp_path, with_plan=True) as (runtime, _):
        batch = await runtime.read_batch(
            navigation=NavigationCommand(
                operation="push",
                location=ScreenLocation(
                    dashboard_id="core.planning", screen_id="detail", item_id="pool-plan"
                ),
            ),
            budget=BudgetPolicy(total=800, fixed=10, output=10, conversation_floor=32),
        )
        screen = next(
            s for s in batch.dashboard.screens if s.ref.location.dashboard_id == "core.planning"
        )
        assert screen.ref.location.screen_id == "detail"
        assert screen.ref.location.item_id == "pool-plan"
        assert sum(allocation.allocated for allocation in batch.dashboard.allocations) <= 780
        background = await runtime.read_batch()
        assert background.dashboard.retained == batch.dashboard.retained
        assert background.dashboard.navigation == batch.dashboard.navigation


async def test_actual_policy_history_journal_and_planning_reads_use_one_sqlite_connection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with workspace(tmp_path, with_plan=True) as (runtime, _):
        traces: list[tuple[int, str]] = []
        original = sqlite3.connect

        def traced(path: str | Path) -> sqlite3.Connection:
            connection = original(path)
            connection.set_trace_callback(lambda sql: traces.append((id(connection), sql)))
            return connection

        monkeypatch.setattr(sqlite3, "connect", traced)
        batch = await runtime.read_batch()
        policy_read = [
            connection
            for connection, sql in traces
            if "SELECT owner, schema_id, canonical_bytes" in sql
        ]
        assert len(policy_read) == 1
        statements = [sql for connection, sql in traces if connection == policy_read[0]]
        assert any("SELECT tenant_id, record_id, owner" in sql for sql in statements)
        assert any("owner = 'evidence_journal'" in sql for sql in statements)
        assert any(
            "idempotency_key, request_fingerprint, commit_sequence" in sql for sql in statements
        )
        assert batch.policy_binding_digest


async def test_corrupt_durable_policy_binding_is_a_typed_integrity_failure(tmp_path: Path) -> None:
    from chiplog.capabilities.projections.r9_boundary import WorkspaceIntegrityError

    async with workspace(tmp_path) as (runtime, _):
        with closing(sqlite3.connect(tmp_path / "canonical.db")) as connection, connection:
            connection.execute(
                "UPDATE records SET canonical_bytes=? WHERE owner='workspace_policy'", (b"corrupt",)
            )
        with pytest.raises(
            WorkspaceIntegrityError, match=r"workspace.policy.*tenant=tenant"
        ) as caught:
            await runtime.read_batch()
        assert caught.value.__cause__ is not None


async def test_owner_module_helper_substitution_is_rejected_before_calendar_acquisition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from chiplog.adapters.driven import calendar_reads

    async with workspace(tmp_path) as (runtime, _):
        monkeypatch.setattr(calendar_reads, "prepare_rows", lambda *args: ())
        with pytest.raises(WorkspaceRejected, match="semantic helper"):
            await runtime.read_batch()
