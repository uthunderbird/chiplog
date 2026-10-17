"""Mounted signed service interval publication and exact restart replay."""

from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.composition.r15_scheduler_runtime import open_r15_runtime
from chiplog.composition.r15_tick_clock_v1 import EvaluationSchedulerClock
from chiplog.platform._owner_publication_contracts import (
    JournalSelectedPublication,
)
from tests.support.composition.r15_interval_service_runtime import (
    _adopt_service,
    _decide,
)
from tests.support.composition.scheduler_interval_runtime import configure


@pytest.mark.parametrize(
    ("policy", "cutoff", "expected_members", "expected_runs"),
    [
        ("COALESCE", 10, 0, 0),
        ("SKIP", 30, 2, 0),
        ("MATERIALIZE_EACH", 30, 2, 2),
    ],
)
async def test_signed_service_publishes_complete_policy_algebra(
    tmp_path: Path,
    policy: str,
    cutoff: int,
    expected_members: int,
    expected_runs: int,
) -> None:
    clock = EvaluationSchedulerClock(cutoff)
    key = Ed25519PrivateKey.generate()
    async with open_r15_runtime(tmp_path / "service.sqlite", clock=clock) as runtime:
        await configure(runtime, policy)
        with pytest.raises(LoopRejected, match=r"selected|policy|service"):
            await _decide(runtime, key, "before-adoption")
        await _adopt_service(runtime, key)
        result = await _decide(runtime, key, "one")
        assert isinstance(result, JournalSelectedPublication) and result.kind == "COMMITTED"
        interval = next(
            row for row in result.complete_records if row.record_kind == "interval-result"
        )
        from chiplog.capabilities.agent_loop.scheduler_contracts import ScheduledIntervalDecision

        decision = ScheduledIntervalDecision.model_validate_json(interval.canonical_bytes)
        assert len(decision.manifest.members) == expected_members
        assert len(decision.dispositions) == expected_members
        assert len(runtime._loop_snapshot().records) == expected_runs


async def test_signed_service_replays_original_after_restart_and_rejects_bad_signature(
    tmp_path: Path,
) -> None:
    database = tmp_path / "service-replay.sqlite"
    clock = EvaluationSchedulerClock(30)
    key = Ed25519PrivateKey.generate()
    async with open_r15_runtime(database, clock=clock) as runtime:
        await configure(runtime, "MATERIALIZE_EACH")
        await _adopt_service(runtime, key, max_cycles=1)
        session = await runtime.open_scheduler_interval_service_session("hermetic-ingress")
        with pytest.raises(LoopRejected, match="signature"):
            await runtime.decide_scheduler_interval(session, "schedule", "one", b"bad")
        first = await runtime.decide_scheduler_interval(
            session, "schedule", "one", key.sign(session.signed_message("schedule", "one"))
        )
        assert first.kind == "COMMITTED"
        retained = first.complete_records
        with pytest.raises(LoopRejected, match=r"cycles|mandate"):
            await _decide(runtime, key, "second")
    clock.unix_ns = 500
    async with open_r15_runtime(database, clock=clock) as restarted:
        replay = await _decide(restarted, key, "one")
        assert isinstance(replay, JournalSelectedPublication) and replay.kind == "EXACT_REPLAY"
        assert replay.complete_records == retained
        assert len(restarted._loop_snapshot().records) == 2
