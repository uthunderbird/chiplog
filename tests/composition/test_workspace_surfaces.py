from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import pytest

from chiplog.adapters.driven.r9_fence import (
    CONVERSATION_OWNER,
    CONVERSATION_SCHEMA,
    SCREEN_SINK,
    R3ConversationStore,
    R3ScreenDerivatives,
)
from chiplog.adapters.driven.r9_planning import PlanningWorkspaceQueries
from chiplog.capabilities.projections._planning import _PlanningProjection
from chiplog.capabilities.projections.conversation import ConversationHistory
from chiplog.capabilities.projections.disclosure import CurrentDisclosureGuard
from chiplog.capabilities.projections.r9_boundary import (
    BudgetPolicy,
    ConversationEntry,
    WorkspaceRejected,
)
from chiplog.capabilities.projections.workspace import QueryDashboardBuilder
from chiplog.composition.r9 import R9Component, build_r9_component
from chiplog.platform._sqlite import EventAppender, FenceAdvanceCommand, SQLiteMaterializer
from tests.support.planning_workspace import empty_planning
from tests.support.workspace import Heads, IssuedContext, Queries, request


class EmptyConversation:
    async def append(self, entry: ConversationEntry) -> Literal["COMMITTED", "REPLAY", "CONFLICT"]:
        return "COMMITTED"

    def entries(self, tenant_id: str, frontier: int) -> tuple[ConversationEntry, ...]:
        return ()


@asynccontextmanager
async def real_component(path: Path) -> AsyncIterator[R9Component]:
    contexts = IssuedContext()
    with SQLiteMaterializer(
        path / "canonical.db",
        record_contracts={CONVERSATION_OWNER: CONVERSATION_SCHEMA},
        derivative_contracts=(SCREEN_SINK,),
        managed_derivative_sinks=(SCREEN_SINK,),
    ) as materializer:
        async with EventAppender(materializer, capacity=2) as appender:
            await appender.advance_fence(FenceAdvanceCommand("tenant", "fence", 2))
            yield build_r9_component(
                path / "screens.db",
                R3ConversationStore(materializer, appender, contexts, contexts.current, 2),
                contexts,
                CurrentDisclosureGuard(contexts, Heads()),
                empty_planning(contexts),
                "local",
                R3ScreenDerivatives(appender, contexts, 2),
            )


@pytest.mark.parametrize(
    "mutation", ["extra", "omit", "substitute", "alias", "renderer", "instance"]
)
async def test_exact_executable_manifest_rejects_mutants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    async with real_component(tmp_path) as component:
        component.check_surfaces()
        if mutation == "renderer":
            monkeypatch.setattr(
                "chiplog.capabilities.projections.workspace.render_screen",
                lambda screen, **kwargs: "secret",
            )
        elif mutation == "instance":
            monkeypatch.setattr(component._workspace._store, "load", lambda *args: "secret")
        elif mutation == "extra":
            monkeypatch.setattr(
                ConversationHistory, "new_content", lambda self: b"secret", raising=False
            )
        elif mutation == "omit":
            monkeypatch.delattr(ConversationHistory, "read")
        elif mutation == "substitute":
            monkeypatch.setattr(ConversationHistory, "read", lambda self, request: b"secret")
        else:
            monkeypatch.setattr(ConversationHistory, "read", ConversationHistory.context_read)
        with pytest.raises(WorkspaceRejected, match=r"disclosure|substituted"):
            await component.history(request())


@pytest.mark.parametrize(
    "mutation", ["unknown-query", "query-subclass", "unknown-owner-leaf", "owner-leaf-subclass"]
)
async def test_actual_supplied_query_graph_rejects_unregistered_implementations(
    tmp_path: Path, mutation: str
) -> None:
    async with real_component(tmp_path) as component:
        builder = component._workspace.registry.families["core.planning"].builder
        assert isinstance(builder, QueryDashboardBuilder)
        original = builder._queries
        assert isinstance(original, PlanningWorkspaceQueries)

        class Substitute(PlanningWorkspaceQueries):
            pass

        if mutation == "unknown-query":
            builder._queries = Queries()
        elif mutation == "query-subclass":
            builder._queries = Substitute(original._queries, original._contexts, {})
        elif mutation == "unknown-owner-leaf":
            original._queries = object()  # type: ignore[assignment]
        else:

            class SubstituteOwner(_PlanningProjection):
                pass

            leaf = original._queries
            assert isinstance(leaf, _PlanningProjection)
            original._queries = SubstituteOwner(leaf._tenant_id, leaf._rows, leaf.checkpoint)
        with pytest.raises(WorkspaceRejected, match="unknown/subclass disclosure graph"):
            build_r9_component(
                tmp_path / "reassembled.db",
                component._history._store,
                component._history._contexts,
                component._history._guard,
                builder._queries,
                "local",
                component._workspace._derivatives,
            )
        with pytest.raises(WorkspaceRejected, match="unknown/subclass disclosure graph"):
            await component.refresh(
                request(), BudgetPolicy(total=100, fixed=0, output=0, conversation_floor=32)
            )


