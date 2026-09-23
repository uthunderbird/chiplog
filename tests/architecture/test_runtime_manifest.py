from __future__ import annotations

from dataclasses import replace

import pytest

from chiplog.architecture.r7_runtime import (
    CODEX_CLI_MANIFEST,
    R7_EVALUATION_MANIFEST,
    R7_PRODUCTION_MANIFEST,
    R13_EVALUATION_MANIFEST,
    R13_PRODUCTION_MANIFEST,
    R14_EVALUATION_MANIFEST,
    R14_PRODUCTION_MANIFEST,
    R16_EVALUATION_MANIFEST,
    R16_PRODUCTION_MANIFEST,
    LeafBinding,
    RoutedCallDecl,
    RuntimeManifestViolation,
    verify_production_evaluation_equivalence,
    verify_runtime_manifest,
)
from chiplog.platform.r7_runtime import AuthorityBrokerRuntime


def test_cli_and_preparation_profiles_reject_cross_profile_substitution() -> None:
    assert CODEX_CLI_MANIFEST.manifest_version != R14_PRODUCTION_MANIFEST.manifest_version
    for manifest, other in (
        (CODEX_CLI_MANIFEST, R14_PRODUCTION_MANIFEST),
        (R14_PRODUCTION_MANIFEST, CODEX_CLI_MANIFEST),
    ):
        assert verify_runtime_manifest(manifest)
        with pytest.raises(RuntimeManifestViolation):
            verify_runtime_manifest(replace(manifest, manifest_version=other.manifest_version))
        with pytest.raises(RuntimeManifestViolation):
            verify_runtime_manifest(replace(manifest, leaves=other.leaves))


def test_denial_manifest_realizes_new_exact_owner_partition_without_changing_legacy() -> None:
    verify_production_evaluation_equivalence(R16_PRODUCTION_MANIFEST, R16_EVALUATION_MANIFEST)
    assert R14_PRODUCTION_MANIFEST.manifest_version == 4
    assert next(
        owner for owner in R14_PRODUCTION_MANIFEST.owners if owner.owner_id == "effects"
    ).capability_ids == ("effects.prepare_transition",)
    for manifest in (R16_PRODUCTION_MANIFEST, R16_EVALUATION_MANIFEST):
        assert manifest.manifest_version == 7
        with AuthorityBrokerRuntime("tenant", 1, "denial", manifest, b"offline") as runtime:
            assert runtime.graph_generation().routes == tuple(
                (
                    r.operation_id,
                    r.caller_owner_id,
                    r.callee_owner_id,
                    r.request_schema_id,
                    r.result_schema_id,
                )
                for r in manifest.routes
            )
            effects = next(row for row in runtime.attest() if row.identity.owner_id == "effects")
            assert effects.identity.capability_ids == (
                "effects.prepare_denial",
                "effects.prepare_transition",
            )
            assert effects.loaded_policy_modules == (
                "chiplog.capabilities.effects._process",
                "chiplog.capabilities.effects._r16_process",
            )


def test_denial_manifest_cannot_substitute_leaf_or_downgrade_owner_target() -> None:
    changed_leaf = replace(
        R16_PRODUCTION_MANIFEST,
        leaves=(
            replace(R16_PRODUCTION_MANIFEST.leaves[0], implementation="external:Live"),
            *R16_PRODUCTION_MANIFEST.leaves[1:],
        ),
    )
    with pytest.raises(RuntimeManifestViolation, match="hermetic"):
        verify_runtime_manifest(changed_leaf)
    changed_owner = replace(
        R16_PRODUCTION_MANIFEST,
        owners=tuple(
            replace(owner, target_ids=("chiplog.capabilities.effects._process:dispatch",))
            if owner.owner_id == "effects"
            else owner
            for owner in R16_PRODUCTION_MANIFEST.owners
        ),
    )
    with pytest.raises(RuntimeManifestViolation, match="capabilities or public operations"):
        verify_runtime_manifest(changed_owner)


