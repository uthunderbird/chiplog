"""Private ACQUIRE preparation seam; assembly must independently authenticate sources.

No runtime mounts this issuer by default. The port is a trusted composition dependency,
not a caller callback or a public DTO authority. It must reproduce selected mandate,
revocation absence, exact root/mandate applicability, adopted administrative policy,
execution authentication and the complete physical/journal cut together. Publication,
source registration, deletion fencing and final atomic CAS remain writer duties.
"""

from __future__ import annotations

import hashlib
import secrets
from typing import Literal, Protocol

from pydantic import Field

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.recovery_contracts import (
    Absent,
    Identity,
    Present,
    TrustedClockProofRef,
)
from chiplog.capabilities.agent_loop.scheduler_contracts import (
    LeaseTransitionCommand,
    SchedulerCommandIdentity,
    SchedulerContextRef,
    SchedulerLineageView,
)
from chiplog.capabilities.agent_loop.scheduler_execution_contracts import (
    SelectedScheduledSystemMandateV2,
)
from chiplog.capabilities.agent_loop.scheduler_leases import (
    IssuedLeaseObservation,
    lease_fence_fingerprint,
    lease_payload_fingerprint,
)
from chiplog.capabilities.agent_loop.scheduler_preparation import (
    LeasePreparationRequest,
    LeasePreparationSnapshot,
    PreparedSchedulerRecord,
    prepare_lease,
)
from chiplog.composition.r15_tick_clock_v1 import registered_clock_source
from chiplog.composition.r15_tick_contracts import (
    TickClockPort,
    TickClockReading,
    TickClockSource,
    TickDTO,
    TickSourceCut,
)

MAX_LEASE_NS = 60_000_000_000
EXECUTION_SERVICE: Literal["r15-hermetic-scheduler-execution-v1"] = (
    "r15-hermetic-scheduler-execution-v1"
)


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class LeaseExecutionPolicy(TickDTO):
    schema_id: Literal["chiplog.scheduler.lease-execution-policy.v1"] = (
        "chiplog.scheduler.lease-execution-policy.v1"
    )
    administrator_principal: Literal["hermetic-principal"] = "hermetic-principal"
    service_identity: Literal["r15-hermetic-scheduler-execution-v1"] = EXECUTION_SERVICE
    adoption: Present
    clock_source: TickClockSource
    max_lease_ns: int = Field(gt=0, le=MAX_LEASE_NS)


class LeaseAcquireSource(TickDTO):
    """Inert selected/authenticated source; port authenticates its entire causal join."""

    context: SchedulerContextRef
    policy: LeaseExecutionPolicy
    mandate: SelectedScheduledSystemMandateV2
    revocation: Absent
    cut: TickSourceCut
    current: SchedulerLineageView
    registry: Present
    used_lease_ids: tuple[Identity, ...]
    authentication_frames: tuple[bytes, ...] = Field(min_length=1)
    selected_root_frames: tuple[bytes, ...] = Field(min_length=1)


class LeaseAcquireSourcePort(Protocol):
    def capture(self, peer: str, root_id: str) -> LeaseAcquireSource:
        """Authenticate execution caller and reproduce the independently selected join."""
        ...


class LeaseAcquireEvidence(TickDTO):
    schema_id: Literal["chiplog.scheduler.lease-acquire-issuance.v1"] = (
        "chiplog.scheduler.lease-acquire-issuance.v1"
    )
    source: LeaseAcquireSource
    reading: TickClockReading
    nonce: Identity
    valid_until_monotonic_ns: int = Field(ge=0)
    request: LeasePreparationRequest
    prepared: PreparedSchedulerRecord


