"""Native scheduler over the existing authenticated per-user PG runtime.

Supported clocks are nondecreasing within the deployment. Current-instance
regression is explicit HOLD; cross-reopen host-clock rollback is backlog.
"""

from __future__ import annotations

import base64
import time
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Literal

from chiplog.capabilities.agent_loop.contracts import LoopHold, LoopRejected, SchedulerRootReference
from chiplog.capabilities.agent_loop.execution_client_transitions import ExecutionRun
from chiplog.capabilities.agent_loop.recovery_contracts import Absent, LeaseBinding, Present
from chiplog.capabilities.agent_loop.scheduler_client_contracts import (
    NativeOccurrenceMember,
    NativeOrdinaryInterval,
    NativeOverflowResolutionDraft,
    NativeScheduleDraft,
    NativeSchedulerAdoption,
    NativeSchedulerCycle,
    NativeSchedulerCycleResult,
    NativeSchedulerLeaseRequest,
    NativeSchedulerLeaseResult,
    NativeScheduleSelection,
    NativeSkippedExecution,
    SchedulerClientDTO,
)
from chiplog.capabilities.agent_loop.scheduler_contracts import (
    HeldLease,
    SchedulerCommandIdentity,
    SchedulerEligibilityBoundary,
    SchedulerIntervalBoundHead,
    SchedulerLineageView,
    SkippedDisposition,
)
from chiplog.capabilities.agent_loop.scheduler_domain import (
    CANONICAL_VERSION,
    eligibility_overflows,
    execution_subjects,
)
from chiplog.composition.postgres_scheduler_records import (
    Configuration,
    Interval,
    LeaseTransition,
    Primitive,
    configuration,
    coordinate,
    envelope_bytes,
    execution_members,
    lineage,
    overflow,
    prepare_members,
    record,
    reference,
)
from chiplog.platform.postgres import Batch, PostgresIntegrityError, Record, Snapshot

if TYPE_CHECKING:
    from chiplog.composition.postgres_execution import PostgresExecutionRuntime


class ResolutionPreview(SchedulerClientDTO):
    operation: Literal["scheduler.resolve"] = "scheduler.resolve"
    tenant: str
    principal: str
    draft: NativeOverflowResolutionDraft


