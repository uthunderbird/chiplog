"""Shared builders extracted without changing their behavior."""

from chiplog.capabilities.agent_loop.contracts import BudgetPolicy
from chiplog.capabilities.agent_loop.scheduler_contracts import SchedulerIntervalBound
from chiplog.composition.r15_scheduler_registry import (
    SchedulerGenesisAdoption,
    SchedulerGenesisDraft,
)
from chiplog.composition.r15_scheduler_runtime import R15SchedulerRuntime


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
