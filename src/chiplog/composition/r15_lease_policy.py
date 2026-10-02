"""Closed lease policy and separate explicit administrative adoption preparation.

Assembly must mount an independently authenticated source port and select the exact
prepared record through its owner journal. DTOs and Present references are inert;
these validators prove canonical joins, not source authentication or selection.
Mandate issuance adoption is a different administrative act and grants no lease right.
"""

from __future__ import annotations

import hashlib
from typing import Literal, Protocol

from pydantic import Field

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.recovery_contracts import Absent, Identity, Present
from chiplog.capabilities.agent_loop.scheduler_contracts import SchedulerContextRef
from chiplog.capabilities.agent_loop.scheduler_execution_contracts import (
    SelectedScheduledSystemMandateV2,
)
from chiplog.composition.r15_lease_acquire import (
    EXECUTION_SERVICE,
    MAX_LEASE_NS,
    LeaseExecutionPolicy,
)
from chiplog.composition.r15_tick_clock_v1 import registered_clock_source
from chiplog.composition.r15_tick_contracts import (
    TickClockPort,
    TickClockReading,
    TickClockSource,
    TickDTO,
    TickSourceCut,
)


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class LeasePolicyDocument(TickDTO):
    schema_id: Literal["chiplog.scheduler.lease-policy-document.v1"] = (
        "chiplog.scheduler.lease-policy-document.v1"
    )
    operation: Literal["scheduler.acquire"] = "scheduler.acquire"
    scope: Literal["ONE_SELECTED_MANDATE_ONE_EXECUTION_SESSION_NO_WORKER_PUBLICATION"] = (
        "ONE_SELECTED_MANDATE_ONE_EXECUTION_SESSION_NO_WORKER_PUBLICATION"
    )
    tenant_id: Literal["hermetic-tenant"] = "hermetic-tenant"
    administrator_principal: Literal["hermetic-principal"] = "hermetic-principal"
    service_identity: Literal["r15-hermetic-scheduler-execution-v1"] = EXECUTION_SERVICE
    execution_session_id: Identity
    selected_mandate: Present
    clock_source: TickClockSource
    max_lease_ns: int = Field(gt=0, le=MAX_LEASE_NS)


class LeasePolicyAdoptionRequest(TickDTO):
    schema_id: Literal["chiplog.scheduler.lease-policy-adoption-request.v1"] = (
        "chiplog.scheduler.lease-policy-adoption-request.v1"
    )
    adoption_act_id: Identity
    policy_bytes: bytes = Field(min_length=1)


class LeasePolicyAdminSource(TickDTO):
    """Port-reproduced observations, not caller authority.

    Port must bind current trust observation/frames to the authenticated administrator,
    the separately adopted administrative lease-policy authority, exact request and
    selected mandate/revocation absence in one authority-gated cut. Historical journal
    integration must verify the same joins from selected original trust/owner bytes.
    """

    principal_id: Identity
    admin_session_id: Identity
    administrative_operation: Literal["scheduler.adopt_lease_execution_policy"] = (
        "scheduler.adopt_lease_execution_policy"
    )
    administrative_policy: Present
    authority_head: Present
    trust_observation_bytes: bytes = Field(min_length=1)
    trust_request_frame: bytes = Field(min_length=1)
    trust_response_frame: bytes = Field(min_length=1)
    request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    mandate: SelectedScheduledSystemMandateV2
    revocation: Absent
    cut: TickSourceCut


class LeasePolicyAdminPort(Protocol):
    def capture(self, peer: str, request: LeasePolicyAdoptionRequest) -> LeasePolicyAdminSource:
        """Independently authenticate admin and explicit administrative applicability."""
        ...


class LeasePolicyAdoptionRecord(TickDTO):
    schema_id: Literal["chiplog.scheduler.lease-policy-adoption-record.v1"] = (
        "chiplog.scheduler.lease-policy-adoption-record.v1"
    )
    request: LeasePolicyAdoptionRequest
    policy: LeasePolicyDocument
    policy_head: Present
    source: LeasePolicyAdminSource
    reading: TickClockReading
    valid_until_monotonic_ns: int = Field(ge=0)


class PreparedLeasePolicyAdoption(TickDTO):
    """Unselected exact record; publication and provenance remain broker responsibilities."""

    schema_id: Literal["chiplog.scheduler.prepared-lease-policy-adoption.v1"] = (
        "chiplog.scheduler.prepared-lease-policy-adoption.v1"
    )
    command_id: Identity
    record_id: Identity
    canonical_record_bytes: bytes = Field(min_length=1)
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


def _policy(request: LeasePolicyAdoptionRequest) -> LeasePolicyDocument:
    policy = LeasePolicyDocument.model_validate_json(request.policy_bytes)
    if policy.canonical_bytes() != request.policy_bytes:
        raise LoopRejected("lease policy bytes are not canonical")
    if policy.execution_session_id == "hermetic-session":
        raise LoopRejected("lease execution session must be distinct from administrator/tick")
    return policy


def policy_head(policy: LeasePolicyDocument) -> Present:
    fingerprint = _digest(policy.canonical_bytes())
    return Present(head="r15-lease-policy-v1:" + fingerprint, fingerprint=fingerprint)


