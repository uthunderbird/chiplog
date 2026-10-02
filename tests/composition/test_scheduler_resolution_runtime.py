"""Mounted overflow hold, explicit bound replacement and operator resolution."""

from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from chiplog.capabilities.agent_loop.contracts import BudgetPolicy, LoopRejected
from chiplog.capabilities.agent_loop.scheduler_contracts import SchedulerIntervalBound
from chiplog.capabilities.agent_loop.scheduler_preparation import PreparedSchedulerBatch
from chiplog.composition.r15_resolution_contracts import (
    ResolutionPolicyAdoption,
    ResolutionPolicyDraft,
)
from chiplog.composition.r15_scheduler_registry import (
    BoundReplacementDraft,
    PolicyAmendmentDraft,
    SchedulerConfigurationAdoption,
    SchedulerGenesisAdoption,
    SchedulerGenesisDraft,
)
from chiplog.composition.r15_scheduler_runtime import R15SchedulerRuntime, open_r15_runtime
from chiplog.composition.r15_tick_clock_v1 import EvaluationSchedulerClock
from chiplog.composition.r15_tick_contracts import TickPolicyAdoption, TickPolicyDraft
from chiplog.platform._sqlite import PhysicalPublicationCommand, PublicationResult
from chiplog.platform.broker import PublicPortCall, PublicPortResult, PublicPortSuccess
from chiplog.platform.owner_publications import OwnerPublicationUncertain
from tests.composition.test_r15_interval_service_runtime import _adopt_service, _decide


async def _configure(
    runtime: R15SchedulerRuntime, bound: SchedulerIntervalBound | None = None
) -> None:
    draft = SchedulerGenesisDraft(
        adoption_act_id="genesis",
        schedule_id="schedule",
        start_ns=10,
        period_ns=10,
        end_exclusive_ns="NO_END",
        prompt="Review",
        policy=BudgetPolicy(),
        missed_policy="COALESCE",
        interval_bound=bound
        or SchedulerIntervalBound(
            max_member_count=1,
            max_manifest_bytes=65536,
            max_serialized_batch_bytes=1_048_576,
        ),
    )
    preview = await runtime.preview_scheduler_genesis("hermetic-ingress", draft)
    assert (
        await runtime.adopt_scheduler_genesis(
            "hermetic-ingress",
            SchedulerGenesisAdoption(
                adoption_act_id="genesis",
                command_bytes=preview.canonical_bytes(),
            ),
        )
    ).kind == "COMMITTED"


async def _hold(runtime: R15SchedulerRuntime) -> None:
    preview = await runtime.preview_scheduler_tick_policy(
        "hermetic-ingress",
        TickPolicyDraft(adoption_act_id="tick", schedule_id="schedule", delivery_id="delivery"),
    )
    result = await runtime.adopt_scheduler_tick(
        "hermetic-ingress",
        TickPolicyAdoption(adoption_act_id="tick", policy_bytes=preview.canonical_bytes()),
    )
    assert result.kind == "COMMITTED"
    assert tuple(record.record_kind for record in result.complete_records) == ("overflow-hold",)


async def _replace_bound(runtime: R15SchedulerRuntime, act: str = "bound", limit: int = 4) -> None:
    draft = BoundReplacementDraft(
        adoption_act_id=act,
        schedule_id="schedule",
        interval_bound=SchedulerIntervalBound(
            max_member_count=limit,
            max_manifest_bytes=65536,
            max_serialized_batch_bytes=1_048_576,
        ),
    )
    preview = await runtime.preview_scheduler_configuration("hermetic-ingress", draft)
    assert (
        await runtime.adopt_scheduler_configuration(
            "hermetic-ingress",
            SchedulerConfigurationAdoption(
                adoption_act_id=act,
                command_bytes=preview.canonical_bytes(),
            ),
        )
    ).kind == "COMMITTED"


@pytest.mark.asyncio
async def test_selected_resolution_advances_once_and_replays_after_restart(tmp_path: Path) -> None:
    database = tmp_path / "resolution.sqlite"
    adoption: ResolutionPolicyAdoption
    async with open_r15_runtime(database, clock=EvaluationSchedulerClock(40)) as runtime:
        await _configure(runtime)
        await _hold(runtime)
        with pytest.raises(LoopRejected, match="successor interval bound"):
            await runtime.preview_scheduler_resolution_policy(
                "hermetic-ingress",
                ResolutionPolicyDraft(
                    adoption_act_id="resolution",
                    schedule_id="schedule",
                ),
            )
        await _replace_bound(runtime)
        preview = await runtime.preview_scheduler_resolution_policy(
            "hermetic-ingress",
            ResolutionPolicyDraft(
                adoption_act_id="resolution",
                schedule_id="schedule",
            ),
        )
        adoption = ResolutionPolicyAdoption(
            adoption_act_id="resolution",
            policy_bytes=preview.canonical_bytes(),
        )
        result = await runtime.adopt_scheduler_resolution("hermetic-ingress", adoption)
        assert result.kind == "COMMITTED"
        assert (
            sum(record.record_kind == "interval-parent" for record in result.complete_records) == 1
        )
        assert (
            sum(record.record_kind == "interval-result" for record in result.complete_records) == 1
        )
        assert (
            sum(record.record_kind == "overflow-resolution" for record in result.complete_records)
            == 1
        )
        assert len(runtime._loop_snapshot().records) == 1
    async with open_r15_runtime(database, clock=EvaluationSchedulerClock(0)) as runtime:
        replay = await runtime.adopt_scheduler_resolution("hermetic-ingress", adoption)
        assert replay.kind == "EXACT_REPLAY"
        assert len(runtime._loop_snapshot().records) == 1