class PostgresScheduler:
    def __init__(
        self,
        runtime: PostgresExecutionRuntime,
        *,
        clock: Callable[[], int] = time.time_ns,
        lease_duration_ns: int = 60_000_000_000,
    ) -> None:
        if lease_duration_ns <= 0:
            raise ValueError("positive scheduler lease horizon required")
        self.runtime, self.clock, self.lease_duration_ns = runtime, clock, lease_duration_ns
        runtime.scheduler = self
        self._last_clock = 0

    def _now(self) -> int:
        now = self.clock()
        if type(now) is not int or not self._last_clock <= now <= 2**64 - 1:
            raise LoopHold("scheduler clock unavailable, regressed or out of range")
        self._last_clock = now
        return now

    def _selected(
        self, snapshot: Snapshot
    ) -> Iterator[tuple[Batch, Configuration | Interval | LeaseTransition]]:
        """Local selected joins; no clock, authorization or Run re-preparation on reads."""
        configs: dict[str, Configuration] = {}
        bounds: dict[str, SchedulerIntervalBoundHead] = {}
        previous: dict[str, Interval] = {}
        held: dict[str, Interval] = {}
        by_head = {r.head: r for r in self.runtime._runs(snapshot)}
        roots: dict[str, SchedulerLineageView] = {}
        for batch in snapshot.batches:
            scheduler_rows = [
                r for r in batch.records if r.schema_id.startswith("chiplog.scheduler.native-")
            ]
            identity = scheduler_rows[0].record_id if scheduler_rows else "<batch>"
            try:
                if batch.operation.startswith("scheduler.") != bool(scheduler_rows):
                    raise ValueError("scheduler operation/schema membership differs")
                if not scheduler_rows:
                    continue
                if len(scheduler_rows) != 1:
                    raise ValueError("scheduler publication needs exactly one native result")
                row = scheduler_rows[0]
                value: Configuration | Interval | LeaseTransition
                if row.schema_id == "chiplog.scheduler.native-configuration.v1":
                    value = Configuration.model_validate_json(row.canonical_bytes)
                    schedule_id = value.draft.schedule_id
                    if (
                        batch.operation != "scheduler.configure"
                        or batch.idempotency_key != value.draft.act_id
                        or len(batch.records) != 1
                        or schedule_id in configs
                        or value.tenant != self.runtime.tenant_id
                        or value.principal != self.runtime.principal_id
                        or value.draft.end_exclusive_ns <= value.draft.start_ns
                        or configuration(
                            value.tenant,
                            value.principal,
                            value.draft,
                            value.origin,
                            value.lease_duration_ns,
                        )
                        != value
                    ):
                        raise ValueError("scheduler configuration identity differs")
                    configs[schedule_id], bounds[schedule_id] = value, value.bound
                elif row.schema_id == "chiplog.scheduler.native-interval.v1":
                    value = Interval.model_validate_json(row.canonical_bytes)
                    schedule_id = value.request.schedule.schedule_id
                    self._validate_interval(
                        by_head,
                        batch,
                        value,
                        configs[schedule_id],
                        bounds[schedule_id],
                        previous.get(schedule_id),
                        held.get(schedule_id),
                    )
                    for view in value.lineages:
                        if view.lineage.root_id in roots:
                            raise ValueError("scheduler root rival initialization")
                        roots[view.lineage.root_id] = view
                    if value.result.kind == "OVERFLOW":
                        held[schedule_id] = value
                    else:
                        previous[schedule_id] = value
                        held.pop(schedule_id, None)
                        if value.successor_bound is not None:
                            bounds[schedule_id] = value.successor_bound
                elif row.schema_id == "chiplog.scheduler.native-lease.v1":
                    value = LeaseTransition.model_validate_json(row.canonical_bytes)
                    if (
                        batch.operation != "scheduler.lease"
                        or batch.idempotency_key != value.act_id
                        or len(batch.records) != 1
                    ):
                        raise ValueError("scheduler lease operation differs")
                    if roots.get(value.observed.lineage.root_id) != value.observed:
                        raise ValueError("scheduler lease does not follow selected root")
                    self._validate_lease(value, configs[value.observed.lineage.schedule_id])
                    roots[value.selected.lineage.root_id] = value.selected
                else:
                    raise ValueError("unknown authoritative native scheduler schema")
                if record(value) != row:
                    raise ValueError("scheduler canonical record differs")
                yield batch, value
            except (ValueError, TypeError, KeyError) as error:
                raise PostgresIntegrityError(
                    f"operation=scheduler.read tenant={self.runtime.tenant_id} record_id={identity}"
                ) from error

    def _validate_interval(
        self,
        by_head: dict[str, ExecutionRun],
        batch: Batch,
        value: Interval,
        config: Configuration,
        bound: SchedulerIntervalBoundHead,
        previous: Interval | None,
        held: Interval | None,
    ) -> None:
        boundary, primitive = value.boundary, value.primitive
        predecessor: Present | Absent = Absent()
        start = config.draft.start_ns
        if previous is not None:
            assert previous.result.kind == "ORDINARY"
            predecessor = previous.result.boundary
            start = int(previous.boundary.cutoff_due_coordinate.canonical_coordinate)
        if (
            value.request.schedule != config.schedule
            or primitive.boundary != boundary
            or primitive.tenant != config.tenant
            or primitive.principal != config.principal
            or boundary.schedule_definition_head != config.schedule
            or boundary.missed_occurrence_policy_head != config.policy
            or boundary.previous_due_boundary != coordinate(start)
            or boundary.predecessor_interval != predecessor
            or not start
            <= int(boundary.cutoff_due_coordinate.canonical_coordinate)
            <= config.draft.end_exclusive_ns
        ):
            raise ValueError("scheduler interval source/boundary chain differs")
        if value.resolution_bytes is None:
            if (
                held is not None
                or batch.operation != "scheduler.cycle"
                or batch.idempotency_key != value.request.act_id
                or value.successor_bound is not None
                or primitive.bound != bound
                or primitive.original_hold != Absent()
                or boundary.enumeration_frontier != str(batch.sequence - 1)
            ):
                raise ValueError("scheduler ordinary source cut differs")
        else:
            preview = ResolutionPreview.model_validate_json(value.resolution_bytes)
            if (
                held is None
                or held.result.kind != "OVERFLOW"
                or batch.operation != "scheduler.resolve"
                or batch.idempotency_key != preview.draft.act_id
                or preview.tenant != config.tenant
                or preview.principal != config.principal
                or preview.draft.schedule != config.schedule
                or preview.draft.expected_bound != bound
                or preview.draft.hold != held.result.hold.hold
                or primitive.original_hold != held.result.hold.hold
                or boundary != held.boundary
                or value.request != held.request
                or value.successor_bound != primitive.bound
                or primitive.bound.generation != bound.generation + 1
                or primitive.bound.predecessor
                != Present(head=bound.head_id, fingerprint=bound.digest())
                or primitive.bound.bound != preview.draft.sufficient_bound
                or value.result.kind != "ORDINARY"
            ):
                raise ValueError("scheduler resolution does not preserve held interval")
        manifest = prepare_members(config, boundary)
        parent = reference("native-scheduler-parent-v1", primitive.model_dump(mode="json"))
        if value.result.kind == "OVERFLOW":
            hold = value.result.hold
            if (
                value.lineages
                or len(batch.records) != 1
                or hold.boundary != boundary
                or hold.bound_head != bound
                or hold.command != primitive.command
                or hold.evidence.kind != "FULL_MANIFEST"
                or hold.evidence.manifest != manifest
                or hold.state.kind != "ACTIVE"
            ):
                raise ValueError("scheduler overflow membership differs")
            return
        if (
            value.result.manifest != manifest
            or value.result.interval != parent
            or value.result.boundary != parent
            or len(envelope_bytes(batch.records)) > primitive.bound.bound.max_serialized_batch_bytes
            or eligibility_overflows(manifest, primitive.bound.bound)
        ):
            raise ValueError("scheduler materialized manifest or bound differs")
        batch_heads = {r.record_id for r in batch.records}
        runs = []
        for subject in execution_subjects(boundary, manifest, manifest.members):
            view, root = lineage(primitive, subject)
            matches = [
                m.execution
                for m in value.result.members
                if m.execution.kind == "EXECUTION"
                and m.execution.run_id == view.lineage.initial_run_id
            ]
            if not matches:
                raise ValueError("scheduler native Run companion absent")
            execution = matches[0]
            run = by_head[execution.initial_run.head]
            if (
                run.head not in batch_heads
                or run.run_id != execution.run_id
                or run.digest() != execution.initial_run.fingerprint
                or run.root_binding != root
                or execution.root != root
                or run.predecessor is not None
                or run.state != "CREATED"
                or run.prompt != config.draft.prompt
                or run.policy != config.draft.budget
                or run.origin != config.origin
                or run.worker_session != primitive.worker
            ):
                raise ValueError("scheduler native Run companion differs")
            runs.append((view, root, run))
        expected = execution_members(primitive, manifest, runs)
        if config.policy.kind == "SKIP":
            expected = tuple(
                NativeOccurrenceMember(
                    disposition=SkippedDisposition(occurrence=m, policy_decision=parent),
                    execution=NativeSkippedExecution(),
                )
                for m in manifest.members
            )
        actual_run_heads = {
            r.record_id for r in batch.records if r.schema_id.endswith("execution-record.v3")
        }
        if (
            value.result.members != expected
            or value.lineages != tuple(v for v, _, _ in runs)
            or actual_run_heads != {r.head for _, _, r in runs}
        ):
            raise ValueError("scheduler disposition/root/Run bijection differs")

    def _validate_lease(self, value: LeaseTransition, config: Configuration) -> None:
        old, new = value.observed.lease, value.selected.lease
        if (
            value.duration_ns != config.lease_duration_ns
            or new.kind != "HELD"
            or value.observed.lineage != value.selected.lineage
            or value.observed.physical_root != value.selected.physical_root
            or value.observed.predecessor_rollover != value.selected.predecessor_rollover
            or new.binding.trusted_expiry != value.observed_at + value.duration_ns
            or new.binding.clock_contract_version != "native-pg-unix-ns.v1"
        ):
            raise ValueError("scheduler lease output differs")
        if value.transition == "ACQUIRE":
            valid = old.kind == "UNLEASED" and new.binding.generation == 1
        elif old.kind != "HELD":
            valid = False
        elif value.transition == "RENEW":
            valid = (
                value.observed_at < old.binding.trusted_expiry < new.binding.trusted_expiry
                and new.binding.generation == old.binding.generation
                and new.binding.lease_id == old.binding.lease_id
                and new.binding.holder_id == old.binding.holder_id
                and new.binding.holder_session_id == old.binding.holder_session_id
            )
        else:
            valid = (
                value.observed_at >= old.binding.trusted_expiry
                and new.binding.generation == old.binding.generation + 1
                and new.binding.lease_id != old.binding.lease_id
            )
        if not valid:
            raise ValueError("invalid selected scheduler lease transition")

    def _state(
        self, snapshot: Snapshot, schedule_id: str
    ) -> tuple[Configuration, SchedulerIntervalBoundHead, Interval | None, Interval | None]:
        config = None
        boundary = None
        hold = None
        bound = None
        for _, value in self._selected(snapshot):
            if isinstance(value, Configuration) and value.draft.schedule_id == schedule_id:
                if config is not None:
                    raise PostgresIntegrityError("duplicate scheduler configuration")
                config, bound = value, value.bound
            elif isinstance(value, Interval) and value.request.schedule.schedule_id == schedule_id:
                if config is None or value.request.schedule != config.schedule:
                    raise PostgresIntegrityError("scheduler interval has no exact configuration")
                if value.result.kind == "OVERFLOW":
                    hold = value
                else:
                    boundary, hold = value, None
                    if value.successor_bound is not None:
                        bound = value.successor_bound
        if config is None or bound is None:
            raise LoopRejected("scheduler configuration absent")
        return config, bound, boundary, hold

    def _prior(self, snapshot: Snapshot, operation: str, act: str) -> Batch | None:
        # Native per-user database and authenticated principal namespace are retained in each body.
        return next(
            (b for b in snapshot.batches if b.operation == operation and b.idempotency_key == act),
            None,
        )

    async def preview_schedule(self, peer: str, draft: NativeScheduleDraft) -> bytes:
        self.runtime._snapshot_for_peer(peer)
        if draft.end_exclusive_ns <= draft.start_ns:
            raise LoopRejected("finite scheduler end must follow start")
        return configuration(
            self.runtime.tenant_id,
            self.runtime.principal_id,
            draft,
            self.runtime.origin,
            self.lease_duration_ns,
        ).canonical_bytes()

    async def adopt_schedule(
        self, peer: str, adoption: NativeSchedulerAdoption
    ) -> NativeScheduleSelection:
        snapshot = self.runtime._snapshot_for_peer(peer)
        try:
            config = Configuration.model_validate_json(adoption.canonical_preview)
            if (
                config.canonical_bytes() != adoption.canonical_preview
                or config.draft.act_id != adoption.act_id
                or await self.preview_schedule(peer, config.draft) != adoption.canonical_preview
            ):
                raise ValueError("scheduler adoption differs from exact preview")
        except ValueError as error:
            raise LoopRejected("invalid scheduler configuration adoption") from error
        prior = self._prior(snapshot, "scheduler.configure", adoption.act_id)
        rows = (record(config),)

        def guard(current: Snapshot) -> None:
            if any(
                isinstance(v, Configuration) and v.draft.schedule_id == config.draft.schedule_id
                for _, v in self._selected(current)
            ):
                raise LoopRejected("scheduler configuration already exists")

        result = await self.runtime.publish_execution_batch(
            peer, "scheduler.configure", adoption.act_id, snapshot.head, rows, guard
        )
        assert result.sequence is not None
        return NativeScheduleSelection(
            disposition="REPLAY" if prior else "COMMITTED",
            publication_sequence=result.sequence,
            schedule=config.schedule,
            policy=config.policy,
            bound=config.bound,
        )

    def _result(self, batch: Batch, value: Interval, replay: bool) -> NativeSchedulerCycleResult:
        return NativeSchedulerCycleResult(
            disposition="REPLAY" if replay else "COMMITTED",
            publication_sequence=batch.sequence,
            result=value.result,
        )

    async def scheduler_cycle(
        self, peer: str, request: NativeSchedulerCycle
    ) -> NativeSchedulerCycleResult:
        snapshot = self.runtime._snapshot_for_peer(peer)
        prior = self._prior(snapshot, "scheduler.cycle", request.act_id)
        if prior is not None:
            value = next(
                v for b, v in self._selected(snapshot) if b == prior and isinstance(v, Interval)
            )
            if value.request != request:
                raise LoopRejected("scheduler cycle replay identity conflict")
            return self._result(prior, value, True)
        config, bound, previous, hold = self._state(snapshot, request.schedule.schedule_id)
        if request.schedule != config.schedule or hold is not None:
            raise LoopRejected("scheduler stale configuration or overflow held")
        now = self._now()
        cutoff = min(now, config.draft.end_exclusive_ns)
        start = (
            config.draft.start_ns
            if previous is None
            else int(previous.boundary.cutoff_due_coordinate.canonical_coordinate)
        )
        if cutoff < start:
            cutoff = start  # A future-start schedule has an empty interval, not backward work.
        predecessor: Present | Absent = Absent()
        if previous is not None:
            assert previous.result.kind == "ORDINARY"
            predecessor = previous.result.boundary
        boundary = SchedulerEligibilityBoundary(
            schedule_definition_head=config.schedule,
            missed_occurrence_policy_head=config.policy,
            previous_due_boundary=coordinate(start),
            cutoff_due_coordinate=coordinate(cutoff),
            enumeration_frontier=str(snapshot.head),
            predecessor_interval=predecessor,
            canonicalization_version=CANONICAL_VERSION,
        )
        value, rows = self._prepare(snapshot, config, bound, request, boundary)
        expected = self._state(snapshot, config.draft.schedule_id)

        def guard(current: Snapshot) -> None:
            if self._state(current, config.draft.schedule_id) != expected:
                raise LoopRejected("scheduler interval source changed")

        published = await self.runtime.publish_execution_batch(
            peer, "scheduler.cycle", request.act_id, snapshot.head, rows, guard
        )
        assert published.sequence is not None
        return NativeSchedulerCycleResult(
            disposition="COMMITTED", publication_sequence=published.sequence, result=value.result
        )

    def _prepare(
        self,
        snapshot: Snapshot,
        config: Configuration,
        bound: SchedulerIntervalBoundHead,
        request: NativeSchedulerCycle,
        boundary: SchedulerEligibilityBoundary,
        *,
        resolution: bytes | None = None,
        original_hold: Present | None = None,
        worker: str | None = None,
    ) -> tuple[Interval, tuple[Record, ...]]:
        command_act = (
            request.act_id
            if resolution is None
            else ResolutionPreview.model_validate_json(resolution).draft.act_id
        )
        command = SchedulerCommandIdentity(
            command_id=reference(
                "native-scheduler-command-v1",
                [
                    self.runtime.tenant_id,
                    self.runtime.principal_id,
                    "cycle" if resolution is None else "resolve",
                    command_act,
                ],
            ).head,
            schema_version="chiplog.scheduler.native-cycle-request.v1",
            canonicalization_version=CANONICAL_VERSION,
        )
        primitive = Primitive(
            command=command,
            boundary=boundary,
            bound=bound,
            original_hold=Absent() if original_hold is None else original_hold,
            tenant=self.runtime.tenant_id,
            principal=self.runtime.principal_id,
            worker=self.runtime._worker if worker is None else worker,
        )
        manifest = prepare_members(config, boundary)
        parent = reference("native-scheduler-parent-v1", primitive.model_dump(mode="json"))
        exceeded = eligibility_overflows(manifest, bound.bound)
        runs = []
        companions: list[Record] = []
        if not exceeded:
            for index, subject in enumerate(
                execution_subjects(boundary, manifest, manifest.members)
            ):
                view, root = lineage(primitive, subject)
                run, rows = self.runtime.prepare_execution_records(
                    snapshot,
                    run_id=view.lineage.initial_run_id,
                    prompt=config.draft.prompt,
                    policy=config.draft.budget,
                    origin=config.origin,
                    worker=primitive.worker,
                    root_binding=root,
                    history_offset=index,
                )
                runs.append((view, root, run))
                companions.extend(rows)
        if config.policy.kind == "SKIP":
            members = tuple(
                NativeOccurrenceMember(
                    disposition=SkippedDisposition(occurrence=m, policy_decision=parent),
                    execution=NativeSkippedExecution(),
                )
                for m in manifest.members
            )
        else:
            members = execution_members(primitive, manifest, runs) if not exceeded else ()
        ordinary = NativeOrdinaryInterval(
            interval=parent, manifest=manifest, members=members, boundary=parent
        )
        value = Interval(
            primitive=primitive,
            request=request,
            resolution_bytes=resolution,
            boundary=boundary,
            result=ordinary,
            lineages=tuple(view for view, _, _ in runs),
            successor_bound=bound if resolution is not None else None,
        )
        rows = (record(value), *companions)
        size = len(envelope_bytes(rows))
        if exceeded or size > bound.bound.max_serialized_batch_bytes:
            if resolution is not None:
                raise LoopRejected("successor bound insufficient for complete resolution")
            dimension: Literal["MEMBER_COUNT", "MANIFEST_BYTES", "SERIALIZED_BATCH_BYTES"]
            dimension, actual, limit = (
                (exceeded[0].dimension, exceeded[0].actual, exceeded[0].limit)
                if exceeded
                else ("SERIALIZED_BATCH_BYTES", size, bound.bound.max_serialized_batch_bytes)
            )
            value = value.model_copy(
                update={
                    "result": overflow(primitive, manifest, dimension, actual, limit),
                    "lineages": (),
                }
            )
            rows = (record(value),)
        return value, rows

    async def preview_overflow_resolution(
        self, peer: str, draft: NativeOverflowResolutionDraft
    ) -> bytes:
        snapshot = self.runtime._snapshot_for_peer(peer)
        config, bound, _, held = self._state(snapshot, draft.schedule.schedule_id)
        if (
            held is None
            or held.result.kind != "OVERFLOW"
            or held.result.hold.hold != draft.hold
            or config.schedule != draft.schedule
            or bound != draft.expected_bound
        ):
            raise LoopRejected("overflow resolution source changed")
        return ResolutionPreview(
            tenant=self.runtime.tenant_id, principal=self.runtime.principal_id, draft=draft
        ).canonical_bytes()

    async def resolve_scheduler_overflow(
        self, peer: str, adoption: NativeSchedulerAdoption
    ) -> NativeSchedulerCycleResult:
        snapshot = self.runtime._snapshot_for_peer(peer)
        try:
            preview = ResolutionPreview.model_validate_json(adoption.canonical_preview)
            if (
                preview.canonical_bytes() != adoption.canonical_preview
                or preview.draft.act_id != adoption.act_id
                or preview.tenant != self.runtime.tenant_id
                or preview.principal != self.runtime.principal_id
            ):
                raise ValueError("resolution adoption identity differs")
        except ValueError as error:
            raise LoopRejected("invalid overflow resolution adoption") from error
        prior = self._prior(snapshot, "scheduler.resolve", adoption.act_id)
        if prior is not None:
            value = next(
                v for b, v in self._selected(snapshot) if b == prior and isinstance(v, Interval)
            )
            if value.resolution_bytes != adoption.canonical_preview:
                raise LoopRejected("scheduler resolution replay identity conflict")
            return self._result(prior, value, True)
        if (
            await self.preview_overflow_resolution(peer, preview.draft)
            != adoption.canonical_preview
        ):
            raise LoopRejected("resolution preview changed")
        config, old_bound, _, held = self._state(snapshot, preview.draft.schedule.schedule_id)
        assert held is not None and held.result.kind == "OVERFLOW"
        if any(
            getattr(preview.draft.sufficient_bound, f) < getattr(old_bound.bound, f)
            for f in type(old_bound.bound).model_fields
        ):
            raise LoopRejected("scheduler bound cannot decrease")
        successor = old_bound.model_copy(
            update={
                "head_id": reference(
                    "native-bound-successor-v1", preview.model_dump(mode="json")
                ).head,
                "predecessor": Present(head=old_bound.head_id, fingerprint=old_bound.digest()),
                "generation": old_bound.generation + 1,
                "bound": preview.draft.sufficient_bound,
            }
        )
        value, rows = self._prepare(
            snapshot,
            config,
            successor,
            held.request,
            held.boundary,
            resolution=adoption.canonical_preview,
            original_hold=held.result.hold.hold,
        )

        def guard(current: Snapshot) -> None:
            if self._state(current, config.draft.schedule_id) != self._state(
                snapshot, config.draft.schedule_id
            ):
                raise LoopRejected("held scheduler interval changed")

        published = await self.runtime.publish_execution_batch(
            peer, "scheduler.resolve", adoption.act_id, snapshot.head, rows, guard
        )
        assert published.sequence is not None
        return NativeSchedulerCycleResult(
            disposition="COMMITTED", publication_sequence=published.sequence, result=value.result
        )

    def lineage(self, snapshot: Snapshot, root_id: str) -> SchedulerLineageView:
        selected = None
        for _, value in self._selected(snapshot):
            if isinstance(value, Interval):
                for view in value.lineages:
                    if view.lineage.root_id == root_id:
                        if selected is not None:
                            raise PostgresIntegrityError("scheduler root has rival initialization")
                        selected = view
            elif isinstance(value, LeaseTransition) and value.selected.lineage.root_id == root_id:
                if selected != value.observed:
                    raise PostgresIntegrityError("scheduler lease transition predecessor differs")
                selected = value.selected
        if selected is None:
            raise LoopHold("scheduler root absent")
        return selected

    def lease_fence(self, snapshot: Snapshot, run: ExecutionRun) -> SchedulerLineageView:
        if not isinstance(run.root_binding, SchedulerRootReference):
            raise LoopRejected("ordinary Run has no scheduler lease fence")
        view = self.lineage(snapshot, run.root_binding.root_id)
        expected_subject = base64.b64encode(view.lineage.subject.canonical_bytes()).decode()
        if (
            run.root_binding.subject_canonical_base64 != expected_subject
            or run.root_binding.root_fingerprint != view.lineage.root_fingerprint
            or run.root_binding.initial_run_id != view.lineage.initial_run_id
            or run.run_id != view.lineage.current_run_id
        ):
            raise LoopHold("scheduled Run differs from selected lineage")
        self.assert_current_lease(snapshot, run, view)
        return view

    def assert_current_lease(
        self, snapshot: Snapshot, run: ExecutionRun, expected: SchedulerLineageView
    ) -> None:
        view = self.lineage(snapshot, expected.lineage.root_id)
        if (
            view != expected
            or not isinstance(run.root_binding, SchedulerRootReference)
            or run.root_binding.root_id != view.lineage.root_id
            or run.run_id != view.lineage.current_run_id
            or view.lease.kind != "HELD"
            or view.lease.binding.holder_id != self.runtime._worker
            or view.lease.binding.holder_session_id != self.runtime._token
            or self._now() >= view.lease.binding.trusted_expiry
        ):
            raise LoopHold("scheduler lease expired, taken over or submission fence changed")

    async def transition_scheduler_lease(
        self, peer: str, request: NativeSchedulerLeaseRequest
    ) -> NativeSchedulerLeaseResult:
        snapshot = self.runtime._snapshot_for_peer(peer)
        prior = self._prior(snapshot, "scheduler.lease", request.act_id)
        if prior is not None:
            value = next(
                v
                for b, v in self._selected(snapshot)
                if b == prior and isinstance(v, LeaseTransition)
            )
            if value.observed != request.expected or value.transition != request.transition:
                raise LoopRejected("lease replay identity conflict")
            return NativeSchedulerLeaseResult(
                disposition="REPLAY", publication_sequence=prior.sequence, lineage=value.selected
            )
        current = self.lineage(snapshot, request.expected.lineage.root_id)
        if current != request.expected:
            raise LoopRejected("stale scheduler lease observation")
        now, lease = self._now(), current.lease
        generation = 1
        lease_id = reference("native-lease-id-v1", [current.lineage.root_id, request.act_id]).head
        if request.transition == "ACQUIRE":
            if lease.kind != "UNLEASED":
                raise LoopRejected("acquire requires unleased root")
        else:
            if lease.kind != "HELD":
                raise LoopHold("renew/takeover requires held scheduler root")
            old = lease.binding
            if request.transition == "RENEW":
                if (
                    now >= old.trusted_expiry
                    or old.holder_id != self.runtime._worker
                    or old.holder_session_id != self.runtime._token
                ):
                    raise LoopHold("renew requires current live holder")
                generation, lease_id = old.generation, old.lease_id
            else:
                if now < old.trusted_expiry:
                    raise LoopHold("takeover requires expired scheduler lease")
                generation = old.generation + 1
        duration = self._state(snapshot, current.lineage.schedule_id)[0].lease_duration_ns
        expiry = now + duration
        if (
            request.transition == "RENEW"
            and lease.kind == "HELD"
            and expiry <= lease.binding.trusted_expiry
        ):
            raise LoopRejected("scheduler renewal must advance expiry")
        if generation > 2**64 - 1 or expiry > 2**64 - 1:
            raise LoopHold("scheduler generation or clock horizon exhausted")
        binding = LeaseBinding(
            lease_head=reference(
                "native-lease-head-v1",
                [request.model_dump(), now, self.runtime._worker, self.runtime._token],
            ).head,
            holder_id=self.runtime._worker,
            holder_session_id=self.runtime._token,
            lease_id=lease_id,
            generation=generation,
            trusted_expiry=expiry,
            clock_contract_version="native-pg-unix-ns.v1",
        )
        selected = current.model_copy(update={"lease": HeldLease(binding=binding)})
        value = LeaseTransition(
            act_id=request.act_id,
            transition=request.transition,
            observed=current,
            selected=selected,
            observed_at=now,
            duration_ns=duration,
        )

        def guard(cut: Snapshot) -> None:
            if self.lineage(cut, current.lineage.root_id) != current:
                raise LoopRejected("scheduler lease changed before publication")
            checked_now = self._now()
            if checked_now >= expiry or (
                request.transition == "RENEW"
                and lease.kind == "HELD"
                and checked_now >= lease.binding.trusted_expiry
            ):
                raise LoopHold("scheduler lease expired before publication")

        published = await self.runtime.publish_execution_batch(
            peer, "scheduler.lease", request.act_id, snapshot.head, (record(value),), guard
        )
        assert published.sequence is not None
        return NativeSchedulerLeaseResult(
            disposition="COMMITTED", publication_sequence=published.sequence, lineage=selected
        )
