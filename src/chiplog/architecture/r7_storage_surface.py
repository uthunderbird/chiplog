"""Independent closed storage and authority-read edge manifest for R7."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from hashlib import sha256


@dataclass(frozen=True)
class StorageMember:
    table: str
    columns: tuple[str, ...]
    authority_bearing: bool


@dataclass(frozen=True)
class ReadEdge:
    operation: str
    table: str
    columns: tuple[str, ...]


AUTHORITY_STORAGE_MEMBERS = (
    StorageMember("deletion_fences", ("tenant_id", "generation", "frontier"), True),
    StorageMember(
        "derivatives",
        (
            "tenant_id",
            "sink",
            "derivative_id",
            "source_record_ids",
            "source_epoch",
            "provenance_fingerprint",
        ),
        False,
    ),
    StorageMember(
        "evidence_inbox",
        (
            "tenant_id",
            "source_id",
            "evidence_id",
            "fingerprint",
            "canonical_bytes",
            "followup_kind",
            "state",
            "attempt_id",
            "transport_version",
            "cursor",
        ),
        True,
    ),
    StorageMember(
        "publications",
        (
            "tenant_id",
            "operation_kind",
            "idempotency_key",
            "request_fingerprint",
            "commit_sequence",
            "record_ids",
        ),
        True,
    ),
    StorageMember(
        "records",
        ("tenant_id", "record_id", "owner", "schema_id", "canonical_bytes", "commit_sequence"),
        True,
    ),
    StorageMember("store_metadata", ("singleton", "store_version"), True),
    StorageMember("tenant_heads", ("tenant_id", "head"), True),
)

PLANNING_PUBLICATION_READ_EDGES = (
    ReadEdge(
        "PLANNING_PUBLICATIONS",
        "publications",
        (
            "tenant_id",
            "operation_kind",
            "idempotency_key",
            "request_fingerprint",
            "commit_sequence",
            "record_ids",
        ),
    ),
    ReadEdge(
        "PLANNING_PUBLICATIONS",
        "records",
        ("tenant_id", "record_id", "owner", "schema_id", "canonical_bytes", "commit_sequence"),
    ),
)


def authority_storage_surface_bytes() -> bytes:
    return json.dumps(
        {
            "members": [asdict(item) for item in AUTHORITY_STORAGE_MEMBERS],
            "read_edges": [asdict(item) for item in PLANNING_PUBLICATION_READ_EDGES],
            "version": 1,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


AUTHORITY_STORAGE_SURFACE_DIGEST = sha256(authority_storage_surface_bytes()).hexdigest()


__all__ = [
    "AUTHORITY_STORAGE_MEMBERS",
    "AUTHORITY_STORAGE_SURFACE_DIGEST",
    "PLANNING_PUBLICATION_READ_EDGES",
    "ReadEdge",
    "StorageMember",
    "authority_storage_surface_bytes",
]
