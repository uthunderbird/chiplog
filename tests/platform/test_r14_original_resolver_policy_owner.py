"""V31 policy operation is mounted through the authenticated owner broker."""

import time

from chiplog.adapters.driven.effects_hermetic import HermeticEffectsProvider
from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.architecture.r7_runtime import (
    R14_EXECUTION_ORIGINAL_RESOLVER_POLICY_V31_PRODUCTION_MANIFEST,
)
from chiplog.capabilities.agent_loop.original_recovery_contracts import (
    AdoptOriginalResolverAuthorityPolicyV1,
    PreparedOriginalResolverAuthorityPolicyV1,
    original_resolver_authority_policy_v1,
)
from chiplog.capabilities.agent_loop.recovery_wire_contracts import (
    ORIGINAL_RECOVERY_REQUEST_SCHEMA,
    ORIGINAL_RECOVERY_RESULT_SCHEMA,
    decode_original_recovery_result,
)
from chiplog.platform.broker import (
    BrokerSession,
    CallBudget,
    PublicPortCall,
    PublicPortSuccess,
)
from chiplog.platform.r7_leaves import ProductionClock, ProductionPlanningStore
from chiplog.platform.r7_runtime import AuthorityBrokerRuntime


def test_v31_adoption_route_runs_in_isolated_owner_and_preserves_v30_routes() -> None:
    manifest = R14_EXECUTION_ORIGINAL_RESOLVER_POLICY_V31_PRODUCTION_MANIFEST
    with AuthorityBrokerRuntime(
        "tenant",
        1,
        "generation",
        manifest,
        b"secret",
        realized_leaves={
            "clock": ProductionClock(),
            "model": HermeticModel(),
            "planning_store": ProductionPlanningStore(),
            "effects_transport": HermeticEffectsProvider(receipt_key=b"fixture", scenarios=()),
        },
    ) as runtime:
        attest = next(item for item in runtime.attest() if item.identity.owner_id == "agent_loop")
        assert attest.target_ids == (
            "chiplog.capabilities.agent_loop._original_recovery_process:dispatch",
        )
        assert attest.process_id != attest.parent_process_id

        callee = runtime.session("agent_loop")
        request = AdoptOriginalResolverAuthorityPolicyV1(
            command_id="adopt-original-resolver-authority:tenant",
            policy=original_resolver_authority_policy_v1("tenant"),
        )
        call = PublicPortCall(
            operation_id="agent_loop.adopt_original_resolver_authority_policy.v1",
            request_id="adopt-policy-1",
            caller=BrokerSession(
                tenant_id="tenant",
                broker_epoch=1,
                generation_id="generation",
                owner_id="broker",
                session_id="broker",
            ),
            callee=callee,
            schema_id=ORIGINAL_RECOVERY_REQUEST_SCHEMA,
            canonical_payload=request.canonical_bytes(),
            budget=CallBudget(
                remaining_calls=1,
                remaining_depth=1,
                absolute_deadline_ns=time.monotonic_ns() + 5_000_000_000,
                policy_version=1,
            ),
        )
        result = runtime.call_sync(call)
        assert isinstance(result, PublicPortSuccess), result
        assert result.schema_id == ORIGINAL_RECOVERY_RESULT_SCHEMA
        decoded = decode_original_recovery_result(result.schema_id, result.canonical_payload).value
        assert isinstance(decoded, PreparedOriginalResolverAuthorityPolicyV1)
        assert decoded.policy == request.policy

        # The V31 owner advertises the entire inherited V30 route set.
        v30 = next(
            owner
            for owner in R14_EXECUTION_ORIGINAL_RESOLVER_POLICY_V31_PRODUCTION_MANIFEST.owners
            if owner.owner_id == "agent_loop"
        )
        route_ids = {
            route.operation_id
            for route in R14_EXECUTION_ORIGINAL_RESOLVER_POLICY_V31_PRODUCTION_MANIFEST.routes
            if route.callee_owner_id == "agent_loop"
        }
        assert set(v30.public_operations) == route_ids
        assert "agent_loop.prepare_accepted_direct_terminal.v1" in route_ids
