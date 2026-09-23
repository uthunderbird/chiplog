"""Canonical R7 lifecycle: trust admission, fresh epoch, reconciliation, generation."""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from base64 import b64decode
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from chiplog.adapters.driven.deployment_trust import IndependentTenantDecisionJournal
from chiplog.architecture.r7_runtime import RuntimeAssemblyManifest
from chiplog.composition.r7 import RuntimeGraphGeneration
from chiplog.platform.authority_gate import AuthorityGate
from chiplog.platform.authority_ledger import BrokerAuthorityLedger
from chiplog.platform.broker import (
    BrokerSession,
    CallBudget,
    PublicPortCall,
    PublicPortRejected,
    PublicPortResult,
)
from chiplog.platform.r7_runtime import AuthorityBrokerRuntime, OwnerProcessAttestation
from chiplog.platform.r7_trust import TrustOwnerCall, TrustOwnerResult, encode_trust_journal
from chiplog.platform.r7_trust_durability import BrokerTrustDurability, FrozenTrustObservation
from chiplog.platform.read_ledger import BrokerReadLedger


@dataclass(frozen=True)
class RuntimeAdmissionEvidence:
    tenant_id: str
    database_instance_id: str
    genesis_head: str
    trust_head: str
    journal_head: str
    materialization_head: str
    accepted: bool
    observation: FrozenTrustObservation | None = None
    request: PublicPortCall | None = None
    response: PublicPortResult | None = None


class RuntimeAdmissionPort(Protocol):
    def authenticate_runtime_admission(
        self, tenant_id: str, runtime: AuthorityBrokerRuntime
    ) -> RuntimeAdmissionEvidence: ...


class R4RuntimeAdmission:
    """Broker-local adapter from durable R4 trust to the R7 startup gate."""

    def __init__(
        self, trust: BrokerTrustDurability, journal: IndependentTenantDecisionJournal
    ) -> None:
        self._trust = trust
        self._journal = journal

    def authenticate_runtime_admission(
        self, tenant_id: str, runtime: AuthorityBrokerRuntime
    ) -> RuntimeAdmissionEvidence:
        callee = runtime.session("deployment_trust")
        gate = self._trust.authority_gate
        observation = self._trust.capture_verified_observation() if gate is not None else None
        owner_call = TrustOwnerCall(
            mode="RUNTIME_ADMISSION",
            snapshot_bytes=(
                observation.snapshot_bytes
                if observation is not None
                else encode_trust_journal(self._trust.owner_snapshot_entries())
            ),
            request_bytes=json.dumps(
                {"tenant_id": tenant_id}, sort_keys=True, separators=(",", ":")
            ).encode(),
        )
        request = PublicPortCall(
            operation_id="deployment_trust.runtime_admission",
            request_id=f"admission:{secrets.token_hex(8)}",
            caller=BrokerSession(
                tenant_id=tenant_id,
                broker_epoch=callee.broker_epoch,
                generation_id=callee.generation_id,
                owner_id="broker",
                session_id=f"broker:{callee.generation_id}",
            ),
            callee=callee,
            schema_id="chiplog.deployment-trust.owner-call.v1",
            canonical_payload=owner_call.canonical_bytes(),
            budget=CallBudget(
                remaining_calls=1,
                remaining_depth=1,
                absolute_deadline_ns=time.monotonic_ns() + 5_000_000_000,
                policy_version=1,
            ),
        )
        response = runtime.call_sync(request)
        denied = RuntimeAdmissionEvidence(tenant_id, "", "", "", "", "", False)
        if response.request_id != request.request_id or response.responder != request.callee:
            raise ValueError("runtime admission owner response identity mismatch")
        if isinstance(response, PublicPortRejected):
            return denied
        if response.schema_id != "chiplog.deployment-trust.owner-result.v1":
            raise ValueError("runtime admission owner response schema mismatch")
        values = json.loads(response.canonical_payload)
        if values["reference_bytes"] is not None:
            values["reference_bytes"] = b64decode(values["reference_bytes"], validate=True)
        owner_result = TrustOwnerResult.model_validate(values)
        if owner_result.canonical_bytes() != response.canonical_payload:
            raise ValueError("runtime admission owner result is not canonical")
        if owner_result.disposition != "VALID":
            return denied
        with nullcontext() if gate is None else gate.hold():
            if (
                observation is not None
                and self._trust.capture_verified_observation() != observation
            ):
                return denied
            if time.monotonic_ns() >= request.budget.absolute_deadline_ns:
                return denied
            state = self._trust.verify()
            if state is None:
                return denied
            return RuntimeAdmissionEvidence(
                tenant_id=state.tenant_id,
                database_instance_id=state.database_instance_id,
                genesis_head=state.genesis_head,
                trust_head=state.trust_head,
                journal_head=state.materialization_head,
                materialization_head=state.materialization_head,
                accepted=state.tenant_id == tenant_id and state.phase == "ACTIVE",
                observation=observation,
                request=request,
                response=response,
            )


