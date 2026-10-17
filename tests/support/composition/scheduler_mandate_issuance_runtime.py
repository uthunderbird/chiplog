"""Shared builders extracted without changing their behavior."""

import hashlib
import json

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.agent_loop.scheduler_configuration import policy_head, schedule_head
from chiplog.capabilities.agent_loop.scheduler_contracts import SchedulerIntervalBound
from chiplog.composition.r15_mandate_contracts import MandateIssuanceAdoption
from chiplog.composition.r15_mandate_scope_registry import (
    EXECUTION_SERVICE_IDENTITY,
    registered_scope_sources,
)
from chiplog.composition.r15_scheduler_registry import (
    SchedulerGenesisDraft,
)
from chiplog.composition.scheduler_source_registry import read_admitted_scheduler_startup


def _draft() -> SchedulerGenesisDraft:
    return SchedulerGenesisDraft(
        adoption_act_id="mandate-test-genesis",
        schedule_id="mandate-test-schedule",
        start_ns=0,
        period_ns=1_000_000_000,
        end_exclusive_ns="NO_END",
        prompt="Review the plan",
        policy=BudgetPolicy(),
        missed_policy="SKIP",
        interval_bound=SchedulerIntervalBound(
            max_member_count=16,
            max_manifest_bytes=65536,
            max_serialized_batch_bytes=1_048_576,
        ),
    )


def _head(subject: str, record: str, fingerprint: str) -> CallSubjectHead:
    return CallSubjectHead(
        subject_id=subject, revision=Present(head=record, fingerprint=fingerprint)
    )


async def _adoption(runtime: object, *, act: str = "issue-once") -> MandateIssuanceAdoption:
    startup = read_admitted_scheduler_startup(
        runtime._database,
        runtime._tenant_id,
        runtime._owner_decisions(),
        runtime._commitment_journal,
    )
    schedule = startup.index.schedules[0]
    policy = startup.index.policies[0]
    bound = startup.index.bounds[0][1]
    schedule_ref, policy_ref = schedule_head(schedule), policy_head(policy)
    registered = registered_scope_sources()
    scope = {
        "tenant_id": "hermetic-tenant",
        "mandate_id": "mandate-test-id",
        "service_identity": EXECUTION_SERVICE_IDENTITY,
        "beneficiary_principal": "hermetic-principal",
        "schedule_head": _head(
            schedule_ref.schedule_id, schedule_ref.head, schedule_ref.fingerprint
        ).model_dump(mode="json"),
        "missed_policy_head": _head(
            policy_ref.policy_id, policy_ref.head, policy_ref.fingerprint
        ).model_dump(mode="json"),
        "bound_head": _head(
            schedule_ref.schedule_id,
            bound.head_id,
            hashlib.sha256(bound.canonical_bytes()).hexdigest(),
        ).model_dump(mode="json"),
        "prompt_template_fingerprint": hashlib.sha256(
            schedule.definition.run_inputs.prompt.encode()
        ).hexdigest(),
        "run_budget_policy": schedule.definition.run_inputs.policy.model_dump(mode="json"),
        "permitted_tools": [],
        "origin": registered.origin.model_dump(mode="json"),
        "delivery_scope": registered.delivery.model_dump(mode="json"),
        "recipient_scope": registered.recipient.model_dump(mode="json"),
        "disclosure_scope": registered.disclosure.model_dump(mode="json"),
        "operations": ["scheduler.execute_interval"],
        "issuance_generation": 0,
        "horizon": {
            "coordinate_codec": "chiplog.scheduler.unix-ns.v1",
            "first_eligible_due_coordinate": "0",
            "last_eligible_due_coordinate": "0",
            "expires_at_unix_ns": runtime._tick_clock.observe().unix_ns + 100_000_000_000,
            "max_cycles": 1,
            "max_runs": 1,
            "max_consequential_calls": 0,
        },
    }
    preview = await runtime.preview_scheduler_mandate_clock(
        "hermetic-ingress", act, json.dumps(scope, sort_keys=True, separators=(",", ":")).encode()
    )
    return MandateIssuanceAdoption(
        adoption_act_id=act,
        canonical_scope_bytes=preview.canonical_scope_bytes,
        canonical_policy_bytes=registered.administrative_policy_bytes,
    )
