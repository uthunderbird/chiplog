"""The H1 completion read-plan registry is a closed, replayable grammar."""

from __future__ import annotations

import hashlib

import pytest


def test_current_registry_has_fixed_canonical_identity_and_order() -> None:
    from chiplog.composition.h1_completion_readplan_registry import current_registry

    registry = current_registry()

    assert registry.schema == "chiplog.h1-completion-read-plan-registry.v1"
    assert registry.version == 1
    assert tuple(role.name for role in registry.roles) == (
        "sealed_run",
        "selected_seal",
        "recovery_frontier_registry",
        "run_completion",
    )
    assert tuple(role.presence for role in registry.roles) == (
        "PRESENT",
        "PRESENT",
        "PRESENT",
        "ABSENT",
    )
    assert registry.fingerprint == hashlib.sha256(registry.canonical_bytes).hexdigest()
    assert registry.head == f"h1-completion-read-plan:1:{registry.fingerprint}"
    assert current_registry() is registry


def test_registry_decoder_rejects_reordered_unknown_duplicate_or_noncanonical_roles() -> None:
    from chiplog.composition.h1_completion_readplan_registry import (
        H1CompletionReadPlanRegistryError,
        current_registry,
        decode_registry,
    )

    canonical = current_registry().canonical_bytes
    assert decode_registry(canonical) == current_registry()

    for hostile in (
        canonical.replace(b'"sealed_run"', b'"unknown_run"', 1),
        canonical.replace(b'"version":1', b'"version":2', 1),
        b'{"roles":[],"schema":"chiplog.h1-completion-read-plan-registry.v1","version":1}',
        canonical + b"\n",
    ):
        with pytest.raises(H1CompletionReadPlanRegistryError):
            decode_registry(hostile)


def test_registry_identity_cannot_be_detached_from_canonical_bytes() -> None:
    from chiplog.composition.h1_completion_readplan_registry import (
        H1CompletionReadPlanRegistryError,
        current_registry,
        require_registry_identity,
    )

    registry = current_registry()
    assert (
        require_registry_identity(
            canonical_bytes=registry.canonical_bytes,
            expected_head=registry.head,
            expected_fingerprint=registry.fingerprint,
        )
        == registry
    )

    with pytest.raises(H1CompletionReadPlanRegistryError):
        require_registry_identity(
            canonical_bytes=registry.canonical_bytes,
            expected_head=registry.head + "0",
            expected_fingerprint=registry.fingerprint,
        )