def _verify_realized_graph_exact(
    manifest: RuntimeAssemblyManifest,
    graph: RuntimeGraphGeneration,
    generation_id: str,
    tenant_id: str,
    broker_epoch: int,
) -> None:
    expected_routes = tuple(
        (
            route.operation_id,
            route.caller_owner_id,
            route.callee_owner_id,
            route.request_schema_id,
            route.result_schema_id,
        )
        for route in manifest.routes
    )
    expected_owners = tuple((owner.owner_id, owner.capability_ids) for owner in manifest.owners)
    realized_owners = tuple((owner.owner_id, owner.capability_ids) for owner in graph.owners)
    owner_details_match = len(graph.owners) == len(manifest.owners) and all(
        realized.provider_ids == declared.provider_ids
        and realized.target_ids == declared.target_ids
        and realized.factory_ids == declared.factory_ids
        and realized.scopes == declared.scopes
        and realized.generation_id == generation_id
        and realized.session_id == f"{generation_id}:{declared.owner_id}:{index}"
        and realized.process_identity.isascii()
        and realized.process_identity.isdecimal()
        and int(realized.process_identity) > 0
        for index, (realized, declared) in enumerate(
            zip(graph.owners, manifest.owners, strict=True), start=1
        )
    )
    expected_leaves = tuple(
        (leaf.leaf_id, leaf.owner_id, leaf.implementation) for leaf in manifest.leaves
    )
    if (
        graph.tenant_id != tenant_id
        or graph.generation_id != generation_id
        or graph.broker_epoch != broker_epoch
        or graph.manifest_digest != manifest.fingerprint()
        or graph.routes != expected_routes
        or realized_owners != expected_owners
        or not owner_details_match
        or len({owner.process_identity for owner in graph.owners}) != len(graph.owners)
        or graph.leaves != expected_leaves
        or graph.broker_capabilities != manifest.broker_raw_capabilities
        or graph.application_loop_id != manifest.application_loop_id
    ):
        raise RuntimeError("realized runtime graph differs from admitted manifest")


