"""Deterministic, non-authoritative workspace built on the real agent-dashboard API."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol

from agent_dashboard import DashboardScreen, render_screen, screen_from_dict, screen_to_dict

from .disclosure import verify_payload
from .r9_boundary import (
    BudgetAllocation,
    BudgetPolicy,
    DerivativePort,
    DisclosureGuard,
    NavigationCommand,
    ReadContextPort,
    ScreenLocation,
    ScreenSnapshot,
    ScreenSnapshotRef,
    ScreenSnapshotV2,
    SnapshotStore,
    SubjectDisclosureGuard,
    WorkspaceIssuancePort,
    WorkspaceRejected,
    WorkspaceState,
)
from .workspace_boundary import (
    ProvenanceSubject,
    WorkspaceQuery,
    WorkspaceQueryPort,
    WorkspaceReadContext,
    WorkspaceReadRequest,
    WorkspaceReadResult,
)


class DashboardBuilder(Protocol):
    async def build(
        self, location: ScreenLocation, request: WorkspaceReadRequest
    ) -> WorkspaceReadResult: ...


class QueryDashboardBuilder:
    def __init__(self, queries: WorkspaceQueryPort, query: WorkspaceQuery) -> None:
        self._queries, self._query = queries, query

    async def build(
        self, location: ScreenLocation, request: WorkspaceReadRequest
    ) -> WorkspaceReadResult:
        return await self._queries.read(
            request.model_copy(update={"query": self._query, "detail_id": location.item_id})
        )


@dataclass(frozen=True)
class DashboardFamilySpec:
    dashboard_id: str
    builder_id: str
    builder_version: str
    builder: DashboardBuilder
    screens: tuple[str, ...] = ("overview",)


class DashboardRegistry:
    def __init__(self, families: tuple[DashboardFamilySpec, ...]) -> None:
        ids = tuple(item.dashboard_id for item in families)
        if len(ids) != len(set(ids)) or "core.conversation" not in ids:
            raise WorkspaceRejected("family collision or missing mandatory conversation")
        for family in families:
            ScreenLocation(dashboard_id=family.dashboard_id)
            if (
                not family.builder_id
                or not family.builder_version
                or not family.screens
                or len(family.screens) != len(set(family.screens))
                or set(family.screens) - {"overview", "detail"}
            ):
                raise WorkspaceRejected("invalid family contract")
        self.families = MappingProxyType({item.dashboard_id: item for item in families})

    def resolve(self, location: ScreenLocation) -> DashboardFamilySpec:
        family = self.families.get(location.dashboard_id)
        if (
            family is None
            or location.screen_id not in family.screens
            or (location.screen_id == "detail") != (location.item_id is not None)
        ):
            raise WorkspaceRejected("invalid typed route")
        return family


class TurnContextBudgeter:
    def allocate(
        self, policy: BudgetPolicy, retained: tuple[str, ...], requests: dict[str, int]
    ) -> dict[str, int]:
        remaining = policy.total - policy.fixed - policy.output - len(retained)
        if remaining < policy.conversation_floor:
            raise WorkspaceRejected("budget cannot preserve conversation floor")
        conversation = min(
            max(policy.conversation_floor, requests["core.conversation"]),
            max(policy.conversation_floor, remaining // 3),
        )
        allocations = {"core.conversation": conversation}
        remaining -= conversation
        weights = tuple(2**index for index in range(len(retained)))
        weight_total = sum(weights)
        used = 0
        for family, weight in zip(retained, weights, strict=True):
            allocation = remaining * weight // weight_total
            allocations[family] = allocation
            used += allocation
        if retained:
            allocations[retained[-1]] += remaining - used
        return allocations


def _canonical_screen(screen: DashboardScreen) -> bytes:
    return json.dumps(
        screen_to_dict(screen), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def _snapshot_identity(snapshot: ScreenSnapshot) -> str:
    unsigned = snapshot.model_copy(
        update={"ref": snapshot.ref.model_copy(update={"snapshot_id": ""})}
    )
    return hashlib.sha256(unsigned.model_dump_json().encode()).hexdigest()


class Workspace:
    def __init__(
        self,
        registry: DashboardRegistry,
        snapshots: SnapshotStore,
        contexts: ReadContextPort,
        guard: DisclosureGuard,
        endpoint: str,
        derivatives: DerivativePort,
        config_version: str = "r9.config.v1",
        *,
        issuance: WorkspaceIssuancePort | None = None,
        subject_bound: bool = False,
    ) -> None:
        self.registry, self._store = registry, snapshots
        self._contexts, self._guard, self._endpoint = contexts, guard, endpoint
        self._derivatives = derivatives
        self._issuance = issuance
        if subject_bound and (issuance is None or not isinstance(guard, SubjectDisclosureGuard)):
            raise WorkspaceRejected("subject-bound workspace requires issuance and bound guard")
        self._subject_bound = subject_bound
        self._config_version = config_version
        self._budgeter = TurnContextBudgeter()

    async def refresh(
        self,
        request: WorkspaceReadRequest,
        policy: BudgetPolicy,
        previous: WorkspaceState | None = None,
        navigation: NavigationCommand | None = None,
        invalidations: tuple[str, ...] = (),
        tool_availability: tuple[str, ...] = (),
    ) -> WorkspaceState:
        context = request.context
        self._contexts.validate(context)
        if request.after_cursor is not None:
            raise WorkspaceRejected("workspace batch must start at initial cursor")
        if previous is not None and (previous.tenant_id, previous.channel_id) != (
            context.tenant_id,
            context.channel_id,
        ):
            raise WorkspaceRejected("foreign workspace state")
        if (
            previous is not None
            and self._store.load(previous.tenant_id, previous.channel_id, previous.sequence)
            != previous
        ):
            raise WorkspaceRejected("unpersisted workspace predecessor")
        if previous is not None and self._issuance is not None:
            self._issuance.verify(previous)
        retained = list(previous.retained if previous else ())
        stacks = dict(previous.navigation if previous else ())
        if navigation is not None:
            self.registry.resolve(navigation.location)
            family_id = navigation.location.dashboard_id
            stack = list(stacks.get(family_id, ()))
            if navigation.operation in ("back", "pop"):
                if len(stack) < 2:
                    raise WorkspaceRejected("navigation stack has no predecessor")
                stack.pop()
            elif navigation.operation in ("replace", "focus"):
                stack = [*stack[:-1], navigation.location]
            else:
                stack.append(navigation.location)
            stacks[family_id] = tuple(stack)
            if family_id != "core.conversation":
                retained = [item for item in retained if item != family_id] + [family_id]
                retained = retained[-3:]
        conversation_location = ScreenLocation(dashboard_id="core.conversation")
        locations = [conversation_location] + [stacks[family][-1] for family in retained]
        results = []
        raw_screens = []
        requests = {}
        for location in locations:
            spec = self.registry.resolve(location)
            result = await spec.builder.build(location, request)
            if (
                result.context != context
                or result.request_id != request.request_id
                or result.read_attempt_id != request.read_attempt_id
                or result.disposition not in ("CURRENT", "LAGGING")
            ):
                raise WorkspaceRejected("workspace requires one complete verified read cut")
            if len(result.rows) > request.max_rows:
                raise WorkspaceRejected("builder exceeded row bound")
            keys = tuple(row.order_key for row in result.rows)
            ids = tuple(row.row_id for row in result.rows)
            if keys != tuple(sorted(set(keys))) or len(ids) != len(set(ids)):
                raise WorkspaceRejected("unordered/duplicate workspace page")
            for row in result.rows:
                verify_payload(row.canonical_payload, row.envelope)
                if self._subject_bound:
                    assert isinstance(self._guard, SubjectDisclosureGuard)
                    self._guard.check_subject(
                        ProvenanceSubject(
                            tenant_id=context.tenant_id,
                            producer=location.dashboard_id,
                            record_id=row.row_id,
                            revision=row.row_version,
                        ),
                        row.envelope,
                        context,
                        self._endpoint,
                    )
                else:
                    self._guard.check(row.envelope, context, self._endpoint)
            lines = tuple(row.canonical_payload.decode("utf-8") for row in result.rows)
            if result.next_cursor:
                lines += ("More items available through bounded history/query navigation.",)
            if result.disposition == "LAGGING":
                lines = ("LAGGING — display only; not authority", *lines)
            screen = DashboardScreen(
                dashboard_id=location.dashboard_id,
                screen_id=location.screen_id,
                breadcrumb=(location.dashboard_id, location.screen_id),
                item_count=len(result.rows),
                body_lines=lines,
            )
            encoded = _canonical_screen(screen)
            requests[location.dashboard_id] = (
                len(render_screen(screen, token_budget=max(1, len(encoded)))) + 3
            ) // 4
            results.append(result)
            raw_screens.append(screen)
        allocated = self._budgeter.allocate(policy, tuple(retained), requests)
        snapshots = []
        allocations = []
        for location, raw, result in zip(locations, raw_screens, results, strict=True):
            spec = self.registry.resolve(location)
            budget = allocated[location.dashboard_id]
            requested = requests[location.dashboard_id]
            # Compression affects content only. Complete source envelopes are retained.
            rendered = render_screen(raw, token_budget=max(8, budget)) if budget >= 8 else ""
            while len(rendered) > budget * 4:
                rendered = rendered[:-1]
            encoded = _canonical_screen(raw)
            content_hash = hashlib.sha256(encoded).hexdigest()
            snapshots.append(
                ScreenSnapshot(
                    ref=ScreenSnapshotRef(
                        tenant_id=context.tenant_id,
                        snapshot_id="",
                        location=location,
                        builder_id=spec.builder_id,
                        builder_version=spec.builder_version,
                        context=context,
                        config_version=self._config_version,
                        budget_version=policy.version,
                        token_budget=budget,
                        budget_policy=policy,
                        content_hash=content_hash,
                    ),
                    screen_bytes=encoded,
                    rendered=rendered,
                    envelopes=tuple(row.envelope for row in result.rows),
                    disposition="LAGGING" if result.disposition == "LAGGING" else "CURRENT",
                )
            )
            snapshot = snapshots[-1]
            if self._subject_bound:
                snapshot = ScreenSnapshotV2(
                    **snapshot.model_dump(),
                    subjects=tuple(
                        ProvenanceSubject(
                            tenant_id=context.tenant_id,
                            producer=location.dashboard_id,
                            record_id=row.row_id,
                            revision=row.row_version,
                        )
                        for row in result.rows
                    ),
                )
            snapshots[-1] = snapshot.model_copy(
                update={
                    "ref": snapshot.ref.model_copy(
                        update={"snapshot_id": _snapshot_identity(snapshot)}
                    )
                }
            )
            allocations.append(
                BudgetAllocation(
                    dashboard_id=location.dashboard_id,
                    requested=requested,
                    allocated=budget,
                    estimated=(len(rendered) + 3) // 4,
                    truncated=requested > budget,
                )
            )
        self._contexts.validate(context)
        state = WorkspaceState(
            tenant_id=context.tenant_id,
            channel_id=context.channel_id,
            sequence=(previous.sequence if previous else 0) + 1,
            retained=tuple(retained),
            navigation=tuple(sorted(stacks.items())),
            screens=tuple(snapshots),
            allocations=tuple(allocations),
            invalidations=invalidations,
            tool_availability=tool_availability,
        )
        for snapshot in state.screens:
            await self._derivatives.register(snapshot)
        self._contexts.validate(context)
        if self._issuance is not None:
            self._issuance.select_verified(state, state.sequence - 1)
        self._store.save(state, state.sequence - 1)
        return state

    def replay(
        self, tenant_id: str, channel_id: str, sequence: int, context: WorkspaceReadContext
    ) -> WorkspaceState:
        self._contexts.validate(context)
        if (tenant_id, channel_id) != (context.tenant_id, context.channel_id):
            raise WorkspaceRejected("foreign replay context")
        state = self._store.load(tenant_id, channel_id, sequence)
        if self._issuance is not None:
            self._issuance.verify(state)
        for snapshot in state.screens:
            if snapshot.ref.context != context:
                raise WorkspaceRejected("replay cut is no longer current")
            self._check_snapshot(snapshot, context)
        return state

    def context_text(self, state: WorkspaceState, context: WorkspaceReadContext) -> str:
        if self.replay(state.tenant_id, state.channel_id, state.sequence, context) != state:
            raise WorkspaceRejected("substituted workspace state")
        return "\n\n".join(snapshot.rendered for snapshot in state.screens)

    def _check_snapshot(self, snapshot: ScreenSnapshot, context: WorkspaceReadContext) -> None:
        if _snapshot_identity(snapshot) != snapshot.ref.snapshot_id:
            raise WorkspaceRejected("snapshot bound identity mismatch")
        if hashlib.sha256(snapshot.screen_bytes).hexdigest() != snapshot.ref.content_hash:
            raise WorkspaceRejected("snapshot content hash mismatch")
        screen = screen_from_dict(json.loads(snapshot.screen_bytes))
        if _canonical_screen(screen) != snapshot.screen_bytes:
            raise WorkspaceRejected("snapshot noncanonical representation")
        spec = self.registry.resolve(snapshot.ref.location)
        if (
            screen.dashboard_id != snapshot.ref.location.dashboard_id
            or screen.screen_id != snapshot.ref.location.screen_id
            or spec.builder_id != snapshot.ref.builder_id
            or spec.builder_version != snapshot.ref.builder_version
        ):
            raise WorkspaceRejected("snapshot location/builder mismatch")
        budget = snapshot.ref.token_budget
        rendered = render_screen(screen, token_budget=max(8, budget)) if budget >= 8 else ""
        if snapshot.rendered != rendered[: budget * 4]:
            raise WorkspaceRejected("snapshot rendering mismatch")
        lines = screen.body_lines
        if snapshot.disposition == "LAGGING":
            lines = lines[1:]
        if lines and lines[-1] == "More items available through bounded history/query navigation.":
            lines = lines[:-1]
        if len(lines) != len(snapshot.envelopes) or screen.item_count != len(lines):
            raise WorkspaceRejected("snapshot incomplete source closure")
        if isinstance(snapshot, ScreenSnapshotV2) and (
            self._issuance is None or not isinstance(self._guard, SubjectDisclosureGuard)
        ):
            raise WorkspaceRejected("subject-bound screen requires independent issuance")
        if self._subject_bound and not isinstance(snapshot, ScreenSnapshotV2):
            raise WorkspaceRejected("legacy screen requires rebuilding with bound subjects")
        for index, (line, envelope) in enumerate(zip(lines, snapshot.envelopes, strict=True)):
            verify_payload(line.encode(), envelope)
            if isinstance(snapshot, ScreenSnapshotV2):
                assert isinstance(self._guard, SubjectDisclosureGuard)
                subject = snapshot.subjects[index]
                if subject.producer != snapshot.ref.location.dashboard_id:
                    raise WorkspaceRejected("screen subject differs from its owner family")
                self._guard.check_subject(subject, envelope, context, self._endpoint)
            else:
                self._guard.check(envelope, context, self._endpoint)
