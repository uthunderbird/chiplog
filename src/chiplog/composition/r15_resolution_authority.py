"""Private resolution issuance for one explicitly adopted active overflow hold."""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.scheduler_preparation import IntervalPreparationRequest
from chiplog.composition.r15_resolution_contracts import ResolutionPolicyAdoption
from chiplog.composition.r15_resolution_evidence import (
    resolution_id,
    validate_resolution_batch,
)
from chiplog.composition.r15_scheduler_authority import SchedulerPublicationAuthority
from chiplog.composition.r15_scheduler_registry import (
    _canonical,
    _digest,
    capture_generation_sources,
)
from chiplog.platform._owner_publication_contracts import (
    InvocationProofRef,
    OwnerCommandBytes,
    PublicationIdentity,
    SingleOwnerBatch,
)

if TYPE_CHECKING:
    from chiplog.composition.r7_planning import ObservedTrustCall
    from chiplog.composition.r15_scheduler_runtime import R15SchedulerRuntime


class ResolutionPublicationAuthority(SchedulerPublicationAuthority):
    def __init__(
        self,
        runtime: R15SchedulerRuntime,
        observed: ObservedTrustCall,
        adoption: ResolutionPolicyAdoption,
    ) -> None:
        self._runtime = runtime
        self._observed = observed
        self._resolution_adoption = adoption
        self._is_genesis = False
        self._operations = ("scheduler.resolve_interval",)
        self._command_id = resolution_id(adoption.adoption_act_id)
        self._invocations = {}
        self._sources = []
        self._prepared = []
        self._originals: list[tuple[PublicationIdentity, OwnerCommandBytes]] = []
        self._evidence_fingerprints: dict[str, str] = {}

    def retain_original(self, batch: SingleOwnerBatch) -> None:
        evidence = validate_resolution_batch(batch)
        if evidence.adoption != self._resolution_adoption:
            raise LoopRejected("resolution selection belongs to another adoption")
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
            or request.operation != "scheduler.resolve_interval"
        ):
            raise LoopRejected("resolution private request binding differs")
        self._originals.append((identity, command))
        self._evidence_fingerprints[command.fingerprint] = _digest(evidence_bytes)

    def issue_invocation(
        self, identity: PublicationIdentity, command: OwnerCommandBytes
    ) -> InvocationProofRef:
        if (identity, command) not in self._originals:
            raise LoopRejected("resolution invocation has no privately retained original")
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
        self._invocations[nonce] = (proof, identity, command, "scheduler.resolve_interval")
        return proof

    def _matches_original(self, request: SingleOwnerBatch) -> bool:
        return validate_resolution_batch(request).adoption == self._resolution_adoption