def _validate_record(record: LeasePolicyAdoptionRecord) -> None:
    policy, source = record.policy, record.source
    mandate = source.mandate
    raw = mandate.mandate.canonical_bytes()
    scope = mandate.mandate.scope
    if (
        _policy(record.request) != policy
        or record.policy_head != policy_head(policy)
        or source.principal_id != policy.administrator_principal
        or source.admin_session_id != "hermetic-session"
        or source.admin_session_id == policy.execution_session_id
        or source.request_fingerprint != _digest(record.request.canonical_bytes())
        or raw != mandate.canonical_mandate_bytes
        or _digest(raw) != mandate.mandate_head.fingerprint
        or policy.selected_mandate != mandate.mandate_head
        or policy.tenant_id != source.cut.tenant_id
        or policy.tenant_id != scope.tenant_id
        or policy.service_identity != scope.service_identity
        or "scheduler.execute_interval" not in scope.operations
        or record.reading.unix_ns >= scope.horizon.expires_at_unix_ns
        or record.valid_until_monotonic_ns != record.reading.monotonic_ns + policy.max_lease_ns
    ):
        raise LoopRejected("lease policy administrative source or selected mandate join differs")


def _prepare_record(record: LeasePolicyAdoptionRecord) -> PreparedLeasePolicyAdoption:
    _validate_record(record)
    raw = record.canonical_bytes()
    fingerprint = _digest(raw)
    return PreparedLeasePolicyAdoption(
        command_id="r15-lease-policy-adopt:" + record.request.adoption_act_id,
        record_id="r15-lease-policy-adoption-v1:" + fingerprint,
        canonical_record_bytes=raw,
        fingerprint=fingerprint,
    )


def validate_historical_lease_policy(
    prepared: PreparedLeasePolicyAdoption,
) -> LeasePolicyAdoptionRecord:
    """Reproduce original exact record; selected trust/journal provenance is external."""
    record = LeasePolicyAdoptionRecord.model_validate_json(prepared.canonical_record_bytes)
    if (
        record.canonical_bytes() != prepared.canonical_record_bytes
        or _prepare_record(record) != prepared
    ):
        raise LoopRejected("lease policy selected original record identity or bytes differ")
    return record


def policy_for_execution(
    prepared: PreparedLeasePolicyAdoption,
    selected_reference: Present,
    context: SchedulerContextRef,
    mandate: SelectedScheduledSystemMandateV2,
) -> LeaseExecutionPolicy:
    """Project independently journal-selected adoption for this exact execution caller.

    Parent must derive selected_reference from its authenticated journal and verify
    trust provenance; callers cannot grant authority by constructing this reference.
    No current clock/auth is substituted while interpreting original selected bytes.
    """
    record = validate_historical_lease_policy(prepared)
    policy = record.policy
    reference = Present(head=prepared.record_id, fingerprint=prepared.fingerprint)
    if (
        selected_reference != reference
        or context.tenant_id != policy.tenant_id
        or context.service_identity != policy.service_identity
        or context.session_id != policy.execution_session_id
        or context.mandate_head != policy.selected_mandate.head
        or mandate != record.source.mandate
    ):
        raise LoopRejected("lease policy selected adoption or execution applicability differs")
    return LeaseExecutionPolicy(
        adoption=reference, clock_source=policy.clock_source, max_lease_ns=policy.max_lease_ns
    )


class LeasePolicyAdoptionIssuer:
    """Assembly-private preparation, never a selected adoption receipt."""

    def __init__(
        self, clock: TickClockPort, source_port: LeasePolicyAdminPort | None = None
    ) -> None:
        self._clock = clock
        self._port = source_port
        self._retained: dict[str, tuple[str, PreparedLeasePolicyAdoption]] = {}

    def prepare(
        self, peer: str, request: LeasePolicyAdoptionRequest
    ) -> PreparedLeasePolicyAdoption:
        if self._port is None:
            raise LoopRejected("lease policy administrative source integration is not mounted")
        policy = _policy(request)
        if registered_clock_source(self._clock) != policy.clock_source:
            raise LoopRejected("lease policy registered clock source differs")
        source = self._port.capture(peer, request)
        reading = self._clock.observe()
        record = LeasePolicyAdoptionRecord(
            request=request,
            policy=policy,
            policy_head=policy_head(policy),
            source=source,
            reading=reading,
            valid_until_monotonic_ns=reading.monotonic_ns + policy.max_lease_ns,
        )
        prepared = _prepare_record(record)
        self._retained[prepared.record_id] = (peer, prepared)
        return prepared

    def recheck(self, prepared: PreparedLeasePolicyAdoption) -> None:
        retained = self._retained.get(prepared.record_id)
        if self._port is None or retained is None or retained[1] != prepared:
            raise LoopRejected("lease policy adoption was not privately retained")
        record = validate_historical_lease_policy(prepared)
        if (
            self._port.capture(retained[0], record.request) != record.source
            or registered_clock_source(self._clock) != record.policy.clock_source
        ):
            raise LoopRejected("lease policy authenticated source cut or clock changed")
        now = self._clock.observe()
        if (
            now.unix_ns < record.reading.unix_ns
            or now.monotonic_ns < record.reading.monotonic_ns
            or now.monotonic_ns >= record.valid_until_monotonic_ns
            or now.unix_ns >= record.source.mandate.mandate.scope.horizon.expires_at_unix_ns
        ):
            raise LoopRejected("lease policy clock regressed, expired or mandate expired")