@pytest.mark.asyncio
async def test_service_overflow_hold_resolves_after_successor_bound(tmp_path: Path) -> None:
    database = tmp_path / "service-resolution.sqlite"
    key = Ed25519PrivateKey.generate()
    async with open_r15_runtime(database, clock=EvaluationSchedulerClock(40)) as runtime:
        await _configure(runtime)
        await _adopt_service(runtime, key)
        held = await _decide(runtime, key, "service-hold")
        assert held.kind == "COMMITTED"
        assert tuple(record.record_kind for record in held.complete_records) == ("overflow-hold",)
        await _replace_bound(runtime)
        preview = await runtime.preview_scheduler_resolution_policy(
            "hermetic-ingress",
            ResolutionPolicyDraft(adoption_act_id="service-resolution", schedule_id="schedule"),
        )
        adoption = ResolutionPolicyAdoption(
            adoption_act_id="service-resolution", policy_bytes=preview.canonical_bytes()
        )
        result = await runtime.adopt_scheduler_resolution("hermetic-ingress", adoption)
        assert result.kind == "COMMITTED"
        kinds = tuple(record.record_kind for record in result.complete_records)
        assert kinds.count("interval-parent") == 1
        assert kinds.count("interval-result") == 1
        assert kinds.count("overflow-resolution") == 1
        run_count = len(runtime._loop_snapshot().records)
        assert run_count > 0
    async with open_r15_runtime(database, clock=EvaluationSchedulerClock(0)) as runtime:
        replay = await runtime.adopt_scheduler_resolution("hermetic-ingress", adoption)
        assert replay.kind == "EXACT_REPLAY"
        assert len(runtime._loop_snapshot().records) == run_count


@pytest.mark.asyncio
async def test_insufficient_successor_and_stale_or_rival_resolution_select_nothing(
    tmp_path: Path,
) -> None:
    async with open_r15_runtime(
        tmp_path / "negative.sqlite", clock=EvaluationSchedulerClock(40)
    ) as runtime:
        await _configure(runtime)
        await _hold(runtime)
        early = await runtime.preview_scheduler_configuration(
            "hermetic-ingress",
            PolicyAmendmentDraft(
                adoption_act_id="early-amend",
                schedule_id="schedule",
                missed_policy="SKIP",
            ),
        )
        before = runtime._owner_decisions().snapshot()
        with pytest.raises(LoopRejected, match="overflow hold"):
            await runtime.adopt_scheduler_configuration(
                "hermetic-ingress",
                SchedulerConfigurationAdoption(
                    adoption_act_id="early-amend",
                    command_bytes=early.canonical_bytes(),
                ),
            )
        assert runtime._owner_decisions().snapshot() == before
        await _replace_bound(runtime, "too-small", limit=2)
        preview = await runtime.preview_scheduler_resolution_policy(
            "hermetic-ingress",
            ResolutionPolicyDraft(
                adoption_act_id="stale",
                schedule_id="schedule",
            ),
        )
        small = ResolutionPolicyAdoption(
            adoption_act_id="stale",
            policy_bytes=preview.canonical_bytes(),
        )
        before = runtime._owner_decisions().snapshot()
        with pytest.raises(LoopRejected, match="successor bound"):
            await runtime.adopt_scheduler_resolution("hermetic-ingress", small)
        assert runtime._owner_decisions().snapshot() == before
        await _replace_bound(runtime, "sufficient", limit=4)
        before = runtime._owner_decisions().snapshot()
        with pytest.raises(LoopRejected, match="adopted heads"):
            await runtime.adopt_scheduler_resolution("hermetic-ingress", small)
        assert runtime._owner_decisions().snapshot() == before
        fresh = await runtime.preview_scheduler_resolution_policy(
            "hermetic-ingress",
            ResolutionPolicyDraft(
                adoption_act_id="fresh",
                schedule_id="schedule",
            ),
        )
        adopted = ResolutionPolicyAdoption(
            adoption_act_id="fresh",
            policy_bytes=fresh.canonical_bytes(),
        )
        assert (
            await runtime.adopt_scheduler_resolution("hermetic-ingress", adopted)
        ).kind == "COMMITTED"
        before = runtime._owner_decisions().snapshot()
        with pytest.raises(LoopRejected, match="active overflow hold"):
            await runtime.preview_scheduler_resolution_policy(
                "hermetic-ingress",
                ResolutionPolicyDraft(
                    adoption_act_id="rival",
                    schedule_id="schedule",
                ),
            )
        assert runtime._owner_decisions().snapshot() == before


