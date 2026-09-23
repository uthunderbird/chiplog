"""Actual isolated owner, journal, SQL and restart tick histories."""

import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest

from chiplog.capabilities.agent_loop.contracts import BudgetPolicy, LoopRejected
from chiplog.capabilities.agent_loop.scheduler_contracts import SchedulerIntervalBound
from chiplog.capabilities.agent_loop.scheduler_preparation import PreparedSchedulerBatch
from chiplog.composition.r15_scheduler_registry import (
    SchedulerGenesisAdoption,
    SchedulerGenesisDraft,
)
from chiplog.composition.r15_scheduler_runtime import R15SchedulerRuntime, open_r15_runtime
from chiplog.composition.r15_tick_clock_v1 import EvaluationSchedulerClock
from chiplog.composition.r15_tick_contracts import TickPolicyAdoption, TickPolicyDraft
from chiplog.platform._sqlite import PhysicalPublicationCommand, PublicationResult
from chiplog.platform.broker import PublicPortCall, PublicPortResult, PublicPortSuccess
from chiplog.platform.owner_publications import OwnerPublicationUncertain
from chiplog.platform.r7_runtime import AuthorityBrokerRuntime


async def configure(
    runtime: R15SchedulerRuntime,
    policy: str = "COALESCE",
    limit: int = 4,
    bound: SchedulerIntervalBound | None = None,
) -> None:
    draft = SchedulerGenesisDraft.model_validate(
        dict(
            adoption_act_id="genesis",
            schedule_id="schedule",
            start_ns=10,
            period_ns=10,
            end_exclusive_ns="NO_END",
            prompt="Review",
            policy=BudgetPolicy(),
            missed_policy=policy,
            interval_bound=bound
            or SchedulerIntervalBound(
                max_member_count=limit,
                max_manifest_bytes=65536,
                max_serialized_batch_bytes=1_048_576,
            ),
        )
    )
    command = await runtime.preview_scheduler_genesis("hermetic-ingress", draft)
    result = await runtime.adopt_scheduler_genesis(
        "hermetic-ingress",
        SchedulerGenesisAdoption(
            adoption_act_id="genesis", command_bytes=command.canonical_bytes()
        ),
    )
    assert result.kind == "COMMITTED"


async def adoption(runtime: R15SchedulerRuntime, act: str = "tick") -> TickPolicyAdoption:
    shown = await runtime.preview_scheduler_tick_policy(
        "hermetic-ingress",
        TickPolicyDraft(adoption_act_id=act, schedule_id="schedule", delivery_id="delivery:" + act),
    )
    return TickPolicyAdoption(adoption_act_id=act, policy_bytes=shown.canonical_bytes())


@pytest.mark.parametrize(
    ("policy", "cutoff", "members", "runs"),
    [
        ("COALESCE", 10, 0, 0),
        ("COALESCE", 20, 1, 1),
        ("COALESCE", 30, 2, 1),
        ("SKIP", 30, 2, 0),
        ("MATERIALIZE_EACH", 30, 2, 2),
    ],
)
async def test_actual_tick_publishes_complete_interval_and_run_readback(
    tmp_path: Path,
    policy: str,
    cutoff: int,
    members: int,
    runs: int,
) -> None:
    database = tmp_path / "tick.sqlite"
    clock = EvaluationSchedulerClock(cutoff)
    async with open_r15_runtime(database, clock=clock) as runtime:
        await configure(runtime, policy)
        accepted = await adoption(runtime)
        result = await runtime.adopt_scheduler_tick("hermetic-ingress", accepted)
        assert result.kind == "COMMITTED"
        with closing(sqlite3.connect(database)) as connection:
            rows = connection.execute(
                "SELECT schema_id, canonical_bytes FROM records WHERE commit_sequence=?",
                (result.tenant_commit_sequence,),
            ).fetchall()
        interval = json.loads(
            next(raw for schema, raw in rows if schema == "chiplog.scheduler.interval-result.v1")
        )
        assert len(interval["manifest"]["members"]) == members
        assert len(interval["dispositions"]) == members
        assert len(runtime._loop_snapshot().records) == runs
        retained = result.complete_records
        clock.unix_ns += 100
        replay = await runtime.adopt_scheduler_tick("hermetic-ingress", accepted)
        assert replay.kind == "EXACT_REPLAY" and replay.complete_records == retained
    async with open_r15_runtime(database, clock=clock) as restarted:
        replay = await restarted.adopt_scheduler_tick("hermetic-ingress", accepted)
        assert replay.kind == "EXACT_REPLAY" and replay.complete_records == retained
        assert len(restarted._loop_snapshot().records) == runs


