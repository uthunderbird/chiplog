"""Closed broker-owned invalidator registry for R7 authority reads."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from hashlib import sha256


@dataclass(frozen=True)
class AuthorityReadInvalidator:
    invalidator_id: str
    state_identity: str
    version: int
    common_order_owner: str


AUTHORITY_READ_INVALIDATOR_REGISTRY = (
    AuthorityReadInvalidator("broker_epoch", "BrokerEpoch", 1, "authority_broker"),
    AuthorityReadInvalidator("credential_session", "CredentialSessionHead", 1, "authority_broker"),
    AuthorityReadInvalidator(
        "endpoint_channel", "EndpointChannelBindingHead", 1, "authority_broker"
    ),
    AuthorityReadInvalidator("file_wal", "AuthenticatedFileWalObservation", 1, "authority_broker"),
    AuthorityReadInvalidator(
        "journal_decision", "TenantDecisionJournalHead", 1, "authority_broker"
    ),
    AuthorityReadInvalidator("materialization", "MaterializationCommitment", 1, "authority_broker"),
    AuthorityReadInvalidator("owner_generation", "RuntimeGraphGeneration", 1, "authority_broker"),
    AuthorityReadInvalidator("principal_contour", "PrincipalContourHead", 1, "authority_broker"),
    AuthorityReadInvalidator(
        "storage_mutation", "StorageMutationGeneration", 1, "authority_broker"
    ),
    AuthorityReadInvalidator("trust_transition", "TrustTransitionHead", 1, "authority_broker"),
)


def invalidator_registry_bytes(
    registry: tuple[AuthorityReadInvalidator, ...] = AUTHORITY_READ_INVALIDATOR_REGISTRY,
) -> bytes:
    return json.dumps(
        [asdict(item) for item in registry], sort_keys=True, separators=(",", ":")
    ).encode()


def verify_invalidator_registry(
    registry: tuple[AuthorityReadInvalidator, ...] = AUTHORITY_READ_INVALIDATOR_REGISTRY,
) -> str:
    identities = tuple(item.invalidator_id for item in registry)
    expected = tuple(item.invalidator_id for item in AUTHORITY_READ_INVALIDATOR_REGISTRY)
    if identities != expected or len(identities) != len(set(identities)):
        raise ValueError("authority-read invalidator registry exact-set mismatch")
    if any(item.common_order_owner != "authority_broker" for item in registry):
        raise ValueError("authority-read invalidator lacks the sole common-order owner")
    return sha256(invalidator_registry_bytes(registry)).hexdigest()


__all__ = [
    "AUTHORITY_READ_INVALIDATOR_REGISTRY",
    "AuthorityReadInvalidator",
    "invalidator_registry_bytes",
    "verify_invalidator_registry",
]