@pytest.mark.parametrize("fault", ["before_commit", "after_commit"])
async def test_selected_resolution_recovers_exact_bytes_across_writer_fault(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: Literal["before_commit", "after_commit"],
) -> None:
    database = tmp_path / f"{fault}.sqlite"
    async with open_r15_runtime(database, clock=EvaluationSchedulerClock(40)) as runtime:
        await _configure(runtime)
        await _hold(runtime)
        await _replace_bound(runtime)
        preview = await runtime.preview_scheduler_resolution_policy(
            "hermetic-ingress",
            ResolutionPolicyDraft(
                adoption_act_id="resolution",
                schedule_id="schedule",
            ),
        )
        adoption = ResolutionPolicyAdoption(
            adoption_act_id="resolution",
            policy_bytes=preview.canonical_bytes(),
        )
        submit = runtime._appender.submit

        async def fail(command: PhysicalPublicationCommand) -> PublicationResult:
            return await submit(replace(command, fault=fault))

        with monkeypatch.context() as patch:
            patch.setattr(runtime._appender, "submit", fail)
            with pytest.raises(OwnerPublicationUncertain):
                await runtime.adopt_scheduler_resolution("hermetic-ingress", adoption)
        selected = runtime._owner_decisions().snapshot().decisions[-1]
        retained = selected.prepared.request.complete_records
    async with open_r15_runtime(database, clock=EvaluationSchedulerClock(0)) as runtime:
        replay = await runtime.adopt_scheduler_resolution("hermetic-ingress", adoption)
        assert replay.kind == "EXACT_REPLAY"
        assert replay.complete_records == retained
        assert len(runtime._loop_snapshot().records) == 1


@pytest.mark.parametrize("dimension", ["MANIFEST_BYTES", "SERIALIZED_BATCH_BYTES"])
async def test_streamed_or_batch_overflow_resolves_only_after_sufficient_bound(
    tmp_path: Path, dimension: str
) -> None:
    bound = SchedulerIntervalBound(
        max_member_count=4,
        max_manifest_bytes=1 if dimension == "MANIFEST_BYTES" else 65536,
        max_serialized_batch_bytes=100 if dimension == "SERIALIZED_BATCH_BYTES" else 1_048_576,
    )
    async with open_r15_runtime(
        tmp_path / f"{dimension}.sqlite", clock=EvaluationSchedulerClock(40)
    ) as runtime:
        await _configure(runtime, bound)
        await _hold(runtime)
        hold = runtime._owner_decisions().snapshot().decisions[-1]
        assert hold.prepared.request.complete_records[0].record_kind == "overflow-hold"
        await _replace_bound(runtime)
        preview = await runtime.preview_scheduler_resolution_policy(
            "hermetic-ingress",
            ResolutionPolicyDraft(
                adoption_act_id=dimension,
                schedule_id="schedule",
            ),
        )
        adoption = ResolutionPolicyAdoption(
            adoption_act_id=dimension,
            policy_bytes=preview.canonical_bytes(),
        )
        result = await runtime.adopt_scheduler_resolution("hermetic-ingress", adoption)
        assert result.kind == "COMMITTED"
        assert len(runtime._loop_snapshot().records) == 1


async def test_omitted_resolution_member_selects_no_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with open_r15_runtime(
        tmp_path / "omitted.sqlite", clock=EvaluationSchedulerClock(40)
    ) as runtime:
        await _configure(runtime)
        await _hold(runtime)
        await _replace_bound(runtime)
        preview = await runtime.preview_scheduler_resolution_policy(
            "hermetic-ingress",
            ResolutionPolicyDraft(
                adoption_act_id="resolution",
                schedule_id="schedule",
            ),
        )
        adoption = ResolutionPolicyAdoption(
            adoption_act_id="resolution",
            policy_bytes=preview.canonical_bytes(),
        )
        before = runtime._owner_decisions().snapshot()
        call = runtime._supervisor.runtime().call

        async def omit(request: PublicPortCall) -> PublicPortResult:
            response = await call(request)
            if request.operation_id != "scheduler.prepare_interval":
                return response
            assert isinstance(response, PublicPortSuccess)
            prepared = PreparedSchedulerBatch.model_validate_json(response.canonical_payload)
            return response.model_copy(
                update={
                    "canonical_payload": prepared.model_copy(
                        update={"records": prepared.records[:-1]}
                    ).canonical_bytes()
                }
            )

        monkeypatch.setattr(runtime._supervisor.runtime(), "call", omit)
        with pytest.raises(LoopRejected, match="complete preparation"):
            await runtime.adopt_scheduler_resolution("hermetic-ingress", adoption)
        assert runtime._owner_decisions().snapshot() == before
