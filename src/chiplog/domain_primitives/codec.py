"""Mechanical canonical-byte admission; contains no owner policy."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

from .canonical import CanonicalBytes, PreservedBytes
from .identity import RecordId, RecordTypeId, SchemaId
from .versions import Fingerprint, OwnerTag, ProducingVersions

FINGERPRINT_ALGORITHM = "sha256"
FINGERPRINT_DOMAIN = b"chiplog.record.v1\x00"


@dataclass(frozen=True)
class AdmissionResult:
    status: Literal["ADMITTED", "HOLD"]
    reason: str
    preserved: PreservedBytes
    fingerprint: Fingerprint | None


def canonical_record_bytes(
    record_id: RecordId,
    record_type: RecordTypeId,
    owner: OwnerTag,
    producing_versions: ProducingVersions,
    fields: Mapping[str, object],
) -> CanonicalBytes:
    """Encode one owner-produced record using its producing version bindings."""
    envelope = {
        "fields": fields,
        "owner": owner.value,
        "record_id": {
            "tenant_id": record_id.tenant_id.value,
            "value": record_id.value,
        },
        "record_type": {"name": record_type.name, "namespace": record_type.namespace},
        "versions": {
            "canonicalization": producing_versions.canonicalization.value,
            "codec": producing_versions.codec.value,
            "schema": {
                "name": producing_versions.schema_id.name,
                "namespace": producing_versions.schema_id.namespace,
                "version": producing_versions.schema_id.version,
            },
        },
    }
    payload = json.dumps(
        envelope,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return CanonicalBytes(payload, producing_versions)


def fingerprint(canonical: CanonicalBytes) -> Fingerprint:
    versions = canonical.producing_versions
    version_binding = json.dumps(
        {
            "canonicalization": versions.canonicalization.value,
            "codec": versions.codec.value,
            "schema": {
                "name": versions.schema_id.name,
                "namespace": versions.schema_id.namespace,
                "version": versions.schema_id.version,
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    bound_bytes = FINGERPRINT_DOMAIN + version_binding + b"\x00" + canonical.payload
    digest = hashlib.sha256(bound_bytes).digest()
    return Fingerprint(FINGERPRINT_ALGORITHM, digest)


def verify_preserved_bytes(preserved: PreservedBytes, expected: Fingerprint) -> bool:
    """Verify historical bytes without decoding or assigning current versions."""
    actual = fingerprint(CanonicalBytes(preserved.original, preserved.producing_versions))
    return actual == expected


def admit_exact_version(
    preserved: PreservedBytes,
    *,
    store_version: int,
    supported_store_versions: frozenset[int],
    supported_schemas: frozenset[SchemaId],
    supported_codec_versions: frozenset[int],
    supported_canonicalization_versions: frozenset[int],
    write: Callable[[PreservedBytes, Fingerprint], None],
) -> AdmissionResult:
    """Write only bytes whose exact producing and store versions are supported."""
    versions = preserved.producing_versions
    reasons = (
        (store_version not in supported_store_versions, "unknown store version"),
        (versions.schema_id not in supported_schemas, "unknown schema version"),
        (versions.codec.value not in supported_codec_versions, "unknown codec version"),
        (
            versions.canonicalization.value not in supported_canonicalization_versions,
            "unknown canonicalization version",
        ),
    )
    for unsupported, reason in reasons:
        if unsupported:
            return AdmissionResult("HOLD", reason, preserved, None)
    observed = fingerprint(CanonicalBytes(preserved.original, versions))
    write(preserved, observed)
    return AdmissionResult("ADMITTED", "exact producing versions admitted", preserved, observed)
