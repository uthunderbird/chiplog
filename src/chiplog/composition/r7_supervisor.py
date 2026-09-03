"""Canonical R7 lifecycle: trust admission, fresh epoch, reconciliation, generation."""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from base64 import b64decode
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from chiplog.adapters.driven.deployment_trust import IndependentTenantDecisionJournal
from chiplog.architecture.r7_runtime import RuntimeAssemblyManifest
from chiplog.composition.r7 import RuntimeGraphGeneration
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
from chiplog.platform.r7_trust_durability import BrokerTrustDurability


@dataclass(frozen=True)
class RuntimeAdmissionEvidence:
    tenant_id: str
    database_instance_id: str
    genesis_head: str
    trust_head: str
    journal_head: str
    materialization_head: str
    accepted: bool


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
        owner_call = TrustOwnerCall(
            mode="RUNTIME_ADMISSION",
            snapshot_bytes=encode_trust_journal(self._trust.owner_snapshot_entries()),
            request_bytes=json.dumps(
                {"tenant_id": tenant_id}, sort_keys=True, separators=(",", ":")
            ).encode(),
        )
        response = runtime.call_sync(
            PublicPortCall(
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
        )
        if isinstance(response, PublicPortRejected):
            return RuntimeAdmissionEvidence(tenant_id, "", "", "", "", "", False)
        values = json.loads(response.canonical_payload)
        if values["reference_bytes"] is not None:
            values["reference_bytes"] = b64decode(values["reference_bytes"])
        owner_result = TrustOwnerResult.model_validate(values)
        if owner_result.disposition != "VALID":
            return RuntimeAdmissionEvidence(tenant_id, "", "", "", "", "", False)
        state = self._trust.verify()
        if state is None:
            return RuntimeAdmissionEvidence(tenant_id, "", "", "", "", "", False)
        return RuntimeAdmissionEvidence(
            tenant_id=state.tenant_id,
            database_instance_id=state.database_instance_id,
            genesis_head=state.genesis_head,
            trust_head=state.trust_head,
            journal_head=state.materialization_head,
            materialization_head=state.materialization_head,
            accepted=state.tenant_id == tenant_id and state.phase == "ACTIVE",
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
    ) -> None:
        self._tenant_id = tenant_id
        self._ledger = BrokerAuthorityLedger(ledger_path)
        self._manifest = manifest
        self._admission = admission
        self._realized_leaves = realized_leaves
        self._runtime: AuthorityBrokerRuntime | None = None
        self._quarantine_runtime: AuthorityBrokerRuntime | None = None
        self._evidence: RuntimeAdmissionEvidence | None = None
        self._reconciled: tuple[str, ...] = ()
        self._graph: RuntimeGraphGeneration | None = None

    @property
    def broker_epoch(self) -> int | None:
        return self._ledger.current_epoch(self._tenant_id)

    @property
    def reconciled_operations(self) -> tuple[str, ...]:
        return self._reconciled

    @property
    def admission_evidence(self) -> RuntimeAdmissionEvidence | None:
        return self._evidence

    @property
    def authority_ledger(self) -> BrokerAuthorityLedger:
        """Expose broker-owned durable admission state to the broker composition only."""
        return self._ledger

    @property
    def admitted_graph(self) -> RuntimeGraphGeneration | None:
        if self._evidence is None:
            return None
        return self._graph

    def start_generation(self, generation_id: str) -> tuple[OwnerProcessAttestation, ...]:
        runtime, attestations, graph = self._start_quarantine_runtime(generation_id)
        evidence = self._admission.authenticate_runtime_admission(self._tenant_id, runtime)
        if not evidence.accepted or evidence.tenant_id != self._tenant_id:
            runtime.close()
            self._quarantine_runtime = None
            raise PermissionError("runtime admission evidence is not authenticated and current")
        self._quarantine_runtime = None
        self._runtime = runtime
        self._graph = graph
        self._evidence = evidence
        return attestations

    def start_quarantine(self, generation_id: str) -> tuple[OwnerProcessAttestation, ...]:
        runtime, attestations, _ = self._start_quarantine_runtime(generation_id)
        self._quarantine_runtime = runtime
        return attestations

    async def call_quarantined_trust(self, call: PublicPortCall) -> PublicPortResult:
        if not call.operation_id.startswith("deployment_trust."):
            raise PermissionError("quarantine exposes only the deployment-trust owner")
        if self._quarantine_runtime is None:
            raise RuntimeError("trust quarantine is not running")
        return await self._quarantine_runtime.call(call)

    def quarantined_trust_session(self) -> BrokerSession:
        if self._quarantine_runtime is None:
            raise RuntimeError("trust quarantine is not running")
        return self._quarantine_runtime.session("deployment_trust")

    def _start_quarantine_runtime(
        self, generation_id: str
    ) -> tuple[
        AuthorityBrokerRuntime,
        tuple[OwnerProcessAttestation, ...],
        RuntimeGraphGeneration,
    ]:
        endpoint_id = f"broker-endpoint:{secrets.token_hex(16)}"
        session_secret = secrets.token_bytes(32)
        key_digest = hashlib.sha256(session_secret).hexdigest()
        epoch = self._ledger.allocate_epoch(self._tenant_id, endpoint_id, key_digest)
        if self._quarantine_runtime is not None:
            self._quarantine_runtime.close()
            self._quarantine_runtime = None
        if self._runtime is not None:
            self._runtime.close()
            self._runtime = None
        self._reconciled = self._ledger.reconcile_pending(self._tenant_id, epoch)
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
            runtime.close()
            raise
        self._evidence = None
        return runtime, attestations, graph

    def runtime(self) -> AuthorityBrokerRuntime:
        if self._runtime is None or self._evidence is None:
            raise RuntimeError("R7 runtime generation is not admitted")
        return self._runtime

    def close(self) -> None:
        if self._runtime is not None:
            self._runtime.close()
            self._runtime = None
            self._graph = None
        if self._quarantine_runtime is not None:
            self._quarantine_runtime.close()
            self._quarantine_runtime = None


__all__ = [
    "R4RuntimeAdmission",
    "R7RuntimeSupervisor",
    "RuntimeAdmissionEvidence",
    "RuntimeAdmissionPort",
]