def _validate_source(source: LeaseAcquireSource, reading: TickClockReading) -> None:
    scope = source.mandate.mandate.scope
    raw = source.mandate.mandate.canonical_bytes()
    if (
        raw != source.mandate.canonical_mandate_bytes
        or _digest(raw) != source.mandate.mandate_head.fingerprint
        or source.context.mandate_head != source.mandate.mandate_head.head
        or source.context.service_identity != EXECUTION_SERVICE
        or source.context.session_id == "hermetic-session"
        or scope.service_identity != EXECUTION_SERVICE
        or source.context.tenant_id != scope.tenant_id
        or source.cut.tenant_id != scope.tenant_id
        or "scheduler.execute_interval" not in scope.operations
        or reading.unix_ns >= scope.horizon.expires_at_unix_ns
        or source.current.lease.kind != "UNLEASED"
        or len(set(source.used_lease_ids)) != len(source.used_lease_ids)
    ):
        raise LoopRejected("lease source mandate, execution identity, freshness or genesis differs")


def _proof_fingerprint(
    source: LeaseAcquireSource,
    reading: TickClockReading,
    nonce: str,
    valid_until: int,
    proof: TrustedClockProofRef,
) -> str:
    return _digest(
        b"r15-lease-clock-proof-v1\0"
        + source.canonical_bytes()
        + reading.canonical_bytes()
        + nonce.encode()
        + b"\0"
        + str(valid_until).encode()
        + b"\0"
        + proof.model_copy(update={"proof_fingerprint": "0" * 64}).canonical_bytes()
    )


def validate_historical_acquire(evidence_bytes: bytes) -> LeaseAcquireEvidence:
    """Verify exact selected evidence bytes; journal provenance is required externally.

    This consumes original readings only, never current authorization or clock. The
    parent journal mapper must bind these bytes and prepared record to one selection.
    """
    evidence = LeaseAcquireEvidence.model_validate_json(evidence_bytes)
    if evidence.canonical_bytes() != evidence_bytes:
        raise LoopRejected("lease evidence is not canonical")
    source, request = evidence.source, evidence.request
    _validate_source(source, evidence.reading)
    command = LeaseTransitionCommand.model_validate_json(request.command_bytes)
    proof = command.clock_proof
    expected_snapshot = LeasePreparationSnapshot(
        tenant_id=source.cut.tenant_id,
        tenant_frontier=source.cut.tenant_frontier,
        materialization_commitment=source.cut.materialization_commitment,
        registry_head=source.registry.head,
        registry_fingerprint=source.registry.fingerprint,
        current=source.current,
        issued=IssuedLeaseObservation(
            proof=proof,
            now=evidence.reading.unix_ns,
            max_lease_duration=source.policy.max_lease_ns,
            holder_id=source.context.service_identity,
            holder_session_id=source.context.session_id,
            authority_epoch=source.cut.deletion_fence_generation,
            used_lease_ids=source.used_lease_ids,
        ),
        submission_id=evidence.nonce,
    )
    if (
        request.operation != "scheduler.acquire"
        or request.context != source.context
        or request.snapshot != expected_snapshot
        or proof.proof_id != "r15-lease-clock:" + evidence.nonce
        or proof.proof_version != "r15-lease-clock-proof-v1"
        or proof.clock_contract_version != source.policy.clock_source.contract_version
        or evidence.valid_until_monotonic_ns
        != evidence.reading.monotonic_ns + source.policy.max_lease_ns
        or proof.proof_fingerprint
        != _proof_fingerprint(
            source, evidence.reading, evidence.nonce, evidence.valid_until_monotonic_ns, proof
        )
        or command.proposed_expiry > source.mandate.mandate.scope.horizon.expires_at_unix_ns
        or prepare_lease(request) != evidence.prepared
    ):
        raise LoopRejected("lease historical proof, request or exact output differs")
    return evidence


