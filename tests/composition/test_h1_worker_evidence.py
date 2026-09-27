"""Installed H1 worker source capabilities reject unowned authority inputs."""

from __future__ import annotations

import copy
from typing import Any, cast

import pytest

from chiplog.composition.h1_worker_evidence import (
    H1WorkerSourceCapability,
    _H1InstalledWorkerEvidenceOwner,
)


def _unmounted_owner() -> _H1InstalledWorkerEvidenceOwner:
    """A deliberately incomplete owner for checks that must precede source replay."""
    owner = object.__new__(_H1InstalledWorkerEvidenceOwner)
    owner._closed = False
    owner._pid = 0
    owner._issued = {}
    return owner


def test_capability_has_no_caller_constructible_authority() -> None:
    with pytest.raises(TypeError, match="owner"):
        H1WorkerSourceCapability()


def test_owner_rejects_noncanonical_runtime_before_creating_authority() -> None:
    with pytest.raises(TypeError, match="canonical common CLI runtime"):
        _H1InstalledWorkerEvidenceOwner(
            cast(Any, object()),
            cast(Any, object()),
            cast(Any, object()),
            cast(Any, object()),
            cast(Any, object()),
        )


def test_capture_rejects_a_forged_native_capability_before_source_replay() -> None:
    owner = _unmounted_owner()

    with pytest.raises(ValueError, match="native capability"):
        owner._capture_for_pre_request(cast(Any, object()))


def test_replay_rejects_constructed_and_copied_capabilities_before_source_replay() -> None:
    owner = _unmounted_owner()
    forged = object.__new__(H1WorkerSourceCapability)

    with pytest.raises(ValueError, match="worker capability"):
        owner._replay_for_pre_request(forged, cast(Any, object()))
    with pytest.raises(TypeError, match="copied"):
        copy.copy(forged)
    with pytest.raises(TypeError, match="copied"):
        copy.deepcopy(forged)


def test_revoke_denies_an_even_previously_registered_capability_before_source_replay() -> None:
    owner = _unmounted_owner()
    capability = object.__new__(H1WorkerSourceCapability)
    cast(dict[int, Any], owner._issued)[id(capability)] = capability
    owner._revoke_all()

    with pytest.raises(ValueError, match=r"closed|worker capability"):
        owner._replay_for_pre_request(capability, cast(Any, object()))
