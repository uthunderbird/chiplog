from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from chiplog.adapters.driven.workspace_sqlite import SQLiteWorkspaceStore
from chiplog.capabilities.projections.disclosure import CurrentDisclosureGuard, join, label
from chiplog.capabilities.projections.r9_boundary import (
    BudgetPolicy,
    NavigationCommand,
    ScreenLocation,
    ScreenSnapshot,
    WorkspaceRejected,
)
from chiplog.capabilities.projections.workspace import (
    DashboardFamilySpec,
    DashboardRegistry,
    QueryDashboardBuilder,
    Workspace,
)
from chiplog.capabilities.projections.workspace_boundary import (
    WorkspaceReadContext,
)
from tests.support.workspace import (
    Heads,
    IssuedContext,
    Queries,
    context,
    envelope,
    request,
)


class Derivatives:
    async def register(self, snapshot: ScreenSnapshot) -> None:
        assert snapshot.ref.snapshot_id


def workspace(path: Path) -> tuple[Workspace, Queries, IssuedContext]:
    queries = Queries()
    contexts = IssuedContext()
    registry = DashboardRegistry(
        tuple(
            DashboardFamilySpec(
                name, f"{name}.builder", "1", QueryDashboardBuilder(queries, "CONVERSATION_HISTORY")
            )
            for name in ("core.conversation", "test.one", "test.two", "test.three", "test.four")
        )
    )
    subject = Workspace(
        registry,
        SQLiteWorkspaceStore(path),
        contexts,
        CurrentDisclosureGuard(contexts, Heads()),
        "local",
        Derivatives(),
    )
    return subject, queries, contexts


async def test_lru_budget_background_refresh_and_replay(tmp_path: Path) -> None:
    path = tmp_path / "workspace.db"
    subject, queries, _ = workspace(path)
    policy = BudgetPolicy(total=500, fixed=40, output=40, conversation_floor=40)
    state = None
    for name in ("test.one", "test.two", "test.three", "test.four"):
        state = await subject.refresh(
            request(),
            policy,
            state,
            NavigationCommand(operation="open", location=ScreenLocation(dashboard_id=name)),
        )
    assert state is not None
    assert state.retained == ("test.two", "test.three", "test.four")
    assert dict(state.navigation)["test.one"]
    assert state.screens[0].ref.location.dashboard_id == "core.conversation"
    assert state.allocations[-1].allocated >= state.allocations[-2].allocated
    queries.lagging = True
    state = await subject.refresh(request(), policy, state)
    assert state.retained == ("test.two", "test.three", "test.four")
    assert all(item.disposition == "LAGGING" for item in state.screens)
    text = subject.context_text(state, context())
    assert len(text) <= (policy.total - policy.fixed - policy.output) * 4
    reopened, _, _ = workspace(path)
    assert reopened.replay("tenant", "channel", state.sequence, context()) == state


async def test_foreign_cut_and_revocation_release_no_screen(tmp_path: Path) -> None:
    subject, queries, contexts = workspace(tmp_path / "workspace.db")
    policy = BudgetPolicy(total=100, fixed=0, output=0, conversation_floor=32)
    queries.other_cut = True
    with pytest.raises(WorkspaceRejected, match="one complete"):
        await subject.refresh(request(), policy)
    queries.other_cut = False
    state = await subject.refresh(request(), policy)
    contexts.revoked = True
    with pytest.raises(WorkspaceRejected, match="independently issued"):
        subject.context_text(state, context())


@pytest.mark.parametrize("mutation", ["rendered", "envelopes", "builder_version", "location"])
async def test_replay_detects_digest_rewritten_snapshot_mutants(
    tmp_path: Path, mutation: str
) -> None:
    path = tmp_path / "workspace.db"
    subject, _, _ = workspace(path)
    state = await subject.refresh(
        request(), BudgetPolicy(total=100, fixed=0, output=0, conversation_floor=32)
    )
    snapshot = state.screens[0]
    if mutation in ("rendered", "envelopes"):
        snapshot = snapshot.model_copy(
            update={mutation: "leaked" if mutation == "rendered" else ()}
        )
    else:
        value = (
            "wrong" if mutation == "builder_version" else ScreenLocation(dashboard_id="test.one")
        )
        snapshot = snapshot.model_copy(
            update={"ref": snapshot.ref.model_copy(update={mutation: value})}
        )
    changed = state.model_copy(update={"screens": (snapshot,)})
    encoded = changed.model_dump_json().encode()
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            "UPDATE workspace SET bytes=?,digest=?", (encoded, hashlib.sha256(encoded).hexdigest())
        )
    with pytest.raises(WorkspaceRejected, match="bound identity"):
        subject.replay("tenant", "channel", 1, context())


def test_lattice_and_cross_endpoint_are_fail_closed() -> None:
    assert (
        join((label("ENDPOINT_RESTRICTED", ("a",)), label("ENDPOINT_RESTRICTED", ("b",)))).value
        == "DENY_ALL"
    )
    guard = CurrentDisclosureGuard(IssuedContext(), Heads())
    with pytest.raises(WorkspaceRejected, match="endpoint denied"):
        guard.check(envelope(), context(), "external")
    with pytest.raises(WorkspaceRejected, match="cannot narrow"):
        guard.check(
            envelope().model_copy(update={"label": label("UNRESTRICTED")}), context(), "local"
        )
    with pytest.raises(WorkspaceRejected, match="missing"):
        join(())


@pytest.mark.parametrize("field", tuple(WorkspaceReadContext.model_fields))
async def test_every_context_member_is_bound(tmp_path: Path, field: str) -> None:
    subject, _, _ = workspace(tmp_path / "workspace.db")
    original = getattr(context(), field)
    changed = (
        original + 1
        if isinstance(original, int)
        else (original + b"x" if isinstance(original, bytes) else original + ".foreign")
    )
    candidate = request().model_copy(
        update={"context": context().model_copy(update={field: changed})}
    )
    with pytest.raises(WorkspaceRejected, match="independently issued"):
        await subject.refresh(
            candidate, BudgetPolicy(total=100, fixed=0, output=0, conversation_floor=32)
        )


async def test_insufficient_budget_writes_no_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "workspace.db"
    subject, _, _ = workspace(path)
    with pytest.raises(WorkspaceRejected, match="floor"):
        await subject.refresh(
            request(), BudgetPolicy(total=31, fixed=0, output=0, conversation_floor=32)
        )
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM workspace").fetchone()[0] == 0