def test_preparation_manifest_realizes_exact_production_and_evaluation_graphs() -> None:
    verify_production_evaluation_equivalence(R14_PRODUCTION_MANIFEST, R14_EVALUATION_MANIFEST)
    for manifest in (R14_PRODUCTION_MANIFEST, R14_EVALUATION_MANIFEST):
        with AuthorityBrokerRuntime("tenant", 1, "preparation", manifest, b"offline") as runtime:
            graph = runtime.graph_generation()
            assert graph.routes == tuple(
                (
                    r.operation_id,
                    r.caller_owner_id,
                    r.callee_owner_id,
                    r.request_schema_id,
                    r.result_schema_id,
                )
                for r in manifest.routes
            )
            assert tuple((owner.owner_id, owner.capability_ids) for owner in graph.owners) == tuple(
                (owner.owner_id, owner.capability_ids) for owner in manifest.owners
            )
            attestations = {item.identity.owner_id: item for item in runtime.attest()}
            assert attestations["agent_loop"].loaded_policy_modules == (
                "chiplog.capabilities.agent_loop._delivery_process",
                "chiplog.capabilities.agent_loop._r13_process",
                "chiplog.capabilities.agent_loop._r14_process",
                "chiplog.capabilities.agent_loop._scheduler_process",
            )
            assert attestations["effects"].loaded_policy_modules == (
                "chiplog.capabilities.effects._process",
            )


def test_preparation_manifest_rejects_replaced_hermetic_leaf() -> None:
    candidate = replace(
        R14_PRODUCTION_MANIFEST,
        leaves=(
            replace(R14_PRODUCTION_MANIFEST.leaves[0], implementation="unregistered:Clock"),
            *R14_PRODUCTION_MANIFEST.leaves[1:],
        ),
    )
    with pytest.raises(RuntimeManifestViolation, match="hermetic"):
        verify_runtime_manifest(candidate)


def test_r13_production_and_evaluation_realize_the_same_loop_owner_partition() -> None:
    verify_production_evaluation_equivalence(R13_PRODUCTION_MANIFEST, R13_EVALUATION_MANIFEST)
    graphs = []
    for manifest in (R13_PRODUCTION_MANIFEST, R13_EVALUATION_MANIFEST):
        with AuthorityBrokerRuntime(
            "hermetic-tenant", 1, "r13-parity", manifest, b"offline"
        ) as runtime:
            graphs.append(runtime.graph_generation())
    assert graphs[0].application_loop_id == graphs[1].application_loop_id == "chiplog.agent-loop.v1"
    assert graphs[0].routes == graphs[1].routes
    assert graphs[0].broker_capabilities == graphs[1].broker_capabilities
    assert [(owner.owner_id, owner.capability_ids) for owner in graphs[0].owners] == [
        (owner.owner_id, owner.capability_ids) for owner in graphs[1].owners
    ]
    assert "agent_loop" in {owner.owner_id for owner in graphs[0].owners}
    with pytest.raises(RuntimeManifestViolation):
        verify_production_evaluation_equivalence(
            R13_PRODUCTION_MANIFEST,
            replace(R13_EVALUATION_MANIFEST, application_loop_id="evaluation-only-loop"),
        )


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


@pytest.mark.parametrize("hybrid", [False, True])
def test_delivery_routes_cannot_be_omitted_or_mixed_with_legacy_partition(hybrid: bool) -> None:
    routes = tuple(
        route
        for route in R14_PRODUCTION_MANIFEST.routes
        if route.operation_id != "agent_loop.prepare_delivery_completion"
    )
    candidate = replace(R14_PRODUCTION_MANIFEST, routes=routes)
    if hybrid:
        candidate = replace(
            R14_PRODUCTION_MANIFEST,
            owners=tuple(
                replace(
                    owner,
                    capability_ids=("agent_loop.validate_transition",),
                    public_operations=("agent_loop.validate_transition",),
                )
                if owner.owner_id == "agent_loop"
                else owner
                for owner in R14_PRODUCTION_MANIFEST.owners
            ),
        )
    with pytest.raises(RuntimeManifestViolation):
        verify_runtime_manifest(candidate)