@pytest.mark.parametrize("scope", ["instance", "class"])
@pytest.mark.parametrize("entrypoint", ["history", "agent_history", "refresh"])
async def test_private_history_dispatch_rejected_before_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scope: str, entrypoint: str
) -> None:
    from chiplog.capabilities.projections.workspace_boundary import (
        WorkspaceReadResult,
        WorkspaceRow,
    )
    from tests.support.workspace import envelope

    async with real_component(tmp_path) as component:
        called = False

        async def substituted(*args: object, **kwargs: object) -> WorkspaceReadResult:
            nonlocal called
            called = True
            req = request()
            return WorkspaceReadResult(
                disposition="CURRENT",
                context=req.context,
                request_id=req.request_id,
                read_attempt_id=req.read_attempt_id,
                rows=(
                    WorkspaceRow(
                        row_id="unauthorized",
                        row_version="1",
                        order_key="1",
                        canonical_payload=b"UNVERIFIED SECRET",
                        envelope=envelope(),
                    ),
                ),
                next_cursor=None,
                reason=None,
            )

        target = component._history if scope == "instance" else ConversationHistory
        monkeypatch.setattr(target, "_read", substituted)
        with pytest.raises(WorkspaceRejected, match="disclosure"):
            if entrypoint == "refresh":
                await component.refresh(
                    request(), BudgetPolicy(total=100, fixed=0, output=0, conversation_floor=32)
                )
            elif entrypoint == "history":
                await component.history(request())
            else:
                await component.agent_history(request())
        assert not called


@pytest.mark.parametrize("scope", ["instance", "class", "static-class"])
@pytest.mark.parametrize("method", ["_check_snapshot", "_verified"])
@pytest.mark.parametrize("entrypoint", ["replay", "context_text"])
async def test_private_replay_dispatch_rejected_before_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scope: str, method: str, entrypoint: str
) -> None:
    async with real_component(tmp_path) as component:
        state = await component.refresh(
            request(), BudgetPolicy(total=100, fixed=0, output=0, conversation_floor=32)
        )
        assert (
            component.replay("tenant", state.channel_id, state.sequence, request().context) == state
        )
        called = False

        def substituted(*args: object, **kwargs: object) -> bytes:
            nonlocal called
            called = True
            return state.model_dump_json().encode()

        owner = component._workspace if method == "_check_snapshot" else component._workspace._store
        target = owner if scope == "instance" else type(owner)
        replacement = staticmethod(substituted) if scope == "static-class" else substituted
        monkeypatch.setattr(target, method, replacement)
        with pytest.raises(WorkspaceRejected, match="disclosure"):
            if entrypoint == "replay":
                component.replay("tenant", state.channel_id, state.sequence, request().context)
            else:
                component.context_text(state, request().context)
        assert not called


@pytest.mark.parametrize("scope", ["instance", "class"])
@pytest.mark.parametrize("edge", ["cursor", "registry", "budget", "platform"])
async def test_supporting_dispatch_substitution_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scope: str, edge: str
) -> None:
    async with real_component(tmp_path) as component:
        store = component._history._store
        assert isinstance(store, R3ConversationStore)
        owners = {
            "cursor": (component._history, "_cursor"),
            "registry": (component._workspace.registry, "resolve"),
            "budget": (component._workspace._budgeter, "allocate"),
            "platform": (store._materializer, "_require_writer"),
        }
        owner, method = owners[edge]
        with monkeypatch.context() as patch:
            patch.setattr(owner if scope == "instance" else type(owner), method, lambda *args: None)
            with pytest.raises(WorkspaceRejected, match="disclosure"):
                await component.history(request())


@pytest.mark.parametrize("helper", ["_canonical_screen", "_snapshot_identity", "verify_payload"])
async def test_workspace_helper_substitution_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, helper: str
) -> None:
    async with real_component(tmp_path) as component:
        monkeypatch.setattr(
            f"chiplog.capabilities.projections.workspace.{helper}", lambda *args: b"secret"
        )
        with pytest.raises(WorkspaceRejected, match="disclosure"):
            await component.refresh(
                request(), BudgetPolicy(total=100, fixed=0, output=0, conversation_floor=32)
            )
