"""Exact decoder for the registered recovery-frontier registry source."""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import Field, ValidationError

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.recovery_contracts import Present, RecoveryDTO
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import (
    RecoveryFrontierRegistry,
    RecoveryRegistryRow,
)

RECOVERY_FRONTIER_REGISTRY_SCHEMA = "chiplog.recovery.frontier-registry.v1"
_CONTENT_SCHEMA = "chiplog.recovery.frontier-registry-content.v1"

__all__ = [
    "RECOVERY_FRONTIER_REGISTRY_SCHEMA",
    "RecoveryFrontierRegistryIntegrityError",
    "decode_frontier_registry",
    "frontier_registry_content_fingerprint",
    "frontier_registry_reference",
]


class RecoveryFrontierRegistryIntegrityError(ValueError):
    """A frontier-registry source did not meet its fixed byte contract."""

    def __init__(
        self,
        operation: str,
        schema_id: str,
        expected_reference: CallSubjectHead | None,
        reason: str,
    ) -> None:
        self.operation = operation
        self.schema_id = schema_id
        self.expected_reference = expected_reference
        self.reason = reason
        super().__init__(f"{operation}: {reason}")


class _FrontierRegistryContentPreimage(RecoveryDTO):
    schema_id: Literal["chiplog.recovery.frontier-registry-content.v1"] = (
        "chiplog.recovery.frontier-registry-content.v1"
    )
    registry_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    ordered_rows: tuple[RecoveryRegistryRow, ...]


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _content_preimage(
    registry_id: str, version: str, ordered_rows: tuple[RecoveryRegistryRow, ...]
) -> _FrontierRegistryContentPreimage:
    return _FrontierRegistryContentPreimage(
        registry_id=registry_id,
        version=version,
        ordered_rows=ordered_rows,
    )


def frontier_registry_content_fingerprint(
    registry_id: str, version: str, ordered_rows: tuple[RecoveryRegistryRow, ...]
) -> str:
    """Return the embedded content-domain fingerprint, excluding itself."""

    return _sha256(_content_preimage(registry_id, version, ordered_rows).canonical_bytes())


def _require_valid_registry(
    registry: RecoveryFrontierRegistry,
    *,
    operation: str,
    expected_reference: CallSubjectHead | None,
) -> None:
    rows = registry.ordered_rows
    row_bytes = tuple(row.canonical_bytes() for row in rows)
    if len(set(row_bytes)) != len(row_bytes):
        raise RecoveryFrontierRegistryIntegrityError(
            operation,
            RECOVERY_FRONTIER_REGISTRY_SCHEMA,
            expected_reference,
            "registry rows are duplicated",
        )
    if tuple(row.ordinal for row in rows) != tuple(range(len(rows))):
        raise RecoveryFrontierRegistryIntegrityError(
            operation,
            RECOVERY_FRONTIER_REGISTRY_SCHEMA,
            expected_reference,
            "registry row ordinals are not exactly ordered from zero",
        )
    fingerprint = frontier_registry_content_fingerprint(
        registry.registry_id, registry.version, registry.ordered_rows
    )
    if registry.fingerprint != fingerprint:
        raise RecoveryFrontierRegistryIntegrityError(
            operation,
            RECOVERY_FRONTIER_REGISTRY_SCHEMA,
            expected_reference,
            "embedded content fingerprint differs from registry content",
        )


def frontier_registry_reference(registry: RecoveryFrontierRegistry) -> CallSubjectHead:
    """Derive the external source reference only for a valid registry body."""

    _require_valid_registry(
        registry,
        operation="frontier_registry_reference",
        expected_reference=None,
    )
    digest = _sha256(registry.canonical_bytes())
    return CallSubjectHead(
        subject_id=registry.registry_id,
        revision=Present(
            head=RECOVERY_FRONTIER_REGISTRY_SCHEMA + ":" + digest,
            fingerprint=digest,
        ),
    )


def decode_frontier_registry(
    schema_id: str,
    canonical_registry_bytes: bytes,
    *,
    expected_reference: CallSubjectHead,
) -> RecoveryFrontierRegistry:
    """Decode one exact external registry source without normalizing its bytes."""

    operation = "decode_frontier_registry"
    if schema_id != RECOVERY_FRONTIER_REGISTRY_SCHEMA:
        raise RecoveryFrontierRegistryIntegrityError(
            operation, schema_id, expected_reference, "unknown registry schema"
        )
    try:
        registry = RecoveryFrontierRegistry.model_validate_json(canonical_registry_bytes)
    except (UnicodeDecodeError, ValidationError, ValueError) as error:
        raise RecoveryFrontierRegistryIntegrityError(
            operation, schema_id, expected_reference, "invalid registry bytes"
        ) from error
    if registry.canonical_bytes() != canonical_registry_bytes:
        raise RecoveryFrontierRegistryIntegrityError(
            operation, schema_id, expected_reference, "registry bytes are not canonical"
        )
    _require_valid_registry(
        registry,
        operation=operation,
        expected_reference=expected_reference,
    )
    if frontier_registry_reference(registry) != expected_reference:
        raise RecoveryFrontierRegistryIntegrityError(
            operation,
            schema_id,
            expected_reference,
            "external registry reference differs from bytes",
        )
    return registry
