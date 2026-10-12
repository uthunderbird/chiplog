from __future__ import annotations

from dataclasses import replace

import pytest

from chiplog.adapters.driven.effects_hermetic import HermeticEffectsProvider
from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.architecture.r7_runtime import (
    CODEX_CLI_MANIFEST,
    R7_EVALUATION_MANIFEST,
    R7_PRODUCTION_MANIFEST,
    R13_EVALUATION_MANIFEST,
    R13_PRODUCTION_MANIFEST,
    R14_EVALUATION_MANIFEST,
    R14_EXECUTION_ACCEPTED_DIRECT_TERMINAL_V30_PRODUCTION_MANIFEST,
    R14_EXECUTION_ACCEPTED_OPEN_V29_PRODUCTION_MANIFEST,
    R14_EXECUTION_LOOP_SEMANTIC_REDUCTION_V34_EVALUATION_MANIFEST,
    R14_EXECUTION_LOOP_SEMANTIC_REDUCTION_V34_PRODUCTION_MANIFEST,
    R14_EXECUTION_OBLIGATION_RESUME_V37_EVALUATION_MANIFEST,
    R14_EXECUTION_OBLIGATION_RESUME_V37_PRODUCTION_MANIFEST,
    R14_EXECUTION_ORIGINAL_CALL_RESOLUTION_V33_EVALUATION_MANIFEST,
    R14_EXECUTION_ORIGINAL_CALL_RESOLUTION_V33_PRODUCTION_MANIFEST,
    R14_EXECUTION_ORIGINAL_RESOLVER_INVOCATION_V32_EVALUATION_MANIFEST,
    R14_EXECUTION_ORIGINAL_RESOLVER_INVOCATION_V32_PRODUCTION_MANIFEST,
    R14_EXECUTION_ORIGINAL_RESOLVER_POLICY_V31_EVALUATION_MANIFEST,
    R14_EXECUTION_ORIGINAL_RESOLVER_POLICY_V31_PRODUCTION_MANIFEST,
    R14_EXECUTION_SAME_RUN_RESUME_V36_EVALUATION_MANIFEST,
    R14_EXECUTION_SAME_RUN_RESUME_V36_PRODUCTION_MANIFEST,
    R14_EXECUTION_SUSPENSION_V35_EVALUATION_MANIFEST,
    R14_EXECUTION_SUSPENSION_V35_PRODUCTION_MANIFEST,
    R14_EXECUTION_SUCCESSOR_V38_EVALUATION_MANIFEST,
    R14_EXECUTION_SUCCESSOR_V38_PRODUCTION_MANIFEST,
    R14_PRODUCTION_MANIFEST,
    R14_R17_H1_NATIVE_ONE_CALL_ACCOUNTING_V23_EVALUATION_MANIFEST,
    R14_R17_H1_NATIVE_ONE_CALL_ACCOUNTING_V23_PRODUCTION_MANIFEST,
    R16_EVALUATION_MANIFEST,
    R16_PRODUCTION_MANIFEST,
    LeafBinding,
    RoutedCallDecl,
    RuntimeManifestViolation,
    verify_production_evaluation_equivalence,
    verify_runtime_manifest,
)
from chiplog.platform.r7_leaves import ProductionClock, ProductionPlanningStore
from chiplog.platform.r7_runtime import AuthorityBrokerRuntime


def test_v35_registers_execution_suspension_without_mutating_v34() -> None:
    production = R14_EXECUTION_SUSPENSION_V35_PRODUCTION_MANIFEST
    evaluation = R14_EXECUTION_SUSPENSION_V35_EVALUATION_MANIFEST
    previous = R14_EXECUTION_LOOP_SEMANTIC_REDUCTION_V34_PRODUCTION_MANIFEST
    operation = "agent_loop.prepare_execution_suspension.v1"

    assert verify_runtime_manifest(production)
    verify_production_evaluation_equivalence(production, evaluation)
    assert production.manifest_version == 35
    assert operation not in {
        item.operation_id for item in previous.routes
    }
    route = next(item for item in production.routes if item.operation_id == operation)
    assert route.request_schema_id == "chiplog.execution.recovery-request.v1"
    assert route.result_schema_id == "chiplog.execution.recovery-result.v1"
    owner = next(item for item in production.owners if item.owner_id == "agent_loop")
    assert operation in owner.capability_ids
    assert operation in owner.public_operations
    old_owner = next(item for item in previous.owners if item.owner_id == "agent_loop")
    assert operation not in old_owner.public_operations
    assert owner.target_ids == old_owner.target_ids