async def test_next_interval_and_overflow_preserve_durable_progress(tmp_path: Path) -> None:
    clock = EvaluationSchedulerClock(20)
    async with open_r15_runtime(tmp_path / "overflow.sqlite", clock=clock) as runtime:
        await configure(runtime, limit=2)
        first = await runtime.adopt_scheduler_tick("hermetic-ingress", await adoption(runtime))
        assert first.kind == "COMMITTED"
        clock.unix_ns = 40
        second = await runtime.adopt_scheduler_tick(
            "hermetic-ingress", await adoption(runtime, "second")
        )
        assert second.kind == "COMMITTED"
        assert len(runtime._loop_snapshot().records) == 2
        clock.unix_ns = 80
        third = await runtime.adopt_scheduler_tick(
            "hermetic-ingress", await adoption(runtime, "third")
        )
        assert third.kind == "COMMITTED"
        assert tuple(r.record_kind for r in third.complete_records) == ("overflow-hold",)
        assert len(runtime._loop_snapshot().records) == 2
        with pytest.raises(LoopRejected, match="overflow hold"):
            await runtime.adopt_scheduler_tick(
                "hermetic-ingress", await adoption(runtime, "fourth")
            )


@pytest.mark.parametrize("fault", ["before_commit", "after_commit"])
async def test_tick_recovers_selected_bytes_without_current_clock_or_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: Literal["before_commit", "after_commit"],
) -> None:
    database = tmp_path / "recover.sqlite"
    async with open_r15_runtime(database, clock=EvaluationSchedulerClock(30)) as runtime:
        await configure(runtime)
        accepted = await adoption(runtime)
        submit = runtime._appender.submit

        async def fail(command: PhysicalPublicationCommand) -> PublicationResult:
            return await submit(replace(command, fault=fault))

        with monkeypatch.context() as patch:
            patch.setattr(runtime._appender, "submit", fail)
            with pytest.raises(OwnerPublicationUncertain):
                await runtime.adopt_scheduler_tick("hermetic-ingress", accepted)
        selected = runtime._owner_decisions().snapshot().decisions[-1]
        retained = selected.prepared.request.complete_records
    call = AuthorityBrokerRuntime.call

    async def no_prepare(self: AuthorityBrokerRuntime, request: PublicPortCall) -> PublicPortResult:
        assert request.operation_id != "scheduler.prepare_interval"
        return await call(self, request)

    monkeypatch.setattr(AuthorityBrokerRuntime, "call", no_prepare)
    async with open_r15_runtime(database, clock=EvaluationSchedulerClock(0)) as restarted:
        replay = await restarted.adopt_scheduler_tick("hermetic-ingress", accepted)
        assert replay.kind == "EXACT_REPLAY" and replay.complete_records == retained
        assert len(restarted._loop_snapshot().records) == 1


