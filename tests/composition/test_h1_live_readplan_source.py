"""The unowned H1 read plan must not manufacture an issuance manifest."""

from __future__ import annotations

import pytest


class _HostileEvidence:
    def __getattribute__(self, _: str) -> object:
        raise AssertionError("deny-only source inspected caller evidence")


def test_uninstalled_readplan_source_has_no_manifest_or_predecessor_success_path() -> None:
    """Caller-shaped reads cannot fill in for installed physical owners."""
    from chiplog.composition.h1_live_readplan_source import (
        H1LiveReadPlanSource,
        H1ReadPlanSourceUnavailable,
    )

    source = H1LiveReadPlanSource()

    for value in (
        object(),
        {"ordered_heads": (), "registry_head": "forged"},
        _HostileEvidence(),
    ):
        with pytest.raises(H1ReadPlanSourceUnavailable, match="not installed"):
            source.capture_predecessor(evidence=value)
        with pytest.raises(H1ReadPlanSourceUnavailable, match="not installed"):
            source.issue_manifest(evidence=value)


def test_readplan_source_names_the_atomic_owner_extension_without_inventing_heads() -> None:
    """The missing registry, not DTO data, must name each required observed head."""
    from chiplog.composition.h1_live_readplan_source import (
        H1LiveReadPlanSource,
        H1ReadPlanOwnerExtension,
    )

    source = H1LiveReadPlanSource()
    extension = source.required_owner_extension

    assert isinstance(extension, H1ReadPlanOwnerExtension)
    assert extension.operation == "agent_loop.complete_acceptance.v2"
    assert extension.ordered_heads == ()
    assert extension.reason == "no installed H1 completion read-plan/registry owner"
    assert extension.requires_one_gate_cut is True
    assert extension.requires_presence_and_absence is True
    assert extension.required_physical_observations == (
        "tenant frontier",
        "materialization commitment",
        "no pending owner/loop/gate publication",
        "selected H1 publication registry revision/head/fingerprint",
    )
