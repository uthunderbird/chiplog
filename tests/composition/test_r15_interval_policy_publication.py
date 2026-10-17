"""Fail-closed checks for the separate interval service policy selection."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.scheduler_configuration import policy_head, schedule_head
from chiplog.composition.r15_interval_policy import (
    IntervalDecisionMandateV3,
    IntervalPolicyAdoptionRequest,
)
from chiplog.composition.r15_interval_policy_publication import (
    OPERATION,
    adopt_interval_policy,
    selected_interval_policy_from_decisions,
    validate_selected_interval_policy,
)
from chiplog.composition.r15_interval_service_auth import IntervalExecutionAuthority
from chiplog.composition.r15_scheduler_registry import SchedulerGenesisAdoption
from chiplog.composition.r15_scheduler_runtime import open_r15_runtime
from chiplog.composition.r15_tick_clock_v1 import EvaluationSchedulerClock
from chiplog.composition.scheduler_source_registry import read_admitted_scheduler_startup
from tests.support.composition.scheduler_mandate_issuance_runtime import _draft


def test_interval_service_needs_separate_selected_administration() -> None:
    with pytest.raises(LoopRejected, match="separately selected"):
        selected_interval_policy_from_decisions(())


def test_constructible_operation_without_broker_envelope_is_rejected() -> None:
    with pytest.raises(LoopRejected, match="publication envelope"):
        validate_selected_interval_policy(SimpleNamespace(operation=OPERATION))


async def test_mounted_interval_policy_adoption_selects_and_replays(tmp_path: Path) -> None:
    database = tmp_path / "interval-policy.sqlite"
    clock = EvaluationSchedulerClock(1_000_000_000)
    async with open_r15_runtime(database, clock=clock) as runtime:
        draft = _draft()
        command = await runtime.preview_scheduler_genesis("hermetic-ingress", draft)
        genesis = SchedulerGenesisAdoption(
            adoption_act_id=draft.adoption_act_id, command_bytes=command.canonical_bytes()
        )
        assert (
            await runtime.adopt_scheduler_genesis("hermetic-ingress", genesis)
        ).kind == "COMMITTED"
        startup = read_admitted_scheduler_startup(
            runtime._database,
            runtime._tenant_id,
            runtime._owner_decisions(),
            runtime._commitment_journal,
        )
        assert startup is not None
        schedule = startup.index.schedules[0]
        policy = startup.index.policies[0]
        bound = startup.index.bounds[0][1]
        mandate = IntervalDecisionMandateV3(
            schedule=schedule_head(schedule),
            missed_policy=policy_head(policy),
            bound=bound,
            first_due_ns=0,
            last_due_ns=1_000_000_000,
            expires_at_unix_ns=1_000_000_000_000,
            max_cycles=2,
        )
        request = IntervalPolicyAdoptionRequest(
            adoption_act_id="interval-service-test", mandate_bytes=mandate.canonical_bytes()
        )
        authority = IntervalExecutionAuthority(public_key=b"x" * 32)
        first = await adopt_interval_policy(runtime, "hermetic-ingress", request, authority)
        assert first.kind == "COMMITTED"
        replay = await adopt_interval_policy(runtime, "hermetic-ingress", request, authority)
        assert replay.kind == "EXACT_REPLAY"
        assert replay.complete_records == first.complete_records