class R7RuntimeSupervisor:
    def __init__(
        self,
        tenant_id: str,
        ledger_path: Path,
        manifest: RuntimeAssemblyManifest,
        admission: RuntimeAdmissionPort,
        realized_leaves: dict[str, object] | None = None,
        *,
        authority_gate: AuthorityGate | None = None,
        read_ledger: BrokerReadLedger | None = None,
        trust: BrokerTrustDurability | None = None,
    ) -> None:
        if authority_gate is None:
            if read_ledger is not None or trust is not None:
                raise ValueError("partial authority lifecycle gate binding")
        elif (
            read_ledger is None
            or trust is None
            or read_ledger.authority_gate != authority_gate
            or trust.authority_gate != authority_gate
        ):
            raise ValueError("authority lifecycle gate binding mismatch")
        self._authority_gate = authority_gate
        self._read_ledger = read_ledger
        self._trust = trust
        self._lifecycle_lock = threading.RLock()
        self._transition = 0
        self._owned_epoch: int | None = None
        self._retiring: list[AuthorityBrokerRuntime] = []
        self._tenant_id = tenant_id
        self._ledger = BrokerAuthorityLedger(ledger_path, authority_gate=authority_gate)
        self._manifest = manifest
        self._admission = admission
        self._realized_leaves = realized_leaves
        self._runtime: AuthorityBrokerRuntime | None = None
        self._quarantine_runtime: AuthorityBrokerRuntime | None = None
        self._evidence: RuntimeAdmissionEvidence | None = None
        self._reconciled: tuple[str, ...] = ()
        self._graph: RuntimeGraphGeneration | None = None

    def _authority_scope(self) -> AbstractContextManager[None]:
        return nullcontext() if self._authority_gate is None else self._authority_gate.hold()

    @property
    def broker_epoch(self) -> int | None:
        return self._ledger.current_epoch(self._tenant_id)

    @property
    def reconciled_operations(self) -> tuple[str, ...]:
        return self._reconciled

    @property
    def admission_evidence(self) -> RuntimeAdmissionEvidence | None:
        with self._authority_scope():
            if self._runtime is None or not self._is_current(self._runtime):
                return None
            return self._evidence

    @property
    def authority_ledger(self) -> BrokerAuthorityLedger:
        """Expose broker-owned durable admission state to the broker composition only."""
        return self._ledger

    @property
    def admitted_graph(self) -> RuntimeGraphGeneration | None:
        with self._authority_scope():
            if (
                self._runtime is None
                or self._evidence is None
                or not self._is_current(self._runtime)
            ):
                return None
            return self._graph

    def _is_current(self, runtime: AuthorityBrokerRuntime) -> bool:
        return runtime.session("deployment_trust").broker_epoch == self._ledger.current_epoch(
            self._tenant_id
        )

    def _drain_read_state(self, *, predecessor: bool) -> None:
        if self._read_ledger is None:
            return
        state = self._read_ledger.current_state_or_none(self._tenant_id)
        if state is None or state.owner_draining:
            return
        if not predecessor and (
            state.broker_epoch != self._owned_epoch
            or self._ledger.current_epoch(self._tenant_id) != self._owned_epoch
        ):
            return
        self._read_ledger.start_owner_drain(self._tenant_id, state.fingerprint())

    def _detach(self) -> None:
        for runtime in (self._runtime, self._quarantine_runtime):
            if runtime is not None and runtime not in self._retiring:
                self._retiring.append(runtime)
        self._runtime = None
        self._quarantine_runtime = None
        self._graph = None
        self._evidence = None

    def _close_retiring(self) -> None:
        # Called only outside the authority gate. Failed drains remain reachable
        # for a later cleanup attempt; they are never eligible for new work.
        errors: list[BaseException] = []
        for runtime in tuple(self._retiring):
            try:
                runtime.close()
            except BaseException as error:
                errors.append(error)
            else:
                self._retiring.remove(runtime)
        if errors:
            raise BaseExceptionGroup("runtime process cleanup failed", errors)

    def _check_transition(self, transition: int, epoch: int) -> None:
        if (
            transition != self._transition
            or epoch != self._owned_epoch
            or epoch != self._ledger.current_epoch(self._tenant_id)
        ):
            raise PermissionError("runtime startup was superseded by another epoch or transition")

    def _check_evidence(
        self, evidence: RuntimeAdmissionEvidence, runtime: AuthorityBrokerRuntime
    ) -> None:
        if not evidence.accepted or evidence.tenant_id != self._tenant_id:
            raise PermissionError("runtime admission evidence is not authenticated and current")
        if self._authority_gate is not None:
            assert self._trust is not None
            if (
                evidence.observation is None
                or evidence.request is None
                or evidence.response is None
                or evidence.request.callee != runtime.session("deployment_trust")
                or time.monotonic_ns() >= evidence.request.budget.absolute_deadline_ns
                or self._trust.capture_verified_observation() != evidence.observation
            ):
                raise PermissionError("runtime admission evidence is not authenticated and current")

    def start_generation(self, generation_id: str) -> tuple[OwnerProcessAttestation, ...]:
        with self._lifecycle_lock:
            runtime: AuthorityBrokerRuntime | None = None
            try:
                runtime, attestations, graph, transition = self._start_quarantine_runtime(
                    generation_id
                )
                evidence = self._admission.authenticate_runtime_admission(self._tenant_id, runtime)
                with self._authority_scope():
                    self._check_transition(transition, graph.broker_epoch)
                    self._check_evidence(evidence, runtime)
                    self._runtime = runtime
                    self._graph = graph
                    self._evidence = evidence
                return attestations
            except BaseException:
                if runtime is not None and runtime not in self._retiring:
                    self._retiring.append(runtime)
                self._close_retiring()
                raise

    def start_quarantine(self, generation_id: str) -> tuple[OwnerProcessAttestation, ...]:
        with self._lifecycle_lock:
            runtime: AuthorityBrokerRuntime | None = None
            try:
                runtime, attestations, graph, transition = self._start_quarantine_runtime(
                    generation_id
                )
                with self._authority_scope():
                    self._check_transition(transition, graph.broker_epoch)
                    self._quarantine_runtime = runtime
                return attestations
            except BaseException:
                if runtime is not None and runtime not in self._retiring:
                    self._retiring.append(runtime)
                self._close_retiring()
                raise

    async def call_quarantined_trust(self, call: PublicPortCall) -> PublicPortResult:
        if not call.operation_id.startswith("deployment_trust."):
            raise PermissionError("quarantine exposes only the deployment-trust owner")
        with self._authority_scope():
            runtime = self._current_quarantine()
        return await runtime.call(call)

    def _current_quarantine(self) -> AuthorityBrokerRuntime:
        runtime = self._quarantine_runtime
        if runtime is None or not self._is_current(runtime):
            raise RuntimeError("trust quarantine is not running or its epoch is stale")
        return runtime

    def quarantined_trust_session(self) -> BrokerSession:
        with self._authority_scope():
            return self._current_quarantine().session("deployment_trust")

    def _start_quarantine_runtime(
        self, generation_id: str
    ) -> tuple[
        AuthorityBrokerRuntime,
        tuple[OwnerProcessAttestation, ...],
        RuntimeGraphGeneration,
        int,
    ]:
        endpoint_id = f"broker-endpoint:{secrets.token_hex(16)}"
        session_secret = secrets.token_bytes(32)
        key_digest = hashlib.sha256(session_secret).hexdigest()
        with self._authority_scope():
            self._transition += 1
            transition = self._transition
            self._drain_read_state(predecessor=True)
            self._detach()
            epoch = self._ledger.allocate_epoch(self._tenant_id, endpoint_id, key_digest)
            self._owned_epoch = epoch
            self._reconciled = self._ledger.reconcile_pending(self._tenant_id, epoch)
        self._close_retiring()
        runtime = AuthorityBrokerRuntime(
            self._tenant_id,
            epoch,
            generation_id,
            self._manifest,
            session_secret,
            self._realized_leaves,
        )
        try:
            runtime.start()
            graph = runtime.graph_generation()
            _verify_realized_graph_exact(
                self._manifest, graph, generation_id, self._tenant_id, epoch
            )
            attestations = runtime.attest()
        except BaseException:
            self._retiring.append(runtime)
            raise
        return runtime, attestations, graph, transition

    def runtime(self) -> AuthorityBrokerRuntime:
        with self._authority_scope():
            if (
                self._runtime is None
                or self._evidence is None
                or not self._is_current(self._runtime)
            ):
                raise RuntimeError("R7 runtime generation is not admitted or its epoch is stale")
            return self._runtime

    def close(self) -> None:
        with self._lifecycle_lock:
            with self._authority_scope():
                self._transition += 1
                self._drain_read_state(predecessor=False)
                self._detach()
            self._close_retiring()


__all__ = [
    "R4RuntimeAdmission",
    "R7RuntimeSupervisor",
    "RuntimeAdmissionEvidence",
    "RuntimeAdmissionPort",
]
