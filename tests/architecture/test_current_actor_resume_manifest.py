"""Fresh-actor resume is opt-in; historical route graphs remain unchanged."""

from chiplog.architecture.r7_runtime import (
    R14_EXECUTION_CURRENT_ACTOR_RESUME_V40_EVALUATION_MANIFEST,
    R14_EXECUTION_CURRENT_ACTOR_RESUME_V40_PRODUCTION_MANIFEST,
    R14_EXECUTION_OBLIGATION_RESUME_V37_PRODUCTION_MANIFEST,
    verify_production_evaluation_equivalence,
    verify_runtime_manifest,
)


def test_v40_registers_only_the_exact_current_actor_resume_extension() -> None:
    production = R14_EXECUTION_CURRENT_ACTOR_RESUME_V40_PRODUCTION_MANIFEST
    previous = R14_EXECUTION_OBLIGATION_RESUME_V37_PRODUCTION_MANIFEST
    operation = "agent_loop.prepare_execution_resume_current_actor.v2"
    assert verify_runtime_manifest(production)
    verify_production_evaluation_equivalence(
        production,
        R14_EXECUTION_CURRENT_ACTOR_RESUME_V40_EVALUATION_MANIFEST,
    )
    assert production.manifest_version == 40
    assert set(production.routes) - set(previous.routes) == {
        next(route for route in production.routes if route.operation_id == operation),
    }
    assert set(previous.routes) <= set(production.routes)
    owner = next(owner for owner in production.owners if owner.owner_id == "agent_loop")
    old_owner = next(owner for owner in previous.owners if owner.owner_id == "agent_loop")
    assert set(owner.capability_ids) - set(old_owner.capability_ids) == {operation}
    assert set(owner.public_operations) - set(old_owner.public_operations) == {operation}
    assert owner.target_ids == old_owner.target_ids
