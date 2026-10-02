"""Independent signed R15.1 service caller; private key stays outside runtime."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from pydantic import Field

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.deployment_trust.operator_policy_command_verifier import (
    _verify_ed25519_signature,
)
from chiplog.composition.r15_interval_policy import (
    INTERVAL_SERVICE,
    SelectedIntervalMandateV3,
    validate_historical_interval_policy,
    validate_mandate_configuration,
)
from chiplog.composition.r15_scheduler_authority import (
    SchedulerFreshSources,
    SchedulerPublicationAuthority,
)
from chiplog.composition.r15_scheduler_history import configuration_snapshot
from chiplog.composition.r15_scheduler_registry import (
    SchedulerConfigurationAdoption,
    _canonical,
    _digest,
    capture_generation_sources,
)
from chiplog.composition.r15_tick_clock_v1 import registered_clock_source
from chiplog.composition.r15_tick_contracts import TickClockObservation, TickDTO

if TYPE_CHECKING:
    from chiplog.composition.r7_planning import ObservedTrustCall
    from chiplog.composition.r15_scheduler_runtime import R15SchedulerRuntime

DOMAIN = b"chiplog.r15.interval-service.decide.v3\x00"


class IntervalExecutionAuthority(TickDTO):
    schema_id: Literal["chiplog.r15.interval-execution-authority.v3"] = (
        "chiplog.r15.interval-execution-authority.v3"
    )
    principal_id: Literal["r15-hermetic-execution-principal-v1"] = (
        "r15-hermetic-execution-principal-v1"
    )
    service_identity: Literal["r15-hermetic-scheduler-execution-v1"] = (
        "r15-hermetic-scheduler-execution-v1"
    )
    session_id: Literal["r15-hermetic-interval-session-v1"] = "r15-hermetic-interval-session-v1"
    public_key: bytes = Field(min_length=32, max_length=32)

    def record(self, policy: Present) -> dict[str, object]:
        return {"authority": self.model_dump(mode="json"), "policy": policy.model_dump(mode="json")}


@dataclass(frozen=True)
class IntervalServiceSession:
    """Retained challenge. Possession alone confers no interval decision right."""

    observed: ObservedTrustCall
    service_identity: str
    session_id: str
    policy: Present
    broker_epoch: int
    generation: str
    authority: IntervalExecutionAuthority
    nonce: str
    expires_monotonic_ns: int

    def intent(self, schedule_id: str, act_id: str) -> dict[str, object]:
        return {
            "schema": "chiplog.r15.interval-decision-intent.v3",
            "operation": "scheduler.decide_interval",
            "tenant": "hermetic-tenant",
            "principal": self.authority.principal_id,
            "schedule_id": schedule_id,
            "act_id": act_id,
            "service": self.service_identity,
            "session": self.session_id,
            "policy": self.policy.model_dump(mode="json"),
            "authority": _digest(self.authority.canonical_bytes()),
        }

    def signed_message(self, schedule_id: str, act_id: str) -> bytes:
        return DOMAIN + _canonical(
            {
                "intent": self.intent(schedule_id, act_id),
                "challenge": {
                    "nonce": self.nonce,
                    "broker_epoch": self.broker_epoch,
                    "generation": self.generation,
                    "expires_monotonic_ns": self.expires_monotonic_ns,
                },
            }
        )


@dataclass(frozen=True)
class IntervalDecisionAuthority:
    mandate: SelectedIntervalMandateV3
    policy: Present
    execution_identity: IntervalExecutionAuthority
    signature: bytes
    signed_message: bytes


def verify_interval_signature(
    authority: IntervalExecutionAuthority, message: bytes, signature: bytes
) -> None:
    if not message.startswith(DOMAIN) or len(signature) != 64:
        raise LoopRejected("independent interval service signature required")
    try:
        _verify_ed25519_signature(authority.public_key, signature, message)
    except Exception as error:
        raise LoopRejected("independent interval service signature invalid") from error


async def open_interval_service_session(
    runtime: R15SchedulerRuntime, peer: str
) -> IntervalServiceSession:
    """Open a short private challenge after reading one selected admin policy."""

    from chiplog.composition.r15_interval_policy_publication import (
        selected_interval_authority,
        selected_interval_policy,
    )
    from chiplog.composition.r15_lease_publication import _authenticate_lease_admin

    observed = await _authenticate_lease_admin(runtime, peer)
    with runtime._authority_gate().hold():
        publisher = SchedulerPublicationAuthority(
            runtime,
            observed,
            SchedulerConfigurationAdoption(
                adoption_act_id="open-interval-session", command_bytes=b"open"
            ),
        )
        sources = publisher.capture_fresh()
        prepared, reference = selected_interval_policy(runtime)
        record = validate_historical_interval_policy(prepared)
        mandate = record.mandate
        configuration = configuration_snapshot(sources.startup, mandate.schedule.schedule_id)
        validate_mandate_configuration(mandate, configuration)
        now = runtime._tick_clock.observe()
        if now.unix_ns >= mandate.expires_at_unix_ns:
            raise LoopRejected("interval service mandate expired")
        agent = sources.generations.agent_session
        handle = IntervalServiceSession(
            observed=observed,
            service_identity=INTERVAL_SERVICE,
            session_id="r15-hermetic-interval-session-v1",
            policy=reference,
            broker_epoch=agent.broker_epoch,
            generation=agent.generation_id,
            authority=selected_interval_authority(runtime),
            nonce=secrets.token_hex(32),
            expires_monotonic_ns=time.monotonic_ns() + 30_000_000_000,
        )
        runtime._interval_service_sessions.append(handle)
        return handle


def _verify_session(
    runtime: R15SchedulerRuntime,
    session: IntervalServiceSession,
    schedule_id: str,
    act_id: str,
    signature: bytes,
) -> IntervalDecisionAuthority:
    from chiplog.composition.r15_interval_policy_publication import (
        selected_interval_authority,
        selected_interval_policy,
    )

    if (
        not isinstance(session, IntervalServiceSession)
        or not any(item is session for item in runtime._interval_service_sessions)
        or time.monotonic_ns() >= session.expires_monotonic_ns
        or session.service_identity != INTERVAL_SERVICE
        or session.session_id != "r15-hermetic-interval-session-v1"
    ):
        raise LoopRejected("unknown or expired interval service challenge")
    generation = capture_generation_sources(runtime, session.observed).agent_session
    if (session.broker_epoch, session.generation) != (
        generation.broker_epoch,
        generation.generation_id,
    ):
        raise LoopRejected("interval service generation changed")
    prepared, reference = selected_interval_policy(runtime)
    if session.policy != reference or selected_interval_authority(runtime) != session.authority:
        raise LoopRejected("interval service selected authority changed")
    message = session.signed_message(schedule_id, act_id)
    verify_interval_signature(session.authority, message, signature)
    record = validate_historical_interval_policy(prepared)
    if record.mandate.schedule.schedule_id != schedule_id:
        raise LoopRejected("interval service schedule differs from mandate")
    return IntervalDecisionAuthority(
        mandate=SelectedIntervalMandateV3(
            mandate_head=record.mandate_head,
            mandate=record.mandate,
        ),
        policy=reference,
        execution_identity=session.authority,
        signature=signature,
        signed_message=message,
    )


def verify_interval_service_replay(
    runtime: R15SchedulerRuntime,
    session: IntervalServiceSession,
    schedule_id: str,
    act_id: str,
    signature: bytes,
) -> IntervalDecisionAuthority:
    """Reauthenticate selected original without substituting current config or clock."""

    return _verify_session(runtime, session, schedule_id, act_id, signature)


def verify_interval_service_request(
    runtime: R15SchedulerRuntime,
    session: IntervalServiceSession,
    schedule_id: str,
    act_id: str,
    signature: bytes,
    sources: SchedulerFreshSources,
    clock: TickClockObservation,
) -> IntervalDecisionAuthority:
    result = _verify_session(runtime, session, schedule_id, act_id, signature)
    mandate = result.mandate.mandate
    from chiplog.composition.r15_interval_service import (
        SERVICE_ISSUANCE_SCHEMA,
        validate_service_interval_batch,
    )

    selected_cycles = 0
    for historical in sources.startup.cut.historical_requests:
        batch = historical.selection.prepared.request
        if (
            batch.operation == "scheduler.decide_interval"
            and getattr(batch.authentication, "applicability_schema", None)
            == SERVICE_ISSUANCE_SCHEMA
        ):
            evidence = validate_service_interval_batch(batch)
            if evidence.selected_policy == result.policy:
                selected_cycles += 1
    current = configuration_snapshot(sources.startup, schedule_id)
    validate_mandate_configuration(mandate, current)
    source = registered_clock_source(runtime._tick_clock)
    now = runtime._tick_clock.observe()
    if (
        clock.source != source
        or now.unix_ns < clock.reading.unix_ns
        or now.monotonic_ns < clock.reading.monotonic_ns
        or now.monotonic_ns >= clock.valid_until_monotonic_ns
        or now.unix_ns >= mandate.expires_at_unix_ns
        or clock.reading.unix_ns >= mandate.expires_at_unix_ns
        or clock.reading.unix_ns < mandate.first_due_ns
        or clock.reading.unix_ns > mandate.last_due_ns
        or selected_cycles >= mandate.max_cycles
    ):
        raise LoopRejected("interval service clock, cycle budget, or mandate horizon changed")
    return result
