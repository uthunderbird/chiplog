"""Actual native PG scheduler publication; clock/source calls are composition-owned."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypedDict

import pytest

from chiplog.adapters.driven.effects_hermetic import HermeticEffectsProvider
from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.capabilities.agent_loop.contracts import (
    BudgetPolicy,
    LoopHold,
    LoopRejected,
    SchedulerRootReference,
)
from chiplog.capabilities.agent_loop.delivery_contracts import (
    ExactHead,
    OriginSelection,
    ProviderRecipient,
)
from chiplog.capabilities.agent_loop.scheduler_client_contracts import (
    NativeOverflowResolutionDraft,
    NativeScheduleDraft,
    NativeSchedulerAdoption,
    NativeSchedulerCycle,
    NativeSchedulerLeaseRequest,
    NativeScheduleSelection,
)
from chiplog.capabilities.agent_loop.scheduler_contracts import SchedulerIntervalBound
from chiplog.composition.postgres_application import PostgresApplicationDriver
from chiplog.composition.postgres_effects import PostgresEffectsBinding
from chiplog.composition.postgres_execution import PostgresExecutionRuntime
from chiplog.composition.postgres_planning import PostgresPlanningRuntime
from chiplog.composition.postgres_scheduler import PostgresScheduler
from chiplog.platform.postgres import PostgresServer
from tests.support.scheduler_execution import origin

SECRET = b"native-scheduler-test"
PEER = "local-cli"


class Identity(TypedDict):
    principal_id: str
    credential_id: str
    session_id: str


IDENTITY: Identity = {"principal_id": "p", "credential_id": "c", "session_id": "s"}


@dataclass
class Clock:
    now: int = 0

    def __call__(self) -> int:
        return self.now


def bound_origin(binding: PostgresEffectsBinding) -> OriginSelection:
    target = binding.target_for_principal("hermetic-principal")
    return OriginSelection(
        ingress_binding=origin().ingress_binding,
        recipient=ProviderRecipient(
            provider_id=target.provider,
            account_id=target.account,
            recipient_id=target.recipient,
            canonical_address=target.canonical_address,
            endpoint=ExactHead(
                identity=target.endpoint.subject_id,
                head=target.endpoint.head,
                fingerprint=target.endpoint.fingerprint,
            ),
            credential_binding=ExactHead(
                identity=target.credential_binding.subject_id,
                head=target.credential_binding.head,
                fingerprint=target.credential_binding.fingerprint,
            ),
        ),
    )


async def opened(
    server: PostgresServer, clock: Clock, binding: PostgresEffectsBinding | None = None
) -> PostgresScheduler:
    database = server.user_database("scheduler-test")
    database.provision()
    identity: Identity = {
        **IDENTITY,
        "principal_id": "p" if binding is None else "hermetic-principal",
    }
    await PostgresPlanningRuntime(database, "t", SECRET).bootstrap(
        database_instance_id="scheduler-db", token="token", **identity
    )
    runtime = PostgresExecutionRuntime(
        database,
        "t",
        SECRET,
        **identity,
        model=HermeticModel(),
        origin=origin() if binding is None else bound_origin(binding),
        effects_binding=binding,
    )
    return PostgresScheduler(runtime, clock=clock, lease_duration_ns=10)


async def adopted(
    scheduler: PostgresScheduler,
    policy: Literal["SKIP", "COALESCE", "MATERIALIZE_EACH"] = "MATERIALIZE_EACH",
    *,
    count: int = 10,
    manifest: int = 100000,
    batch: int = 1000000,
) -> NativeScheduleSelection:
    draft = NativeScheduleDraft(
        act_id="adopt",
        schedule_id="schedule",
        start_ns=10,
        period_ns=10,
        end_exclusive_ns=100,
        prompt="Review",
        budget=BudgetPolicy(),
        missed_policy=policy,
        bound=SchedulerIntervalBound(
            max_member_count=count, max_manifest_bytes=manifest, max_serialized_batch_bytes=batch
        ),
    )
    adoption = NativeSchedulerAdoption(
        act_id=draft.act_id, canonical_preview=await scheduler.preview_schedule(PEER, draft)
    )
    return await scheduler.adopt_schedule(PEER, adoption)


@pytest.mark.parametrize(
    ("now", "policy", "members", "runs"),
    [
        (5, "MATERIALIZE_EACH", 0, 0),
        (11, "MATERIALIZE_EACH", 1, 1),
        (31, "MATERIALIZE_EACH", 3, 3),
        (31, "COALESCE", 3, 1),
        (31, "SKIP", 3, 0),
    ],
)
async def test_due_members_and_native_runs_publish_in_one_batch(
    postgres_server: PostgresServer,
    now: int,
    policy: Literal["SKIP", "COALESCE", "MATERIALIZE_EACH"],
    members: int,
    runs: int,
) -> None:
    clock = Clock(now)
    scheduler = await opened(postgres_server, clock)
    selected = await adopted(scheduler, policy)
    result = await scheduler.scheduler_cycle(
        PEER, NativeSchedulerCycle(act_id="tick", schedule=selected.schedule)
    )
    assert result.result.kind == "ORDINARY"
    assert len(result.result.members) == members
    snapshot = scheduler.runtime.snapshot()
    native = scheduler.runtime._runs(snapshot)
    assert len(native) == runs
    assert (
        len({entry.ordinal for entry in scheduler.runtime._history(snapshot, snapshot.head, "p")})
        == runs
    )
    batch = snapshot.batches[-1]
    assert len([r for r in batch.records if r.schema_id.endswith("execution-record.v3")]) == runs
    assert all(run.root_binding != "NOT_APPLICABLE" for run in native)
    assert isinstance(scheduler.runtime.model, HermeticModel)
    assert scheduler.runtime.model.requests == []
    if policy == "COALESCE":
        assert (
            len(
                {
                    m.execution.run_id
                    for m in result.result.members
                    if m.execution.kind == "EXECUTION"
                }
            )
            == 1
        )
        assert all(m.disposition.kind == "COALESCED_INTO" for m in result.result.members)
    if policy == "SKIP":
        assert all(m.execution.kind == "NO_EXECUTION" for m in result.result.members)


async def test_reopen_replays_selected_interval_without_sampling_clock_or_new_run(
    postgres_server: PostgresServer,
) -> None:
    scheduler = await opened(postgres_server, Clock(31))
    selected = await adopted(scheduler)
    request = NativeSchedulerCycle(act_id="tick", schedule=selected.schedule)
    result = await scheduler.scheduler_cycle(PEER, request)
    snapshot = scheduler.runtime.snapshot()
    runtime = scheduler.runtime
    reopened = PostgresExecutionRuntime(
        runtime.database, "t", SECRET, **IDENTITY, model=HermeticModel(), origin=origin()
    )

    def unavailable() -> int:
        raise AssertionError("replay sampled a new clock")

    replay = await PostgresScheduler(reopened, clock=unavailable).scheduler_cycle(PEER, request)
    assert replay.disposition == "REPLAY" and replay.result == result.result
    assert reopened.snapshot() == snapshot
    changed = request.model_copy(
        update={"schedule": selected.schedule.model_copy(update={"head": "other"})}
    )
    with pytest.raises(LoopRejected, match="conflict"):
        await PostgresScheduler(reopened, clock=unavailable).scheduler_cycle(PEER, changed)


@pytest.mark.parametrize("dimension", ["count", "manifest", "batch"])
async def test_overflow_holds_complete_interval_then_explicit_bound_resolves_once(
    postgres_server: PostgresServer,
    dimension: str,
) -> None:
    scheduler = await opened(postgres_server, Clock(31))
    selected = await adopted(
        scheduler,
        count=1 if dimension == "count" else 10,
        manifest=1 if dimension == "manifest" else 100000,
        batch=1 if dimension == "batch" else 1000000,
    )
    request = NativeSchedulerCycle(act_id="tick", schedule=selected.schedule)
    held = await scheduler.scheduler_cycle(PEER, request)
    assert held.result.kind == "OVERFLOW"
    assert held.result.hold.evidence.kind == "FULL_MANIFEST"
    assert len(held.result.hold.evidence.manifest.members) == 3
    assert scheduler.runtime._runs(scheduler.runtime.snapshot()) == ()
    before = scheduler.runtime.snapshot()
    with pytest.raises(LoopRejected, match="held"):
        await scheduler.scheduler_cycle(PEER, request.model_copy(update={"act_id": "tick2"}))
    draft = NativeOverflowResolutionDraft(
        act_id="resolve",
        schedule=selected.schedule,
        hold=held.result.hold.hold,
        expected_bound=selected.bound,
        sufficient_bound=SchedulerIntervalBound(
            max_member_count=10, max_manifest_bytes=100000, max_serialized_batch_bytes=1000000
        ),
    )
    adoption = NativeSchedulerAdoption(
        act_id=draft.act_id,
        canonical_preview=await scheduler.preview_overflow_resolution(PEER, draft),
    )
    resolved = await scheduler.resolve_scheduler_overflow(PEER, adoption)
    assert resolved.result.kind == "ORDINARY" and len(resolved.result.members) == 3
    assert len(scheduler.runtime._runs(scheduler.runtime.snapshot())) == 3
    after = scheduler.runtime.snapshot()
    assert after.head == before.head + 1
    assert (await scheduler.resolve_scheduler_overflow(PEER, adoption)).disposition == "REPLAY"
    assert scheduler.runtime.snapshot() == after


async def test_expiry_takeover_and_exact_action_lease_fences(
    postgres_server: PostgresServer,
) -> None:
    clock = Clock(11)
    scheduler = await opened(postgres_server, clock)
    selected = await adopted(scheduler)
    await scheduler.scheduler_cycle(
        PEER, NativeSchedulerCycle(act_id="tick", schedule=selected.schedule)
    )
    run = scheduler.runtime._runs(scheduler.runtime.snapshot())[0]
    assert isinstance(run.root_binding, SchedulerRootReference)
    view = scheduler.lineage(scheduler.runtime.snapshot(), run.root_binding.root_id)
    acquired = await scheduler.transition_scheduler_lease(
        PEER, NativeSchedulerLeaseRequest(act_id="acquire", transition="ACQUIRE", expected=view)
    )
    assert scheduler.lease_fence(scheduler.runtime.snapshot(), run) == acquired.lineage
    clock.now = 21
    with pytest.raises(LoopHold, match="expired"):
        scheduler.assert_current_lease(scheduler.runtime.snapshot(), run, acquired.lineage)
    successor = await scheduler.transition_scheduler_lease(
        PEER,
        NativeSchedulerLeaseRequest(
            act_id="takeover", transition="TAKEOVER", expected=acquired.lineage
        ),
    )
    assert successor.lineage.lease.kind == "HELD"
    assert successor.lineage.lease.binding.generation == 2
    with pytest.raises(LoopHold, match="fence"):
        scheduler.assert_current_lease(scheduler.runtime.snapshot(), run, acquired.lineage)
    assert scheduler.lease_fence(scheduler.runtime.snapshot(), run) == successor.lineage
    clock.now = 20
    with pytest.raises(LoopHold, match="regressed"):
        scheduler.lease_fence(scheduler.runtime.snapshot(), run)


async def test_scheduler_precommit_failure_rolls_back_runs_and_disposition(
    postgres_server: PostgresServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from chiplog.platform.postgres import Connection, PublicationResult, Record, UserDatabase

    scheduler = await opened(postgres_server, Clock(31))
    selected = await adopted(scheduler)
    before = scheduler.runtime.snapshot()
    original = UserDatabase.append

    def failing_append(
        self: UserDatabase,
        connection: Connection,
        operation: str,
        key: str,
        fingerprint: str,
        records: tuple[Record, ...],
        expected_head: int,
    ) -> PublicationResult:
        result = original(self, connection, operation, key, fingerprint, records, expected_head)
        if operation == "scheduler.cycle":
            raise RuntimeError("injected after all physical append writes")
        return result

    with monkeypatch.context() as scoped:
        scoped.setattr(UserDatabase, "append", failing_append)
        with pytest.raises(RuntimeError, match="injected"):
            await scheduler.scheduler_cycle(
                PEER, NativeSchedulerCycle(act_id="tick", schedule=selected.schedule)
            )
    assert scheduler.runtime.snapshot() == before
    assert scheduler.runtime._runs(before) == ()
    assert (
        await scheduler.scheduler_cycle(
            PEER, NativeSchedulerCycle(act_id="tick", schedule=selected.schedule)
        )
    ).result.kind == "ORDINARY"


async def test_insufficient_resolution_leaves_hold_and_bound_unchanged(
    postgres_server: PostgresServer,
) -> None:
    scheduler = await opened(postgres_server, Clock(31))
    selected = await adopted(scheduler, count=1)
    held = await scheduler.scheduler_cycle(
        PEER, NativeSchedulerCycle(act_id="tick", schedule=selected.schedule)
    )
    assert held.result.kind == "OVERFLOW"
    draft = NativeOverflowResolutionDraft(
        act_id="resolve",
        schedule=selected.schedule,
        hold=held.result.hold.hold,
        expected_bound=selected.bound,
        sufficient_bound=selected.bound.bound,
    )
    adoption = NativeSchedulerAdoption(
        act_id=draft.act_id,
        canonical_preview=await scheduler.preview_overflow_resolution(PEER, draft),
    )
    before = scheduler.runtime.snapshot()
    with pytest.raises(LoopRejected, match="insufficient"):
        await scheduler.resolve_scheduler_overflow(PEER, adoption)
    assert scheduler.runtime.snapshot() == before


@pytest.mark.parametrize("coherent", [False, True])
async def test_selected_interval_cannot_shift_cutoff_or_initial_boundary(
    postgres_server: PostgresServer,
    coherent: bool,
) -> None:
    from chiplog.composition.postgres_scheduler_records import Interval, coordinate, record
    from chiplog.platform.postgres import PostgresIntegrityError

    scheduler = await opened(postgres_server, Clock(11))
    selected = await adopted(scheduler, "SKIP")
    request = NativeSchedulerCycle(act_id="tick", schedule=selected.schedule)
    await scheduler.scheduler_cycle(PEER, request)
    snapshot = scheduler.runtime.snapshot()
    row = snapshot.batches[-1].records[0]
    original = Interval.model_validate_json(row.canonical_bytes)
    if coherent:
        config = scheduler._state(snapshot, selected.schedule.schedule_id)[0]
        boundary = original.boundary.model_copy(
            update={
                "previous_due_boundary": coordinate(20),
                "cutoff_due_coordinate": coordinate(31),
            }
        )
        bad, _ = scheduler._prepare(snapshot, config, selected.bound, request, boundary)
    else:
        bad = original.model_copy(
            update={
                "boundary": original.boundary.model_copy(
                    update={"cutoff_due_coordinate": coordinate(100)}
                )
            }
        )
    changed = record(bad)
    with scheduler.runtime.database.connect() as connection:
        connection.execute(
            "UPDATE records SET record_id=%s,canonical_bytes=%s WHERE record_id=%s",
            (changed.record_id, changed.canonical_bytes, row.record_id),
        )
        connection.execute(
            "UPDATE publications SET record_ids=array_replace(record_ids,%s,%s) WHERE sequence=%s",
            (row.record_id, changed.record_id, snapshot.head),
        )
    with pytest.raises(PostgresIntegrityError, match=r"scheduler\.read"):
        await scheduler.scheduler_cycle(PEER, request)


async def test_scheduled_native_run_uses_shared_driver_and_completion_after_reopen(
    postgres_server: PostgresServer,
    tmp_path: Path,
) -> None:
    binding = PostgresEffectsBinding(
        HermeticEffectsProvider(
            receipt_key=b"scheduler-effects",
            scenarios=(),
            journal_path=tmp_path / "transfers",
            observation_journal_path=tmp_path / "observations",
        ),
        b"scheduler-effects",
    )
    identity: Identity = {**IDENTITY, "principal_id": "hermetic-principal"}
    clock = Clock(11)
    scheduler = await opened(postgres_server, clock, binding)
    selected = await adopted(scheduler)
    request = NativeSchedulerCycle(act_id="tick", schedule=selected.schedule)
    cycle = await scheduler.scheduler_cycle(PEER, request)
    assert cycle.result.kind == "ORDINARY"
    execution = cycle.result.members[0].execution
    assert execution.kind == "EXECUTION"
    run_id = execution.run_id
    raw = json.dumps(
        {
            "kind": "Complete",
            "tenant": "t",
            "run_id": run_id,
            "turn_id": run_id + "/turn/1",
            "deliveries": [
                {"payload": [{"kind": "NonAuthoritativeText", "text": "Scheduled review finished"}]}
            ],
        }
    ).encode()
    model = HermeticModel((raw,))
    runtime = PostgresExecutionRuntime(
        scheduler.runtime.database,
        "t",
        SECRET,
        **identity,
        model=model,
        origin=bound_origin(binding),
        effects_binding=binding,
    )
    scheduler = PostgresScheduler(runtime, clock=clock, lease_duration_ns=10)
    view = scheduler.lineage(runtime.snapshot(), execution.root.root_id)
    await scheduler.transition_scheduler_lease(
        PEER, NativeSchedulerLeaseRequest(act_id="acquire", transition="ACQUIRE", expected=view)
    )
    completed = await PostgresApplicationDriver(runtime).drive_run(PEER, run_id)
    assert completed.state == "SUCCEEDED" and len(model.requests) == 1
    assert len(runtime.delivery_intents(PEER, run_id)) == 1
    assert binding.provider.transfers == ()
    snapshot = runtime.snapshot()
    reopened = PostgresExecutionRuntime(
        runtime.database,
        "t",
        SECRET,
        **identity,
        model=HermeticModel(),
        origin=bound_origin(binding),
        effects_binding=binding,
    )
    assert await PostgresApplicationDriver(reopened).drive_run(PEER, run_id) == completed
    assert reopened.snapshot() == snapshot
    assert (
        await PostgresScheduler(reopened, clock=clock).scheduler_cycle(PEER, request)
    ).result == cycle.result