class LeaseAcquireIssuer:
    """Assembly-private retained issuance; no method publishes or starts a worker."""

    def __init__(
        self, clock: TickClockPort, source_port: LeaseAcquireSourcePort | None = None
    ) -> None:
        self._clock = clock
        self._port = source_port
        self._retained: dict[str, tuple[str, str, bytes]] = {}

    def prepare(
        self, peer: str, root_id: str, command_id: str, duration_ns: int = MAX_LEASE_NS
    ) -> LeaseAcquireEvidence:
        if self._port is None:
            raise LoopRejected("lease acquire source integration is not mounted")
        if type(duration_ns) is not int or not 0 < duration_ns <= MAX_LEASE_NS:
            raise LoopRejected("lease duration exceeds sixty seconds")
        source = self._port.capture(peer, root_id)
        clock_source = registered_clock_source(self._clock)
        reading = self._clock.observe()
        _validate_source(source, reading)
        if (
            source.current.lineage.root_id != root_id
            or source.policy.clock_source != clock_source
            or duration_ns > source.policy.max_lease_ns
        ):
            raise LoopRejected("lease root, registered clock or duration differs")
        nonce = secrets.token_hex(32)
        lease_id = "r15-lease:" + secrets.token_hex(32)
        proof = TrustedClockProofRef(
            proof_id="r15-lease-clock:" + nonce,
            proof_fingerprint="0" * 64,
            proof_version="r15-lease-clock-proof-v1",
            clock_contract_version=clock_source.contract_version,
            fence_fingerprint=lease_fence_fingerprint(source.current),
            command_id=command_id,
            command_payload_fingerprint="0" * 64,
            submission_id=nonce,
        )
        command = LeaseTransitionCommand(
            identity=SchedulerCommandIdentity(
                command_id=command_id, schema_version="1", canonicalization_version="1"
            ),
            kind="ACQUIRE",
            lineage=source.current.lineage,
            physical_root=source.current.physical_root,
            observed_lease=source.current.lease,
            proposed_holder_id=source.context.service_identity,
            proposed_holder_session_id=source.context.session_id,
            proposed_lease_id=lease_id,
            proposed_expiry=reading.unix_ns + duration_ns,
            proposed_generation=1,
            clock_proof=proof,
        )
        valid_until = reading.monotonic_ns + source.policy.max_lease_ns
        proof = proof.model_copy(
            update={"command_payload_fingerprint": lease_payload_fingerprint(command)}
        )
        proof = proof.model_copy(
            update={
                "proof_fingerprint": _proof_fingerprint(source, reading, nonce, valid_until, proof)
            }
        )
        command = command.model_copy(update={"clock_proof": proof})
        request = LeasePreparationRequest(
            operation="scheduler.acquire",
            context=source.context,
            snapshot=LeasePreparationSnapshot(
                tenant_id=source.cut.tenant_id,
                tenant_frontier=source.cut.tenant_frontier,
                materialization_commitment=source.cut.materialization_commitment,
                registry_head=source.registry.head,
                registry_fingerprint=source.registry.fingerprint,
                current=source.current,
                issued=IssuedLeaseObservation(
                    proof=proof,
                    now=reading.unix_ns,
                    max_lease_duration=source.policy.max_lease_ns,
                    holder_id=source.context.service_identity,
                    holder_session_id=source.context.session_id,
                    authority_epoch=source.cut.deletion_fence_generation,
                    used_lease_ids=source.used_lease_ids,
                ),
                submission_id=nonce,
            ),
            command_bytes=command.canonical_bytes(),
        )
        evidence = LeaseAcquireEvidence(
            source=source,
            reading=reading,
            nonce=nonce,
            valid_until_monotonic_ns=valid_until,
            request=request,
            prepared=prepare_lease(request),
        )
        validate_historical_acquire(evidence.canonical_bytes())
        self._retained[nonce] = (peer, root_id, evidence.canonical_bytes())
        return evidence

    def recheck(self, evidence: LeaseAcquireEvidence) -> None:
        retained = self._retained.get(evidence.nonce)
        if self._port is None or retained is None or retained[2] != evidence.canonical_bytes():
            raise LoopRejected("lease issuance is not privately retained")
        source = self._port.capture(retained[0], retained[1])
        if (
            source != evidence.source
            or registered_clock_source(self._clock) != source.policy.clock_source
        ):
            raise LoopRejected("lease authenticated source cut or clock changed")
        now = self._clock.observe()
        command = LeaseTransitionCommand.model_validate_json(evidence.request.command_bytes)
        if (
            now.unix_ns < evidence.reading.unix_ns
            or now.monotonic_ns < evidence.reading.monotonic_ns
            or now.monotonic_ns >= evidence.valid_until_monotonic_ns
            or now.unix_ns >= command.proposed_expiry
        ):
            raise LoopRejected("lease clock regressed or expired")
