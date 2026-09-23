"""Private tick invocation ownership, reusing exact scheduler publication mechanics."""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, Literal

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.scheduler_preparation import IntervalPreparationRequest
from chiplog.composition.r15_scheduler_authority import SchedulerPublicationAuthority
from chiplog.composition.r15_scheduler_registry import (
    _canonical,
    _digest,
    capture_generation_sources,
)
from chiplog.composition.r15_tick_contracts import TickClockObservation, TickPolicyAdoption
from chiplog.composition.r15_tick_evidence import tick_id, validate_tick_batch
from chiplog.platform._owner_publication_contracts import (
    InvocationProofRef,
    OwnerCommandBytes,
    PublicationIdentity,
    SingleOwnerBatch,
)
from chiplog.platform.owner_publications import PreparedOwnerPublication

if TYPE_CHECKING:
    from chiplog.composition.r7_planning import ObservedTrustCall
    from chiplog.composition.r15_scheduler_runtime import R15SchedulerRuntime


class TickPublicationAuthority(SchedulerPublicationAuthority):
    def __init__(
        self,
        runtime: R15SchedulerRuntime,
        observed: ObservedTrustCall,
        adoption: TickPolicyAdoption,
    ) -> None:
        self._runtime = runtime
        self._observed = observed
        self._tick_adoption = adoption
        self._is_genesis = False
        self._operations = ("scheduler.decide_interval",)
        self._command_id = tick_id(adoption.adoption_act_id)
        self._invocations = {}
        self._sources = []
        self._prepared = []
        self._tick_clock = runtime._tick_clock
        self._clock_observation: TickClockObservation | None = None
        self._originals: list[tuple[PublicationIdentity, OwnerCommandBytes]] = []
        self._evidence_fingerprints: dict[str, str] = {}

    def retain_original(self, batch: SingleOwnerBatch) -> None:
        evidence = validate_tick_batch(batch)
        if evidence.adoption != self._tick_adoption:
            raise LoopRejected("tick selection belongs to another exact adoption")
        self._originals.append((batch.identity, batch.command))
        self._evidence_fingerprints[batch.command.fingerprint] = _digest(evidence.canonical_bytes())

    def retain_request(
        self, identity: PublicationIdentity, command: OwnerCommandBytes, evidence_bytes: bytes
    ) -> None:
        request = IntervalPreparationRequest.model_validate_json(command.canonical_bytes)
        if (
            identity.command_id != self._command_id
            or identity.command_fingerprint != _digest(request.command_bytes)
            or command.fingerprint != _digest(command.canonical_bytes)
            or command.schema_id != "chiplog.scheduler.interval-preparation.v1"
            or request.operation != "scheduler.decide_interval"
        ):
            raise LoopRejected("tick private request binding differs")
        self._originals.append((identity, command))
        self._evidence_fingerprints[command.fingerprint] = _digest(evidence_bytes)

    def issue_invocation(
        self, identity: PublicationIdentity, command: OwnerCommandBytes
    ) -> InvocationProofRef:
        if (identity, command) not in self._originals:
            raise LoopRejected("tick invocation has no privately retained original")
        session = capture_generation_sources(self._runtime, self._observed).agent_session
        nonce = secrets.token_hex(32)
        proof = InvocationProofRef(
            issuance_id=nonce,
            issuance_fingerprint=_digest(
                _canonical(
                    {
                        "nonce": nonce,
                        "identity": identity.model_dump(mode="json"),
                        "command": command.model_dump(mode="json"),
                        "evidence": self._evidence_fingerprints[command.fingerprint],
                    }
                )
            ),
            broker_epoch=str(session.broker_epoch),
            broker_session=session.session_id,
            runtime_generation=session.generation_id,
            operation_subject=identity.command_id,
        )
        self._invocations[nonce] = (proof, identity, command, "scheduler.decide_interval")
        return proof

    def _matches_original(self, request: SingleOwnerBatch) -> bool:
        return validate_tick_batch(request).adoption == self._tick_adoption

    def check_clock(self) -> None:
        original = self._clock_observation
        if original is None:
            raise LoopRejected("tick clock observation not issued")
        if getattr(self._runtime, "_tick_clock", None) is not self._tick_clock:
            raise LoopRejected("tick clock instance changed")
        from chiplog.composition.r15_tick_clock_v1 import registered_clock_source

        if registered_clock_source(self._tick_clock) != original.source:
            raise LoopRejected("registered clock source changed")
        now = self._tick_clock.observe()
        if (
            now.unix_ns < original.reading.unix_ns
            or now.monotonic_ns < original.reading.monotonic_ns
            or now.monotonic_ns >= original.valid_until_monotonic_ns
        ):
            raise LoopRejected("tick clock regressed or expired")

    def check_prepared(
        self, prepared: PreparedOwnerPublication
    ) -> Literal["DENIED", "STALE", "INDETERMINATE"] | None:
        failure = super().check_prepared(prepared)
        if failure is not None:
            return failure
        try:
            self.check_clock()
        except OSError, RuntimeError, ValueError, TypeError:
            return "STALE"
        return None
