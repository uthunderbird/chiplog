"""Shared builders extracted without changing their behavior."""

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from chiplog.capabilities.agent_loop.scheduler_configuration import policy_head, schedule_head
from chiplog.composition.r15_interval_policy import (
    IntervalDecisionMandateV3,
    IntervalPolicyAdoptionRequest,
)
from chiplog.composition.r15_interval_service_auth import IntervalExecutionAuthority
from chiplog.composition.r15_scheduler_runtime import R15SchedulerRuntime
from chiplog.composition.scheduler_source_registry import (
    AdmittedSchedulerStartup,
    read_admitted_scheduler_startup,
)
from chiplog.platform._owner_publication_contracts import (
    BrokerPublicationResult,
)


async def _adopt_service(
    runtime: R15SchedulerRuntime,
    key: Ed25519PrivateKey,
    *,
    max_cycles: int = 2,
) -> None:
    startup = read_admitted_scheduler_startup(
        runtime._database,
        runtime._tenant_id,
        runtime._owner_decisions(),
        runtime._commitment_journal,
    )
    assert isinstance(startup, AdmittedSchedulerStartup)
    schedule = startup.index.schedules[0]
    policy = startup.index.policies[0]
    bound = startup.index.bounds[0][1]
    mandate = IntervalDecisionMandateV3(
        schedule=schedule_head(schedule),
        missed_policy=policy_head(policy),
        bound=bound,
        first_due_ns=10,
        last_due_ns=100,
        expires_at_unix_ns=1_000_000_000_000,
        max_cycles=max_cycles,
    )
    request = IntervalPolicyAdoptionRequest(
        adoption_act_id="signed-interval-service",
        mandate_bytes=mandate.canonical_bytes(),
    )
    authority = IntervalExecutionAuthority(
        public_key=key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    )
    result = await runtime.adopt_scheduler_interval_service_policy(
        "hermetic-ingress", request, authority
    )
    assert result.kind == "COMMITTED"


async def _decide(
    runtime: R15SchedulerRuntime,
    key: Ed25519PrivateKey,
    act_id: str,
) -> BrokerPublicationResult:
    session = await runtime.open_scheduler_interval_service_session("hermetic-ingress")
    signature = key.sign(session.signed_message("schedule", act_id))
    return await runtime.decide_scheduler_interval(session, "schedule", act_id, signature)