def test_v36_registers_same_run_resume_without_mutating_v35() -> None:
    production = R14_EXECUTION_SAME_RUN_RESUME_V36_PRODUCTION_MANIFEST
    evaluation = R14_EXECUTION_SAME_RUN_RESUME_V36_EVALUATION_MANIFEST
    previous = R14_EXECUTION_SUSPENSION_V35_PRODUCTION_MANIFEST
    operation = "agent_loop.prepare_execution_resume.v1"

    assert verify_runtime_manifest(production)
    verify_production_evaluation_equivalence(production, evaluation)
    assert production.manifest_version == 36
    assert operation not in {item.operation_id for item in previous.routes}
    route = next(item for item in production.routes if item.operation_id == operation)
    assert route.request_schema_id == "chiplog.execution.recovery-request.v1"
    assert route.result_schema_id == "chiplog.execution.recovery-result.v1"
    owner = next(item for item in production.owners if item.owner_id == "agent_loop")
    previous_owner = next(item for item in previous.owners if item.owner_id == "agent_loop")
    assert operation in owner.capability_ids
    assert operation in owner.public_operations
    assert owner.target_ids == previous_owner.target_ids


def test_v37_selects_the_new_profile_generation_and_preserves_v36_route_graph() -> None:
    production = R14_EXECUTION_OBLIGATION_RESUME_V37_PRODUCTION_MANIFEST
    evaluation = R14_EXECUTION_OBLIGATION_RESUME_V37_EVALUATION_MANIFEST
    previous = R14_EXECUTION_SAME_RUN_RESUME_V36_PRODUCTION_MANIFEST

    assert verify_runtime_manifest(production)
    verify_production_evaluation_equivalence(production, evaluation)
    assert production.manifest_version == 37
    added_operations = {
        "agent_loop.prepare_execution_suspension_with_obligation.v1",
        "agent_loop.prepare_execution_resume_with_obligation.v1",
    }
    assert {route.operation_id for route in production.routes} - {
        route.operation_id for route in previous.routes
    } == added_operations
    assert {route.operation_id for route in previous.routes} <= {
        route.operation_id for route in production.routes
    }
    assert production.owners != previous.owners
    assert previous.manifest_version == 36
    with AuthorityBrokerRuntime(
        "tenant",
        1,
        "obligation-resume-v37",
        production,
        b"offline",
        realized_leaves={
            "clock": ProductionClock(),
            "model": HermeticModel(),
            "planning_store": ProductionPlanningStore(),
            "effects_transport": HermeticEffectsProvider(
                receipt_key=b"fixture", scenarios=()
            ),
        },
    ) as runtime:
        loop = next(row for row in runtime.attest() if row.identity.owner_id == "agent_loop")
    assert {
        "agent_loop.prepare_execution_suspension_with_obligation.v1",
        "agent_loop.prepare_execution_resume_with_obligation.v1",
    } <= set(loop.identity.capability_ids)
    assert loop.target_ids == (
        "chiplog.capabilities.agent_loop._original_recovery_process:dispatch",
    )


