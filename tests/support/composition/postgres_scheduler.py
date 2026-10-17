"""Shared builders extracted without changing their behavior."""

from dataclasses import dataclass
from typing import Literal, TypedDict

from tests.support.scheduler_execution import origin

from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.capabilities.agent_loop.contracts import (
    BudgetPolicy,
)
from chiplog.capabilities.agent_loop.delivery_contracts import (
    ExactHead,
    OriginSelection,
    ProviderRecipient,
)
from chiplog.capabilities.agent_loop.scheduler_client_contracts import (
    NativeScheduleDraft,
    NativeSchedulerAdoption,
    NativeScheduleSelection,
)
from chiplog.capabilities.agent_loop.scheduler_contracts import SchedulerIntervalBound
from chiplog.composition.postgres_effects import PostgresEffectsBinding
from chiplog.composition.postgres_execution import PostgresExecutionRuntime
from chiplog.composition.postgres_planning import PostgresPlanningRuntime
from chiplog.composition.postgres_scheduler import PostgresScheduler
from chiplog.platform.postgres import PostgresServer

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
