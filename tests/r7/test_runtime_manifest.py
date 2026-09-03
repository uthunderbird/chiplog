from __future__ import annotations

from dataclasses import replace

import pytest

from chiplog.architecture.r7_runtime import (
    R7_EVALUATION_MANIFEST,
    R7_PRODUCTION_MANIFEST,
    LeafBinding,
    RoutedCallDecl,
    RuntimeManifestViolation,
    verify_production_evaluation_equivalence,
    verify_runtime_manifest,
)
from chiplog.platform.r7_runtime import AuthorityBrokerRuntime


def test_runtime_manifest_has_one_authority_empty_process_per_owner() -> None:
    assert verify_runtime_manifest(R7_PRODUCTION_MANIFEST)
    assert all(not owner.raw_capabilities for owner in R7_PRODUCTION_MANIFEST.owners)
    assert {item.owner_id for item in R7_PRODUCTION_MANIFEST.owners} == {
        "deployment_trust",
        "planning",
        "projections",
    }


def test_sync_route_cycle_is_rejected() -> None:
    forward = RoutedCallDecl(
        "planning.callback",
        "planning",
        "deployment_trust",
        "request.v1",
        "result.v1",
    )
    reverse = RoutedCallDecl(
        "deployment_trust.callback",
        "deployment_trust",
        "planning",
        "request.v1",
        "result.v1",
    )
    with pytest.raises(RuntimeManifestViolation, match="cycle"):
        verify_runtime_manifest(
            replace(
                R7_PRODUCTION_MANIFEST,
                routes=(*R7_PRODUCTION_MANIFEST.routes, forward, reverse),
            )
        )


def test_raw_capability_in_owner_process_is_rejected() -> None:
    planning = replace(R7_PRODUCTION_MANIFEST.owners[1], raw_capabilities=("sqlite",))
    candidate = replace(
        R7_PRODUCTION_MANIFEST,
        owners=(R7_PRODUCTION_MANIFEST.owners[0], planning, R7_PRODUCTION_MANIFEST.owners[2]),
    )
    with pytest.raises(RuntimeManifestViolation, match="authority-empty"):
        verify_runtime_manifest(candidate)


@pytest.mark.parametrize("field", ["routes", "leaves"])
def test_closed_manifest_rejects_missing_route_or_leaf(field: str) -> None:
    candidate = (
        replace(R7_PRODUCTION_MANIFEST, routes=())
        if field == "routes"
        else replace(R7_PRODUCTION_MANIFEST, leaves=())
    )
    with pytest.raises(RuntimeManifestViolation, match="route" if field == "routes" else "leaf"):
        verify_runtime_manifest(candidate)


def test_production_and_evaluation_differ_only_by_registered_leaf_implementation() -> None:
    verify_production_evaluation_equivalence(R7_PRODUCTION_MANIFEST, R7_EVALUATION_MANIFEST)
    with AuthorityBrokerRuntime(
        "tenant-1", 1, "generation-1", R7_PRODUCTION_MANIFEST, b"prod-secret"
    ) as production:
        production_graph = production.graph_generation()
    with AuthorityBrokerRuntime(
        "tenant-1", 1, "generation-1", R7_EVALUATION_MANIFEST, b"eval-secret"
    ) as evaluation:
        evaluation_graph = evaluation.graph_generation()
    assert production_graph.routes == evaluation_graph.routes
    assert production_graph.broker_capabilities == evaluation_graph.broker_capabilities
    assert production_graph.application_loop_id == evaluation_graph.application_loop_id
    assert tuple((item.owner_id, item.capability_ids) for item in production_graph.owners) == tuple(
        (item.owner_id, item.capability_ids) for item in evaluation_graph.owners
    )
    assert tuple((item[0], item[1]) for item in production_graph.leaves) == tuple(
        (item[0], item[1]) for item in evaluation_graph.leaves
    )


def test_eval_cannot_change_security_profile_or_leaf_owner() -> None:
    with pytest.raises(RuntimeManifestViolation, match="security topology"):
        verify_production_evaluation_equivalence(
            R7_PRODUCTION_MANIFEST,
            replace(R7_EVALUATION_MANIFEST, ipc_security_profile="weaker"),
        )
    moved = replace(
        R7_EVALUATION_MANIFEST,
        leaves=(
            LeafBinding("clock", "planning", "tests.r7.leaves:EvaluationClock"),
            R7_EVALUATION_MANIFEST.leaves[1],
        ),
    )
    with pytest.raises(RuntimeManifestViolation, match="original owner"):
        verify_production_evaluation_equivalence(R7_PRODUCTION_MANIFEST, moved)