def test_v38_registers_execution_successor_without_mutating_v37() -> None:
    production = R14_EXECUTION_SUCCESSOR_V38_PRODUCTION_MANIFEST
    evaluation = R14_EXECUTION_SUCCESSOR_V38_EVALUATION_MANIFEST
    previous = R14_EXECUTION_OBLIGATION_RESUME_V37_PRODUCTION_MANIFEST
    operation = "agent_loop.prepare_execution_successor.v1"

    assert verify_runtime_manifest(production)
    verify_production_evaluation_equivalence(production, evaluation)
    assert production.manifest_version == 38
    assert previous.manifest_version == 37
    assert {route.operation_id for route in production.routes} - {
        route.operation_id for route in previous.routes
    } == {operation}
    route = next(route for route in production.routes if route.operation_id == operation)
    assert route.request_schema_id == "chiplog.execution.recovery-request.v1"
    assert route.result_schema_id == "chiplog.execution.recovery-result.v1"
    loop = next(owner for owner in production.owners if owner.owner_id == "agent_loop")
    previous_loop = next(owner for owner in previous.owners if owner.owner_id == "agent_loop")
    assert operation in loop.capability_ids
    assert operation in loop.public_operations
    assert loop.target_ids == previous_loop.target_ids
    assert operation not in previous_loop.public_operations


def test_v30_registers_direct_terminal_owner_and_preserves_v29_next_turn() -> None:
    production = R14_EXECUTION_ACCEPTED_DIRECT_TERMINAL_V30_PRODUCTION_MANIFEST
    assert verify_runtime_manifest(production)
    assert production.manifest_version == 30
    operation = "agent_loop.prepare_accepted_direct_terminal.v1"
    route = next(item for item in production.routes if item.operation_id == operation)
    assert route.request_schema_id == "chiplog.execution.direct-terminal-request.v1"
    assert route.result_schema_id == "chiplog.execution.direct-terminal-result.v1"
    loop = next(owner for owner in production.owners if owner.owner_id == "agent_loop")
    assert operation in loop.capability_ids
    assert operation in loop.public_operations
    assert loop.target_ids == (
        "chiplog.capabilities.agent_loop._execution_direct_terminal_process:dispatch",
    )
    assert "agent_loop.prepare_next_execution_turn.v1" in loop.public_operations
    v29 = R14_EXECUTION_ACCEPTED_OPEN_V29_PRODUCTION_MANIFEST
    assert v29.manifest_version == 29
    v29_loop = next(owner for owner in v29.owners if owner.owner_id == "agent_loop")
    assert operation not in v29_loop.public_operations
    assert v29_loop.target_ids == (
        "chiplog.capabilities.agent_loop._execution_accepted_open_process:dispatch",
    )

    with AuthorityBrokerRuntime(
        "tenant",
        1,
        "direct-terminal-v30",
        production,
        b"offline",
        realized_leaves={
            "clock": ProductionClock(),
            "model": HermeticModel(),
            "planning_store": ProductionPlanningStore(),
            "effects_transport": HermeticEffectsProvider(receipt_key=b"fixture", scenarios=()),
        },
    ) as runtime:
        attestation = next(
            item for item in runtime.attest() if item.identity.owner_id == "agent_loop"
        )
    assert "chiplog.capabilities.agent_loop._execution_direct_terminal_process" in (
        attestation.loaded_policy_modules
    )
    assert "chiplog.capabilities.agent_loop._execution_next_turn_process" in (
        attestation.loaded_policy_modules
    )


def test_v31_registers_the_original_resolver_policy_owner_route() -> None:
    production = R14_EXECUTION_ORIGINAL_RESOLVER_POLICY_V31_PRODUCTION_MANIFEST
    evaluation = R14_EXECUTION_ORIGINAL_RESOLVER_POLICY_V31_EVALUATION_MANIFEST
    assert verify_runtime_manifest(production)
    verify_production_evaluation_equivalence(production, evaluation)
    assert production.manifest_version == 31
    route = next(
        item
        for item in production.routes
        if item.operation_id == "agent_loop.adopt_original_resolver_authority_policy.v1"
    )
    assert route.request_schema_id == "chiplog.loop.original-recovery-request.v1"
    loop = next(owner for owner in production.owners if owner.owner_id == "agent_loop")
    assert loop.target_ids == (
        "chiplog.capabilities.agent_loop._original_recovery_process:dispatch",
    )


