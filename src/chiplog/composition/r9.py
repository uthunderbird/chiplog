"""R9 component assembly, with an exact executable disclosure inventory.

The supplied context port must authenticate its bound peer independently and validate
broker-issued cuts. This component is not a promoted R7 runtime generation.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import FunctionType, MethodType
from typing import cast

from agent_dashboard import render_screen, screen_from_dict, screen_to_dict

from chiplog.adapters.driven.r9_fence import R3ConversationStore, R3ScreenDerivatives
from chiplog.adapters.driven.r9_planning import PlanningWorkspaceQueries
from chiplog.adapters.driven.workspace_sqlite import SQLiteWorkspaceStore
from chiplog.capabilities.projections import conversation as conversation_module
from chiplog.capabilities.projections import workspace as workspace_module
from chiplog.capabilities.projections._planning import _PlanningProjection
from chiplog.capabilities.projections.conversation import ConversationHistory
from chiplog.capabilities.projections.disclosure import (
    DisclosureSurface,
    DisclosureSurfaceManifest,
    verify_payload,
)
from chiplog.capabilities.projections.r9_boundary import (
    BudgetPolicy,
    ConversationEntry,
    ConversationStore,
    DerivativePort,
    DisclosureGuard,
    NavigationCommand,
    ReadContextPort,
    ScreenLocation,
    WorkspaceRejected,
    WorkspaceState,
)
from chiplog.capabilities.projections.workspace import (
    DashboardFamilySpec,
    DashboardRegistry,
    QueryDashboardBuilder,
    TurnContextBudgeter,
    Workspace,
)
from chiplog.capabilities.projections.workspace_boundary import (
    WorkspaceQueryPort,
    WorkspaceReadContext,
    WorkspaceReadRequest,
    WorkspaceReadResult,
)
from chiplog.platform._sqlite import EventAppender, SQLiteMaterializer


class _ConversationContextBuilder:
    def __init__(self, history: ConversationHistory) -> None:
        self._history = history

    async def build(
        self, location: ScreenLocation, request: WorkspaceReadRequest
    ) -> WorkspaceReadResult:
        return await self._history.context_read(request)


# Fixed compiled inventory independent of the objects realized below. Later R12
# surfaces must explicitly extend this contract before their component executes.
_SURFACES = (
    ("conversation.dispatch", ConversationHistory, "_read", ConversationHistory._read),
    ("conversation.cursor", ConversationHistory, "_cursor", ConversationHistory._cursor),
    ("workspace.snapshot.verify", Workspace, "_check_snapshot", Workspace._check_snapshot),
    (
        "workspace.persistence.verify",
        SQLiteWorkspaceStore,
        "_verified",
        SQLiteWorkspaceStore._verified,
    ),
    ("workspace.registry.resolve", DashboardRegistry, "resolve", DashboardRegistry.resolve),
    ("workspace.budget.allocate", TurnContextBudgeter, "allocate", TurnContextBudgeter.allocate),
    ("conversation.accept", ConversationHistory, "accept", ConversationHistory.accept),
    ("conversation.history", ConversationHistory, "read", ConversationHistory.read),
    ("conversation.context", ConversationHistory, "context_read", ConversationHistory.context_read),
    ("workspace.build", Workspace, "refresh", Workspace.refresh),
    ("workspace.replay", Workspace, "replay", Workspace.replay),
    ("workspace.context", Workspace, "context_text", Workspace.context_text),
    ("conversation.canonical.append", R3ConversationStore, "append", R3ConversationStore.append),
    ("conversation.canonical.read", R3ConversationStore, "entries", R3ConversationStore.entries),
    ("workspace.persistence.write", SQLiteWorkspaceStore, "save", SQLiteWorkspaceStore.save),
    ("workspace.persistence.replay", SQLiteWorkspaceStore, "load", SQLiteWorkspaceStore.load),
    (
        "workspace.derivative.register",
        R3ScreenDerivatives,
        "register",
        R3ScreenDerivatives.register,
    ),
    ("workspace.planning.query", PlanningWorkspaceQueries, "read", PlanningWorkspaceQueries.read),
    ("workspace.planning.owner-query", _PlanningProjection, "get", _PlanningProjection.get),
    (
        "workspace.physical.read",
        SQLiteMaterializer,
        "guarded_records",
        SQLiteMaterializer.guarded_records,
    ),
    ("workspace.physical.append", EventAppender, "submit", EventAppender.submit),
    (
        "workspace.physical.derivative",
        EventAppender,
        "register_derivative",
        EventAppender.register_derivative,
    ),
    ("workspace.query.builder", QueryDashboardBuilder, "build", QueryDashboardBuilder.build),
    (
        "conversation.context.builder",
        _ConversationContextBuilder,
        "build",
        _ConversationContextBuilder.build,
    ),
)
_EXPECTED_PUBLIC = {
    DashboardRegistry: frozenset(("resolve",)),
    TurnContextBudgeter: frozenset(("allocate",)),
    ConversationHistory: frozenset(("accept", "read", "context_read")),
    Workspace: frozenset(("refresh", "replay", "context_text")),
    R3ConversationStore: frozenset(("append", "entries")),
    SQLiteWorkspaceStore: frozenset(("save", "load")),
    R3ScreenDerivatives: frozenset(("register",)),
    PlanningWorkspaceQueries: frozenset(("read",)),
    QueryDashboardBuilder: frozenset(("build",)),
    _ConversationContextBuilder: frozenset(("build",)),
    _PlanningProjection: frozenset(("get",)),
    SQLiteMaterializer: frozenset(
        name
        for name, value in vars(SQLiteMaterializer).items()
        if not name.startswith("_") and callable(value)
    ),
    EventAppender: frozenset(
        name
        for name, value in vars(EventAppender).items()
        if not name.startswith("_") and callable(value)
    ),
}


# Integrity dependencies include private dispatch in the bound platform leaves.
# Capture descriptors before assembly, independently of realized instances; static
# methods must retain their descriptor as well as their underlying function.
_CLASS_DISPATCH = {
    cls: {
        name: value
        for name, value in vars(cls).items()
        if isinstance(value, (FunctionType, staticmethod, classmethod))
    }
    for cls in _EXPECTED_PUBLIC
}
_MODULE_DISPATCH = (
    (conversation_module, "verify_payload", verify_payload),
    (workspace_module, "verify_payload", verify_payload),
    (workspace_module, "_canonical_screen", workspace_module._canonical_screen),
    (workspace_module, "_snapshot_identity", workspace_module._snapshot_identity),
)


@dataclass(frozen=True)
class R9Component:
    _history: ConversationHistory
    _workspace: Workspace
    endpoint: str

    def check_surfaces(self) -> DisclosureSurfaceManifest:
        if (
            vars(workspace_module).get("render_screen") is not render_screen
            or vars(workspace_module).get("screen_to_dict") is not screen_to_dict
            or vars(workspace_module).get("screen_from_dict") is not screen_from_dict
        ):
            raise WorkspaceRejected("dashboard serialization/render implementation substituted")
        for module, name, helper_original in _MODULE_DISPATCH:
            if vars(module).get(name) is not helper_original:
                raise WorkspaceRejected("substituted disclosure helper implementation")
        instances: dict[type[object], object] = {}

        def bind(instance: object, expected: type[object]) -> None:
            if type(instance) is not expected:
                raise WorkspaceRejected("unknown/subclass disclosure graph implementation")
            prior = instances.get(expected)
            if prior is not None and prior is not instance:
                raise WorkspaceRejected("aliased disclosure graph owner instance")
            instances[expected] = instance

        bind(self._history, ConversationHistory)
        bind(self._workspace, Workspace)
        bind(self._history._store, R3ConversationStore)
        bind(self._workspace._store, SQLiteWorkspaceStore)
        bind(self._workspace._derivatives, R3ScreenDerivatives)
        if type(self._workspace.registry) is not DashboardRegistry:
            raise WorkspaceRejected("unregistered disclosure family registry")
        bind(self._workspace.registry, DashboardRegistry)
        bind(self._workspace._budgeter, TurnContextBudgeter)
        families = self._workspace.registry.families
        if set(families) != {"core.conversation", "core.planning"}:
            raise WorkspaceRejected("unknown/omitted disclosure family graph")
        conversation_builder = families["core.conversation"].builder
        planning_builder = families["core.planning"].builder
        bind(conversation_builder, _ConversationContextBuilder)
        bind(planning_builder, QueryDashboardBuilder)
        if cast(_ConversationContextBuilder, conversation_builder)._history is not self._history:
            raise WorkspaceRejected("substituted canonical conversation graph")
        planning = cast(QueryDashboardBuilder, planning_builder)._queries
        bind(planning, PlanningWorkspaceQueries)
        bind(cast(PlanningWorkspaceQueries, planning)._queries, _PlanningProjection)
        conversation = cast(R3ConversationStore, self._history._store)
        derivatives = cast(R3ScreenDerivatives, self._workspace._derivatives)
        bind(conversation._materializer, SQLiteMaterializer)
        bind(conversation._appender, EventAppender)
        bind(derivatives._appender, EventAppender)
        if conversation._appender._materializer is not conversation._materializer:
            raise WorkspaceRejected("split canonical disclosure storage graph")
        for cls, instance in instances.items():
            descriptors = _CLASS_DISPATCH[cls]
            current = {
                name: value
                for name, value in vars(cls).items()
                if isinstance(value, (FunctionType, staticmethod, classmethod))
            }
            if current.keys() != descriptors.keys() or any(
                current[name] is not original for name, original in descriptors.items()
            ):
                raise WorkspaceRejected("substituted class disclosure dispatch")
            if any(
                name in descriptors or (not name.startswith("_") and callable(value))
                for name, value in vars(instance).items()
            ):
                raise WorkspaceRejected("instance disclosure method substitution/alias")
        actual: dict[str, Callable[..., object]] = {}
        rows = []
        for surface_id, cls, method, original in _SURFACES:
            instance = instances[cls]
            public = {
                name
                for name in vars(type(instance))
                if not name.startswith("_") and callable(getattr(type(instance), name))
            }
            if public != _EXPECTED_PUBLIC[cls]:
                raise WorkspaceRejected("unknown or omitted executable disclosure method")
            if any(
                not name.startswith("_") and callable(value)
                for name, value in vars(instance).items()
            ):
                raise WorkspaceRejected("instance disclosure method substitution/alias")
            realized = getattr(instance, method, None)
            if isinstance(_CLASS_DISPATCH[cls][method], staticmethod):
                if realized is not original:
                    raise WorkspaceRejected("substituted static disclosure implementation")
            elif (
                not isinstance(realized, MethodType)
                or realized.__self__ is not instance
                or realized.__func__ is not original
            ):
                raise WorkspaceRejected("substituted bound disclosure implementation")
            actual[surface_id] = original
            rows.append(
                DisclosureSurface(
                    surface_id,
                    original,
                    "conversation" if cls is ConversationHistory else "projections",
                    "complete-source-manifest.v1",
                    "DisclosureEnvelope.v1",
                    self.endpoint,
                    "current-source-label-head",
                    "R3.guarded_records+issued-context",
                    "immutable-snapshot/current-head-revalidation",
                    "pre-return+broker-release",
                )
            )
        for name, implementation in (
            ("render", render_screen),
            ("serialize", screen_to_dict),
            ("deserialize", screen_from_dict),
        ):
            identity = f"workspace.dashboard.{name}"
            actual[identity] = implementation
            rows.append(
                DisclosureSurface(
                    identity,
                    implementation,
                    "projections",
                    "complete-source-manifest.v1",
                    "DisclosureEnvelope.v1",
                    self.endpoint,
                    "current-source-label-head",
                    "R3.guarded_records+issued-context",
                    "immutable-snapshot",
                    "pre-return+broker-release",
                )
            )
        manifest = DisclosureSurfaceManifest(tuple(rows), actual)
        manifest.validate()
        return manifest

    async def accept(self, entry: ConversationEntry, request: WorkspaceReadRequest) -> str:
        self.check_surfaces()
        result = await self._history.accept(entry, request)
        self.check_surfaces()
        return result

    async def history(self, request: WorkspaceReadRequest) -> WorkspaceReadResult:
        self.check_surfaces()
        result = await self._history.read(request)
        self.check_surfaces()
        return result

    async def agent_history(self, request: WorkspaceReadRequest) -> WorkspaceReadResult:
        self.check_surfaces()
        result = await self._history.context_read(request)
        self.check_surfaces()
        return result

    async def refresh(
        self,
        request: WorkspaceReadRequest,
        policy: BudgetPolicy,
        previous: WorkspaceState | None = None,
        navigation: NavigationCommand | None = None,
        invalidations: tuple[str, ...] = (),
        tool_availability: tuple[str, ...] = (),
    ) -> WorkspaceState:
        self.check_surfaces()
        result = await self._workspace.refresh(
            request, policy, previous, navigation, invalidations, tool_availability
        )
        self.check_surfaces()
        return result

    def replay(
        self, tenant_id: str, channel_id: str, sequence: int, context: WorkspaceReadContext
    ) -> WorkspaceState:
        self.check_surfaces()
        result = self._workspace.replay(tenant_id, channel_id, sequence, context)
        self.check_surfaces()
        return result

    def context_text(self, state: WorkspaceState, context: WorkspaceReadContext) -> str:
        self.check_surfaces()
        result = self._workspace.context_text(state, context)
        self.check_surfaces()
        return result


def build_r9_component(
    path: Path,
    conversation_store: ConversationStore,
    contexts: ReadContextPort,
    guard: DisclosureGuard,
    planning: WorkspaceQueryPort,
    endpoint: str,
    derivatives: DerivativePort,
) -> R9Component:
    history = ConversationHistory(conversation_store, contexts, guard, endpoint)
    registry = DashboardRegistry(
        (
            DashboardFamilySpec(
                "core.conversation",
                "conversation.v1",
                "1",
                _ConversationContextBuilder(history),
            ),
            DashboardFamilySpec(
                "core.planning",
                "planning.v1",
                "1",
                QueryDashboardBuilder(planning, "PLANNING_VIEW"),
                ("overview", "detail"),
            ),
        )
    )
    component = R9Component(
        history,
        Workspace(registry, SQLiteWorkspaceStore(path), contexts, guard, endpoint, derivatives),
        endpoint,
    )
    component.check_surfaces()
    return component
