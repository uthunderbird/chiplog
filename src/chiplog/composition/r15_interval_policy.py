"""Closed R15.1 interval-decision mandate; values alone grant no authority.

Only an independently selected administrative adoption and a fresh signed service
challenge can make this inert document applicable to a publication.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.agent_loop.scheduler_configuration import (
    ConfigurationSnapshot,
    policy_head,
    schedule_head,
)
from chiplog.capabilities.agent_loop.scheduler_contracts import (
    MissedOccurrencePolicyHead,
    ScheduleDefinitionHead,
    SchedulerIntervalBoundHead,
)
from chiplog.composition.r15_scheduler_registry import _canonical, _digest
from chiplog.composition.r15_tick_contracts import TickClockReading, TickDTO, TickSourceCut

INTERVAL_SERVICE = "r15-hermetic-scheduler-execution-v1"
MAX_INTERVAL_CYCLES = 1024
POLICY_SCHEMA = "chiplog.scheduler.interval-service-mandate.v3"


class IntervalDecisionMandateV3(TickDTO):
    """One schedule and bounded due range; no worker or external effect authority."""

    schema_id: Literal["chiplog.scheduler.interval-service-mandate.v3"] = (
        "chiplog.scheduler.interval-service-mandate.v3"
    )
    tenant_id: Literal["hermetic-tenant"] = "hermetic-tenant"
    beneficiary_principal: Literal["hermetic-principal"] = "hermetic-principal"
    service_identity: Literal["r15-hermetic-scheduler-execution-v1"] = (
        "r15-hermetic-scheduler-execution-v1"
    )
    schedule: ScheduleDefinitionHead
    missed_policy: MissedOccurrencePolicyHead
    bound: SchedulerIntervalBoundHead
    first_due_ns: int = Field(ge=0)
    last_due_ns: int = Field(ge=0)
    expires_at_unix_ns: int = Field(gt=0)
    max_cycles: int = Field(gt=0, le=MAX_INTERVAL_CYCLES)
    permitted_tools: tuple[()] = ()
    external_emission: Literal["NONE"] = "NONE"


class IntervalPolicyAdoptionRequest(TickDTO):
    adoption_act_id: str = Field(min_length=1)
    mandate_bytes: bytes = Field(min_length=1)


class SelectedIntervalMandateV3(TickDTO):
    mandate_head: Present
    mandate: IntervalDecisionMandateV3


class IntervalPolicyAdminSource(TickDTO):
    principal_id: str = Field(min_length=1)
    admin_session_id: str = Field(min_length=1)
    administrative_policy: Present
    authority_head: Present
    trust_observation_bytes: bytes = Field(min_length=1)
    trust_request_frame: bytes = Field(min_length=1)
    trust_response_frame: bytes = Field(min_length=1)
    request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    cut: TickSourceCut
    configuration: ConfigurationSnapshot
    selected_schedule: ScheduleDefinitionHead
    selected_missed_policy: MissedOccurrencePolicyHead
    selected_bound: SchedulerIntervalBoundHead


class IntervalPolicyAdoptionRecord(TickDTO):
    schema_id: Literal["chiplog.scheduler.interval-service-adoption.v3"] = (
        "chiplog.scheduler.interval-service-adoption.v3"
    )
    request: IntervalPolicyAdoptionRequest
    mandate: IntervalDecisionMandateV3
    mandate_head: Present
    source: IntervalPolicyAdminSource
    reading: TickClockReading
    valid_until_monotonic_ns: int = Field(ge=0)


class PreparedIntervalPolicyAdoption(TickDTO):
    schema_id: Literal["chiplog.scheduler.prepared-interval-service-adoption.v3"] = (
        "chiplog.scheduler.prepared-interval-service-adoption.v3"
    )
    command_id: str = Field(min_length=1)
    record_id: str = Field(min_length=1)
    canonical_record_bytes: bytes = Field(min_length=1)
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


def adopted_mandate(request: IntervalPolicyAdoptionRequest) -> IntervalDecisionMandateV3:
    mandate = IntervalDecisionMandateV3.model_validate_json(request.mandate_bytes)
    if mandate.canonical_bytes() != request.mandate_bytes:
        raise LoopRejected("interval service mandate adoption is not canonical")
    return mandate


def mandate_reference(mandate: IntervalDecisionMandateV3) -> Present:
    digest = _digest(mandate.canonical_bytes())
    return Present(head="r15-interval-service-mandate-v3:" + digest, fingerprint=digest)


def validate_mandate_configuration(
    mandate: IntervalDecisionMandateV3, configuration: ConfigurationSnapshot
) -> None:
    """Require exact selected configuration and an aligned finite due horizon."""

    schedule, policy, bound = (
        configuration.schedule,
        configuration.policy,
        configuration.bound,
    )
    if schedule is None or policy is None or bound is None:
        raise LoopRejected("interval service mandate requires complete schedule")
    definition = schedule.definition
    start = definition.start_ns
    period = definition.period_ns
    first, last = mandate.first_due_ns, mandate.last_due_ns
    if (
        mandate.schedule != schedule_head(schedule)
        or mandate.missed_policy != policy_head(policy)
        or mandate.bound != bound
        or first < start
        or first > last
        or (first - start) % period != 0
        or (last - start) % period != 0
        or (last - first) // period + 1 < mandate.max_cycles
        or (definition.end_exclusive_ns != "NO_END" and last >= definition.end_exclusive_ns)
    ):
        raise LoopRejected("interval service mandate differs from selected bounded schedule")


def selected_mandate(request: IntervalPolicyAdoptionRequest) -> SelectedIntervalMandateV3:
    mandate = adopted_mandate(request)
    return SelectedIntervalMandateV3(mandate_head=mandate_reference(mandate), mandate=mandate)


def administrative_policy_bytes() -> bytes:
    return _canonical(
        {
            "schema": "chiplog.scheduler.interval-service-administration.v3",
            "administrator": "hermetic-principal",
            "registered_ingress": "hermetic-ingress",
            "operation": "r15.adopt_interval_service_policy",
            "service_identity": INTERVAL_SERVICE,
            "mandate_schema": POLICY_SCHEMA,
            "scope": "ONE_SCHEDULE_BOUNDED_INTERVAL_DECISIONS_NO_SEND_NO_TOOLS",
            "max_cycles": MAX_INTERVAL_CYCLES,
        }
    )


def administrative_policy_head() -> Present:
    fingerprint = _digest(administrative_policy_bytes())
    return Present(
        head="r15-interval-service-admin-policy-v3:" + fingerprint,
        fingerprint=fingerprint,
    )


def prepare_interval_policy_adoption(
    record: IntervalPolicyAdoptionRecord,
) -> PreparedIntervalPolicyAdoption:
    """Pure complete record preparation; publication remains an independent action."""

    source = record.source
    if (
        adopted_mandate(record.request) != record.mandate
        or record.mandate_head != mandate_reference(record.mandate)
        or source.principal_id != record.mandate.beneficiary_principal
        or source.admin_session_id != "hermetic-session"
        or source.administrative_policy != administrative_policy_head()
        or source.request_fingerprint != _digest(record.request.canonical_bytes())
        or source.cut.tenant_id != record.mandate.tenant_id
        or source.selected_schedule != record.mandate.schedule
        or source.selected_missed_policy != record.mandate.missed_policy
        or source.selected_bound != record.mandate.bound
        or record.reading.unix_ns >= record.mandate.expires_at_unix_ns
        or record.valid_until_monotonic_ns <= record.reading.monotonic_ns
        or record.valid_until_monotonic_ns > record.reading.monotonic_ns + 30_000_000_000
    ):
        raise LoopRejected("interval service adoption source or horizon differs")
    validate_mandate_configuration(record.mandate, source.configuration)
    raw = record.canonical_bytes()
    fingerprint = _digest(raw)
    return PreparedIntervalPolicyAdoption(
        command_id="r15-interval-service-adopt:" + record.request.adoption_act_id,
        record_id="r15-interval-service-adoption-v3:" + fingerprint,
        canonical_record_bytes=raw,
        fingerprint=fingerprint,
    )


def validate_historical_interval_policy(
    prepared: PreparedIntervalPolicyAdoption,
) -> IntervalPolicyAdoptionRecord:
    record = IntervalPolicyAdoptionRecord.model_validate_json(prepared.canonical_record_bytes)
    if (
        record.canonical_bytes() != prepared.canonical_record_bytes
        or prepare_interval_policy_adoption(record) != prepared
    ):
        raise LoopRejected("selected interval service adoption record differs")
    return record