def test_v32_adds_invocation_route_without_mutating_v31() -> None:
    v31 = R14_EXECUTION_ORIGINAL_RESOLVER_POLICY_V31_PRODUCTION_MANIFEST
    production = R14_EXECUTION_ORIGINAL_RESOLVER_INVOCATION_V32_PRODUCTION_MANIFEST
    evaluation = R14_EXECUTION_ORIGINAL_RESOLVER_INVOCATION_V32_EVALUATION_MANIFEST
    assert verify_runtime_manifest(production)
    verify_production_evaluation_equivalence(production, evaluation)
    assert production.manifest_version == 32
    assert v31.manifest_version == 31
    assert "agent_loop.prepare_original_resolver_invocation.v1" not in {
        route.operation_id for route in v31.routes
    }
    assert "agent_loop.prepare_original_resolver_invocation.v1" in {
        route.operation_id for route in production.routes
    }
    loop = next(owner for owner in production.owners if owner.owner_id == "agent_loop")
    assert "agent_loop.prepare_original_resolver_invocation.v1" in loop.capability_ids


def test_v33_adds_original_call_resolution_route_without_mutating_v32() -> None:
    v32 = R14_EXECUTION_ORIGINAL_RESOLVER_INVOCATION_V32_PRODUCTION_MANIFEST
    production = R14_EXECUTION_ORIGINAL_CALL_RESOLUTION_V33_PRODUCTION_MANIFEST
    evaluation = R14_EXECUTION_ORIGINAL_CALL_RESOLUTION_V33_EVALUATION_MANIFEST
    assert verify_runtime_manifest(production)
    verify_production_evaluation_equivalence(production, evaluation)
    assert production.manifest_version == 33
    assert v32.manifest_version == 32
    operation = "agent_loop.prepare_original_call_resolution.v1"
    assert operation not in {route.operation_id for route in v32.routes}
    route = next(route for route in production.routes if route.operation_id == operation)
    assert route.request_schema_id == "chiplog.loop.original-recovery-request.v1"
    assert route.result_schema_id == "chiplog.loop.original-recovery-result.v1"
    loop = next(owner for owner in production.owners if owner.owner_id == "agent_loop")
    assert operation in loop.capability_ids


def test_v34_registers_semantic_reduction_owner_route() -> None:
    production = R14_EXECUTION_LOOP_SEMANTIC_REDUCTION_V34_PRODUCTION_MANIFEST
    evaluation = R14_EXECUTION_LOOP_SEMANTIC_REDUCTION_V34_EVALUATION_MANIFEST
    assert verify_runtime_manifest(production)
    verify_production_evaluation_equivalence(production, evaluation)
    assert production.manifest_version == 34
    operation = "agent_loop.prepare_loop_semantic_reduction.v1"
    route = next(route for route in production.routes if route.operation_id == operation)
    assert route.request_schema_id == "chiplog.loop.original-recovery-request.v1"
    assert route.result_schema_id == "chiplog.loop.original-recovery-result.v1"
    loop = next(owner for owner in production.owners if owner.owner_id == "agent_loop")
    assert operation in loop.capability_ids


def test_v23_adds_only_bounded_native_accounting_route() -> None:
    production = R14_R17_H1_NATIVE_ONE_CALL_ACCOUNTING_V23_PRODUCTION_MANIFEST
    evaluation = R14_R17_H1_NATIVE_ONE_CALL_ACCOUNTING_V23_EVALUATION_MANIFEST
    assert verify_runtime_manifest(production)
    verify_production_evaluation_equivalence(production, evaluation)
    agent_loop = next(owner for owner in production.owners if owner.owner_id == "agent_loop")
    assert "agent_loop.prepare_native_one_call_cancelled_accounting" in agent_loop.capability_ids
    assert production.manifest_version == 23


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