@pytest.mark.parametrize("mutation", ["reorder", "omit", "clock", "generation"])
async def test_changed_owner_or_sources_select_no_tick(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    clock = EvaluationSchedulerClock(30)
    async with open_r15_runtime(tmp_path / "mutation.sqlite", clock=clock) as runtime:
        await configure(runtime)
        accepted = await adoption(runtime)
        before = runtime._owner_decisions().snapshot()
        call = runtime._supervisor.runtime().call

        async def alter(request: PublicPortCall) -> PublicPortResult:
            result = await call(request)
            if request.operation_id != "scheduler.prepare_interval":
                return result
            assert isinstance(result, PublicPortSuccess)
            if mutation == "clock":
                clock.unix_ns = 0
            elif mutation == "generation":
                runtime._start_generation()
            else:
                output = PreparedSchedulerBatch.model_validate_json(result.canonical_payload)
                records = (
                    tuple(reversed(output.records))
                    if mutation == "reorder"
                    else output.records[:-1]
                )
                result = result.model_copy(
                    update={
                        "canonical_payload": output.model_copy(
                            update={"records": records}
                        ).canonical_bytes()
                    }
                )
            return result

        monkeypatch.setattr(runtime._supervisor.runtime(), "call", alter)
        with pytest.raises(LoopRejected):
            await runtime.adopt_scheduler_tick("hermetic-ingress", accepted)
        assert runtime._owner_decisions().snapshot() == before
        assert runtime._loop_snapshot().records == ()


async def test_tick_adoption_is_exact_and_worker_cannot_execute_unfenced(tmp_path: Path) -> None:
    from chiplog.capabilities.agent_loop.domain import transition
    from chiplog.composition.r15_scheduler_runtime import _SchedulerLoopStore

    async with open_r15_runtime(
        tmp_path / "scope.sqlite", clock=EvaluationSchedulerClock(30)
    ) as runtime:
        await configure(runtime)
        with pytest.raises(LoopRejected):
            await runtime.adopt_scheduler_tick(
                "hermetic-ingress",
                TickPolicyAdoption(
                    adoption_act_id="old-config", policy_bytes=b'{"kind":"GENESIS"}'
                ),
            )
        exact = await adoption(runtime)
        result = await runtime.adopt_scheduler_tick("hermetic-ingress", exact)
        assert result.kind == "COMMITTED"
        changed = json.loads(exact.policy_bytes)
        changed["delivery_id"] = "other-delivery"
        from chiplog.composition.r15_tick_contracts import TickPolicyPreview

        conflict = await runtime.adopt_scheduler_tick(
            "hermetic-ingress",
            TickPolicyAdoption(
                adoption_act_id=exact.adoption_act_id,
                policy_bytes=TickPolicyPreview.model_validate(changed).canonical_bytes(),
            ),
        )
        assert conflict.kind == "CONFLICT"
        snapshot = runtime._loop_snapshot()
        run = snapshot.records[0]
        before = runtime._owner_decisions().snapshot()
        with pytest.raises(LoopRejected, match="live lease"):
            await _SchedulerLoopStore(runtime).publish(transition(run, "ACTIVE"), snapshot)
        assert runtime._loop_snapshot() == snapshot
        assert runtime._owner_decisions().snapshot() == before


@pytest.mark.parametrize(
    "field", ["clock", "registered_policy", "operation_alias", "clock_substitution"]
)
async def test_pending_tick_missing_provenance_rejects_before_physical_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    from chiplog.composition.r14_runtime import AnchoredOwnerDecisionJournal
    from chiplog.platform._owner_publication_contracts import SingleOwnerBatch, WorkerAuthentication
    from chiplog.platform.owner_decision_journal import (
        OwnerJournalIntegrityError,
        OwnerJournalSnapshot,
    )

    database = tmp_path / "provenance.sqlite"
    async with open_r15_runtime(database, clock=EvaluationSchedulerClock(30)) as runtime:
        await configure(runtime)
        exact = await adoption(runtime)
        before_frontier = runtime._loop_snapshot().tenant_head
        submit = runtime._appender.submit

        async def fail(command: PhysicalPublicationCommand) -> PublicationResult:
            return await submit(replace(command, fault="before_commit"))

        with monkeypatch.context() as patch:
            patch.setattr(runtime._appender, "submit", fail)
            with pytest.raises(OwnerPublicationUncertain):
                await runtime.adopt_scheduler_tick("hermetic-ingress", exact)
    snapshot = AnchoredOwnerDecisionJournal.snapshot

    def rehash(batch: SingleOwnerBatch) -> SingleOwnerBatch:
        raw = json.dumps(
            batch.model_dump(mode="json", exclude={"complete_batch_fingerprint"}),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
        return batch.model_copy(
            update={"complete_batch_fingerprint": hashlib.sha256(raw).hexdigest()}
        )

    def corrupt(self: AnchoredOwnerDecisionJournal) -> OwnerJournalSnapshot:
        # Deliberately cross the authenticated journal boundary to reach the semantic
        # pre-recovery validator. Real disk tampering is independently rejected earlier.
        original = snapshot(self)
        decision = original.decisions[-1]
        batch = decision.prepared.request
        assert isinstance(batch, SingleOwnerBatch)
        assert isinstance(batch.authentication, WorkerAuthentication)
        if field == "operation_alias":
            changed = replace(
                decision,
                prepared=replace(
                    decision.prepared,
                    request=rehash(
                        batch.model_copy(update={"operation": "recovery.record_result"})
                    ),
                ),
            )
            return replace(original, decisions=(*original.decisions[:-1], changed))
        raw = json.loads(batch.authentication.applicability_bytes)
        if field == "clock_substitution":
            raw["clock"]["reading"]["unix_ns"] += 1
        else:
            del raw[field]
        payload = json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
        auth = batch.authentication.model_copy(
            update={
                "applicability_bytes": payload,
                "applicability_fingerprint": hashlib.sha256(payload).hexdigest(),
            }
        )
        if field == "clock_substitution":
            from chiplog.composition.r15_tick_contracts import TickIssuanceEvidence

            payload = TickIssuanceEvidence.model_validate_json(payload).canonical_bytes()
            auth = auth.model_copy(
                update={
                    "applicability_bytes": payload,
                    "applicability_fingerprint": hashlib.sha256(payload).hexdigest(),
                }
            )
            proof = json.dumps(
                {
                    "nonce": auth.invocation.issuance_id,
                    "identity": batch.identity.model_dump(mode="json"),
                    "command": batch.command.model_dump(mode="json"),
                    "evidence": auth.applicability_fingerprint,
                },
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
            auth = auth.model_copy(
                update={
                    "invocation": auth.invocation.model_copy(
                        update={"issuance_fingerprint": hashlib.sha256(proof).hexdigest()}
                    )
                }
            )
        changed = replace(
            decision,
            prepared=replace(
                decision.prepared, request=rehash(batch.model_copy(update={"authentication": auth}))
            ),
        )
        return replace(original, decisions=(*original.decisions[:-1], changed))

    monkeypatch.setattr(AnchoredOwnerDecisionJournal, "snapshot", corrupt)
    with pytest.raises(OwnerJournalIntegrityError):
        async with open_r15_runtime(database):
            pytest.fail("corrupt selected provenance exposed a runtime")
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute(
            "SELECT head FROM tenant_heads WHERE tenant_id='hermetic-tenant'"
        ).fetchone() == (before_frontier,)
        assert connection.execute(
            "SELECT COUNT(*) FROM publications WHERE operation_kind='scheduler.decide_interval'"
        ).fetchone() == (0,)


@pytest.mark.parametrize(
    ("field", "dimension"),
    [
        ("max_member_count", "MEMBER_COUNT"),
        ("max_manifest_bytes", "MANIFEST_BYTES"),
        ("max_serialized_batch_bytes", "SERIALIZED_BATCH_BYTES"),
    ],
)
async def test_all_registered_bounds_hold_without_partial_runs(
    tmp_path: Path,
    field: str,
    dimension: str,
) -> None:
    values = {
        "max_member_count": 4,
        "max_manifest_bytes": 65536,
        "max_serialized_batch_bytes": 1_048_576,
    }
    values[field] = 1
    async with open_r15_runtime(
        tmp_path / "bound.sqlite", clock=EvaluationSchedulerClock(30)
    ) as runtime:
        await configure(runtime, bound=SchedulerIntervalBound.model_validate(values))
        result = await runtime.adopt_scheduler_tick("hermetic-ingress", await adoption(runtime))
        assert result.kind == "COMMITTED"
        assert len(result.complete_records) == 1
        hold = json.loads(result.complete_records[0].canonical_bytes)
        assert hold["exceeded_dimension"] == dimension
        assert hold["boundary"]["previous_due_boundary"]["canonical_coordinate"] == "10"
        assert runtime._loop_snapshot().records == ()


async def test_scheduled_runs_coexist_with_ordinary_loop_after_restart(tmp_path: Path) -> None:
    from chiplog.composition.r15_scheduler_runtime import open_r15_loop

    database = tmp_path / "ordinary.sqlite"
    async with open_r15_runtime(database, clock=EvaluationSchedulerClock(30)) as runtime:
        await configure(runtime)
        exact = await adoption(runtime)
        result = await runtime.adopt_scheduler_tick("hermetic-ingress", exact)
        assert result.kind == "COMMITTED"
        scheduled = runtime._loop_snapshot().records[0]
    response = b'{"kind":"Complete","deliveries":[{"kind":"NonAuthoritativeText","text":"Ready"}]}'
    tool_response = (
        b'{"kind":"Continue","tool_calls":[{"call_id":"plan",'
        b'"tool":"propose_planning","text":"Swim every week"}]}'
    )
    async with open_r15_loop(database, responses=(tool_response, response)) as loop:
        created = await loop.create("ordinary", "Hello", BudgetPolicy())
        active = await loop.activate("ordinary", created.head)
        continued = await loop.step("ordinary", active.head)
        assert continued.state == "ACTIVE"
        calls = loop.record("ordinary").turns[0].sealed_calls
        assert calls is not None and len(calls) == 1
        assert calls[0].state == "TERMINAL"
        assert calls[0].proposal_id == "ordinary/turn/1/proposal/plan"
        outcome = await loop.step("ordinary", continued.head)
        assert outcome.state == "SUCCEEDED"
        with pytest.raises(LoopRejected, match="authenticated binding"):
            loop.record(scheduled.run_id)
    async with open_r15_runtime(database) as restarted:
        replay = await restarted.adopt_scheduler_tick("hermetic-ingress", exact)
        assert replay.kind == "EXACT_REPLAY"
        assert replay.complete_records == result.complete_records
        assert restarted._loop_snapshot().records[0] == scheduled
        assert restarted._loop_snapshot().records[-1].run_id == "ordinary"


async def test_changed_configuration_rejects_fresh_adoption_but_preserves_selected_replay(
    tmp_path: Path,
) -> None:
    from chiplog.composition.r15_scheduler_registry import (
        PolicyAmendmentDraft,
        SchedulerConfigurationAdoption,
    )

    async with open_r15_runtime(
        tmp_path / "changed-config.sqlite", clock=EvaluationSchedulerClock(30)
    ) as runtime:
        await configure(runtime)
        exact = await adoption(runtime)
        result = await runtime.adopt_scheduler_tick("hermetic-ingress", exact)
        assert result.kind == "COMMITTED"
        stale = await adoption(runtime, "stale")
        preview = await runtime.preview_scheduler_configuration(
            "hermetic-ingress",
            PolicyAmendmentDraft(
                adoption_act_id="change-policy", schedule_id="schedule", missed_policy="SKIP"
            ),
        )
        change = await runtime.adopt_scheduler_configuration(
            "hermetic-ingress",
            SchedulerConfigurationAdoption(
                adoption_act_id="change-policy", command_bytes=preview.canonical_bytes()
            ),
        )
        assert change.kind == "COMMITTED"
        before = runtime._owner_decisions().snapshot()
        with pytest.raises(LoopRejected, match="stale"):
            await runtime.adopt_scheduler_tick("hermetic-ingress", stale)
        replay = await runtime.adopt_scheduler_tick("hermetic-ingress", exact)
        assert replay.kind == "EXACT_REPLAY" and replay.complete_records == result.complete_records
        assert runtime._owner_decisions().snapshot() == before


@pytest.mark.parametrize("mutation", ["instance_observe", "class_observe", "class_source"])
@pytest.mark.parametrize("boundary", ["after_preview", "after_owner", "writer"])
async def test_clock_callable_substitution_selects_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
    boundary: str,
) -> None:
    clock = EvaluationSchedulerClock(10)
    database = tmp_path / "clock-callable.sqlite"
    async with open_r15_runtime(database, clock=clock) as runtime:
        await configure(runtime)
        accepted = await adoption(runtime)
        before = runtime._owner_decisions().snapshot()
        with sqlite3.connect(database) as connection:
            physical_before = tuple(connection.iterdump())
        invoked = False
        changed = False

        def forbidden(*args: object) -> None:
            nonlocal invoked
            invoked = True
            pytest.fail("replaced clock descriptor was accessed")

        def alter() -> None:
            nonlocal changed
            changed = True
            if mutation == "instance_observe":
                monkeypatch.setattr(clock, "observe", forbidden)
            elif mutation == "class_observe":
                monkeypatch.setattr(EvaluationSchedulerClock, "observe", forbidden)
            else:
                monkeypatch.setattr(EvaluationSchedulerClock, "source", property(forbidden))

        if boundary == "after_preview":
            alter()
        elif boundary == "after_owner":
            call = runtime._supervisor.runtime().call

            async def after_owner(request: PublicPortCall) -> PublicPortResult:
                result = await call(request)
                if request.operation_id == "scheduler.prepare_interval":
                    alter()
                return result

            monkeypatch.setattr(runtime._supervisor.runtime(), "call", after_owner)
        else:
            submit = runtime._appender.submit

            async def at_writer(command: PhysicalPublicationCommand) -> PublicationResult:
                alter()
                return await submit(command)

            monkeypatch.setattr(runtime._appender, "submit", at_writer)
        if boundary == "writer":
            result = await runtime.adopt_scheduler_tick("hermetic-ingress", accepted)
            assert result.kind == "STALE"
        else:
            with pytest.raises(LoopRejected, match="clock implementation changed"):
                await runtime.adopt_scheduler_tick("hermetic-ingress", accepted)
        assert changed and not invoked
        assert runtime._owner_decisions().snapshot() == before
        with sqlite3.connect(database) as connection:
            assert tuple(connection.iterdump()) == physical_before
        assert runtime._loop_snapshot().records == ()


async def test_registered_evaluation_clock_allows_coordinate_advance_after_preview(
    tmp_path: Path,
) -> None:
    clock = EvaluationSchedulerClock(10)
    async with open_r15_runtime(tmp_path / "advance.sqlite", clock=clock) as runtime:
        await configure(runtime)
        accepted = await adoption(runtime)
        original = clock.source
        clock.unix_ns = 30
        result = await runtime.adopt_scheduler_tick("hermetic-ingress", accepted)
        assert result.kind == "COMMITTED"
        assert len(runtime._loop_snapshot().records) == 1
        assert clock.source == original
