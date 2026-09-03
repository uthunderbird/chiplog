"""Independently authored executable reachability surface for R7 read invalidation."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from hashlib import sha256

from chiplog.architecture.r7_read_registry import AuthorityReadInvalidator


@dataclass(frozen=True)
class InvalidationSurfaceRow:
    invalidator_id: str
    validity_reads: tuple[str, ...]
    mutation_paths: tuple[str, ...]
    state_identity: str
    version: int
    common_order_owner: str


AUTHORITY_READ_INVALIDATION_SURFACE = (
    InvalidationSurfaceRow(
        "broker_epoch",
        ("BrokerReadState.broker_epoch",),
        ("BrokerAuthorityLedger.allocate_epoch",),
        "BrokerEpoch",
        1,
        "authority_broker",
    ),
    InvalidationSurfaceRow(
        "credential_session",
        ("BrokerReadState.credential_session_head",),
        ("BrokerReadLedger.invalidate_credential_session",),
        "CredentialSessionHead",
        1,
        "authority_broker",
    ),
    InvalidationSurfaceRow(
        "endpoint_channel",
        ("BrokerReadState.endpoint_channel_head",),
        ("BrokerReadLedger.invalidate_endpoint_channel",),
        "EndpointChannelBindingHead",
        1,
        "authority_broker",
    ),
    InvalidationSurfaceRow(
        "file_wal",
        ("BrokerReadState.file_wal_observation",),
        ("BrokerReadLedger.invalidate_file_wal",),
        "AuthenticatedFileWalObservation",
        1,
        "authority_broker",
    ),
    InvalidationSurfaceRow(
        "journal_decision",
        ("BrokerReadState.journal_head",),
        ("BrokerReadLedger.invalidate_journal_decision",),
        "TenantDecisionJournalHead",
        1,
        "authority_broker",
    ),
    InvalidationSurfaceRow(
        "materialization",
        ("BrokerReadState.materialization_commitment",),
        ("BrokerReadLedger.invalidate_materialization",),
        "MaterializationCommitment",
        1,
        "authority_broker",
    ),
    InvalidationSurfaceRow(
        "owner_generation",
        ("BrokerReadState.owner_generation", "BrokerReadState.owner_draining"),
        ("BrokerReadLedger.invalidate_owner_generation", "BrokerReadLedger.start_owner_drain"),
        "RuntimeGraphGeneration",
        1,
        "authority_broker",
    ),
    InvalidationSurfaceRow(
        "principal_contour",
        ("BrokerReadState.principal_contour_head",),
        ("BrokerReadLedger.invalidate_principal_contour",),
        "PrincipalContourHead",
        1,
        "authority_broker",
    ),
    InvalidationSurfaceRow(
        "storage_mutation",
        ("BrokerReadState.storage_mutation_generation",),
        ("BrokerReadLedger.invalidate_storage_mutation",),
        "StorageMutationGeneration",
        1,
        "authority_broker",
    ),
    InvalidationSurfaceRow(
        "trust_transition",
        ("BrokerReadState.trust_transition_head",),
        ("BrokerReadLedger.invalidate_trust_transition",),
        "TrustTransitionHead",
        1,
        "authority_broker",
    ),
)


def surface_bytes(
    surface: tuple[InvalidationSurfaceRow, ...] = AUTHORITY_READ_INVALIDATION_SURFACE,
) -> bytes:
    return json.dumps(
        [asdict(item) for item in surface], sort_keys=True, separators=(",", ":")
    ).encode()


def verify_surface_registry_equality(
    registry: tuple[AuthorityReadInvalidator, ...],
    surface: tuple[InvalidationSurfaceRow, ...] = AUTHORITY_READ_INVALIDATION_SURFACE,
) -> str:
    surface_projection = tuple(
        AuthorityReadInvalidator(
            item.invalidator_id,
            item.state_identity,
            item.version,
            item.common_order_owner,
        )
        for item in surface
    )
    if surface_projection != registry:
        raise ValueError("read invalidation surface and registry differ bidirectionally")
    if any(not item.validity_reads or not item.mutation_paths for item in surface):
        raise ValueError("read invalidation surface contains an unreachable row")
    return sha256(surface_bytes(surface)).hexdigest()


__all__ = [
    "AUTHORITY_READ_INVALIDATION_SURFACE",
    "InvalidationSurfaceRow",
    "surface_bytes",
    "verify_surface_registry_equality",
]
