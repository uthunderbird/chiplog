from __future__ import annotations

from chiplog.architecture.r7_read_registry import (
    AUTHORITY_READ_INVALIDATOR_REGISTRY,
    verify_invalidator_registry,
)
from chiplog.verification.r7_read_surface import (
    AUTHORITY_READ_INVALIDATION_SURFACE,
    verify_surface_registry_equality,
)


def test_independent_surface_equals_registry_bidirectionally() -> None:
    assert verify_invalidator_registry()
    assert verify_surface_registry_equality(AUTHORITY_READ_INVALIDATOR_REGISTRY)
    assert tuple(item.invalidator_id for item in AUTHORITY_READ_INVALIDATION_SURFACE) == tuple(
        item.invalidator_id for item in AUTHORITY_READ_INVALIDATOR_REGISTRY
    )
