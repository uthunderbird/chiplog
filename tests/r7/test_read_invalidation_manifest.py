from __future__ import annotations

from dataclasses import replace

import pytest

from chiplog.architecture.r7_read_registry import (
    AUTHORITY_READ_INVALIDATOR_REGISTRY,
    verify_invalidator_registry,
)
from chiplog.verification.r7_read_surface import (
    AUTHORITY_READ_INVALIDATION_SURFACE,
    InvalidationSurfaceRow,
    verify_surface_registry_equality,
)


def test_independent_surface_equals_registry_bidirectionally() -> None:
    assert verify_invalidator_registry()
    assert verify_surface_registry_equality(AUTHORITY_READ_INVALIDATOR_REGISTRY)
    assert tuple(item.invalidator_id for item in AUTHORITY_READ_INVALIDATION_SURFACE) == tuple(
        item.invalidator_id for item in AUTHORITY_READ_INVALIDATOR_REGISTRY
    )


@pytest.mark.parametrize("mutation", ["missing", "extra", "owner", "pathless"])
def test_surface_mutants_reject_generation_publication(mutation: str) -> None:
    surface = AUTHORITY_READ_INVALIDATION_SURFACE
    candidate: tuple[InvalidationSurfaceRow, ...]
    if mutation == "missing":
        candidate = surface[:-1]
    elif mutation == "extra":
        candidate = (*surface, replace(surface[-1], invalidator_id="unknown"))
    elif mutation == "owner":
        candidate = (replace(surface[0], common_order_owner="planning"), *surface[1:])
    else:
        candidate = (replace(surface[0], mutation_paths=()), *surface[1:])
    with pytest.raises(ValueError, match=r"differ|unreachable"):
        verify_surface_registry_equality(AUTHORITY_READ_INVALIDATOR_REGISTRY, candidate)


def test_registry_mutant_cannot_self_certify_against_independent_surface() -> None:
    changed = (
        replace(AUTHORITY_READ_INVALIDATOR_REGISTRY[0], state_identity="CallerEpoch"),
        *AUTHORITY_READ_INVALIDATOR_REGISTRY[1:],
    )
    with pytest.raises(ValueError, match="bidirectionally"):
        verify_surface_registry_equality(changed)
