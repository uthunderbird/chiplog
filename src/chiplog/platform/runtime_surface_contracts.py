"""Broker/verification wire for independently discovered executable surfaces.

Registration and discovery are separate observations. A self-consistent manifest
does not prove that a handler is mounted, fenced or included in discovery. No
object in this module authenticates input or grants a writer capability.
"""

from typing import Annotated, Literal, Protocol

from pydantic import Field

from ._ingress_contracts import Digest, Head, Identity, IngressDTO, SourceClass

WorkerApplicabilityKind = Literal[
    "NON_SCHEDULER_NOT_APPLICABLE",
    "EXECUTION_ROOT_LIVE_LEASE",
    "PRE_ROOT_SCHEDULER_DECISION",
    "POST_TERMINAL_RECOVERY_WORK",
    "PHYSICAL_ROOT_ROLLOVER",
    "WORK_EPOCH_ROLLOVER",
]


class ExecutableSymbol(IngressDTO):
    module: Identity
    qualified_name: Identity
    source_path: Identity
    source_fingerprint: Digest


class RuntimeRecordSchema(IngressDTO):
    owner: Identity
    record_kind: Identity
    schema_id: Identity
    schema_fingerprint: Digest


class WorkerAuthoritativeCommitRow(IngressDTO):
    row_id: Identity
    command_id: Identity
    request_schema: Identity
    request_schema_fingerprint: Digest
    handler: ExecutableSymbol
    writer_boundary: ExecutableSymbol
    applicability: WorkerApplicabilityKind
    published_records: tuple[RuntimeRecordSchema, ...] = Field(min_length=1)
    current_cut_validator: ExecutableSymbol


class WorkerAuthoritativeCommitRegistry(IngressDTO):
    schema_id: Literal["chiplog.worker-authoritative-commit-registry.v1"] = (
        "chiplog.worker-authoritative-commit-registry.v1"
    )
    registry_id: Identity
    version: Identity
    ordered_rows: tuple[WorkerAuthoritativeCommitRow, ...] = Field(min_length=1)
    fingerprint: Digest


class RetainedIngressHandoff(IngressDTO):
    kind: Literal["RETAINED_SOURCE"] = "RETAINED_SOURCE"
    retention_contract: Head
    allocation: ExecutableSymbol
    custody_publication: ExecutableSymbol
    release_or_ack: ExecutableSymbol


class DestructiveIngressHandoff(IngressDTO):
    kind: Literal["DESTRUCTIVE_READ"] = "DESTRUCTIVE_READ"
    preallocate_loss_slot: ExecutableSymbol
    destructive_receive: ExecutableSymbol
    exact_raw_custody_cas: ExecutableSymbol
    enumerate_unsettled_loss: ExecutableSymbol
    release_or_ack: ExecutableSymbol


IngressHandoff = Annotated[
    RetainedIngressHandoff | DestructiveIngressHandoff, Field(discriminator="kind")
]


class EvidenceIngressSurfaceRow(IngressDTO):
    row_id: Identity
    source_class: SourceClass
    source_contract: Head
    adapter_entrypoint: ExecutableSymbol
    authenticator: ExecutableSymbol
    handoff: IngressHandoff
    receipt_token_schema: RuntimeRecordSchema
    custody_schema: RuntimeRecordSchema
    admission_entrypoint: ExecutableSymbol
    replay_identity_version: Identity


class EvidenceIngressSurfaceManifest(IngressDTO):
    schema_id: Literal["chiplog.evidence-ingress-surface-manifest.v1"] = (
        "chiplog.evidence-ingress-surface-manifest.v1"
    )
    manifest_id: Identity
    version: Identity
    ordered_rows: tuple[EvidenceIngressSurfaceRow, ...] = Field(min_length=1)
    fingerprint: Digest


class DiscoveredWorkerPath(IngressDTO):
    """Observed from executable routing/writer sites, never copied from registry."""

    command_id: Identity
    request_schema: Identity
    request_schema_fingerprint: Digest
    handler: ExecutableSymbol
    writer_boundary: ExecutableSymbol
    applicability: WorkerApplicabilityKind
    published_records: tuple[RuntimeRecordSchema, ...] = Field(min_length=1)
    current_cut_validator: ExecutableSymbol
    discovery_origin: ExecutableSymbol


class DiscoveredIngressPath(IngressDTO):
    source_class: SourceClass
    source_contract: Head
    adapter_entrypoint: ExecutableSymbol
    authenticator: ExecutableSymbol
    handoff: IngressHandoff
    receipt_token_schema: RuntimeRecordSchema
    custody_schema: RuntimeRecordSchema
    admission_entrypoint: ExecutableSymbol
    replay_identity_version: Identity
    discovery_origin: ExecutableSymbol


class RuntimeSurfaceCut(IngressDTO):
    runtime_profile: Head
    broker_generation: Identity
    runtime_generation: Identity
    source_inventory_fingerprint: Digest
    worker_registry: Head
    ingress_manifest: Head


class RuntimeSurfaceDiscovery(IngressDTO):
    schema_id: Literal["chiplog.runtime-surface-discovery.v1"] = (
        "chiplog.runtime-surface-discovery.v1"
    )
    cut: RuntimeSurfaceCut
    discovery_algorithm: Head
    worker_paths: tuple[DiscoveredWorkerPath, ...]
    ingress_paths: tuple[DiscoveredIngressPath, ...]
    complete_observation_fingerprint: Digest


class RuntimeSurfaceDiscoveryPort(Protocol):
    """Observer of the actual mounted graph, including unexpected/empty surfaces.

    The consumer independently compares this observation to both registries and the
    mandatory source/applicability universe. Empty discovery is representable so a
    missing assembly cannot turn into an implicit successful or partial inventory.
    """

    def discover_runtime_surface(self) -> RuntimeSurfaceDiscovery: ...
