"""V31 policy operation is mounted through the authenticated owner broker."""

import time

from chiplog.adapters.driven.effects_hermetic import HermeticEffectsProvider
from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.architecture.r7_runtime import (
    _V33_EXECUTION_OPERATIONS,
    R14_EXECUTION_ORIGINAL_RESOLVER_INVOCATION_V32_PRODUCTION_MANIFEST,
)
from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.execution_recovery_observations import RecoverySourceRecord
from chiplog.capabilities.agent_loop.original_recovery_contracts import (
    AdoptOriginalResolverAuthorityPolicyV1,
    OriginalResolverInvocationV1,
    PreparedOriginalResolverAuthorityPolicyV1,
    PrepareOriginalResolverInvocationV1,
    original_resolver_authority_policy_v1,
)
from chiplog.capabilities.agent_loop.recovery_contracts import (
    Absent,
    OriginalObligationBinding,
    Present,
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
from chiplog.platform.r7_runtime import (
    AuthorityBrokerRuntime,
    OwnerProcessIdentity,
    _owner_module,
)


def test_v33_partition_resolves_to_the_static_original_recovery_process() -> None:
    identity = OwnerProcessIdentity(
        tenant_id="tenant",
        broker_epoch=1,
        owner_id="agent_loop",
        generation_id="generation",
        session_id="session",
        capability_ids=_V33_EXECUTION_OPERATIONS,
    )
    assert _owner_module(identity) == ("chiplog.capabilities.agent_loop._original_recovery_process")


def test_v31_adoption_route_runs_in_isolated_owner_and_preserves_v30_routes() -> None:
    manifest = R14_EXECUTION_ORIGINAL_RESOLVER_INVOCATION_V32_PRODUCTION_MANIFEST
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

        def source(name: str) -> RecoverySourceRecord:
            ref = CallSubjectHead(
                subject_id=name,
                revision=Present(head="record:" + name, fingerprint="a" * 64),
            )
            return RecoverySourceRecord(
                owner="agent_loop",
                subject=ref,
                schema_id="unregistered.test-source.v1",
                canonical_record_bytes=b"{}",
                selected_decision=ref,
                physical_record=ref,
            )

        original = OriginalObligationBinding(
            original_run_id="run",
            original_call_id="call",
            obligation_id="obligation",
            obligation_stream_id="stream",
            obligation_head="head",
            closure_predicate_id="closure",
            closure_predicate_version="v1",
            resolver_id="R14_AUTHENTICATED_ORIGINAL_PRINCIPAL_RESOLVER",
            resolver_version="r14-original-resolver.v1",
            reducer_id="R14_ORIGINAL_EFFECT_EVIDENCE_REDUCER",
            reducer_version="r14-effect-evidence.v1",
            evidence_stream_id="evidence",
            evidence_head=Absent(),
        )
        invocation = OriginalResolverInvocationV1(
            invocation_id="r14-resolver-invocation:tenant:obligation",
            tenant_id="tenant",
            original=original,
            accepted_opening_source=source("opening"),
            accepted_terminal_source=source("terminal"),
            resolver_policy_source=source("resolver-policy"),
            reducer_policy_source=source("reducer-policy"),
            trust_journal_head="trust-head",
            trust_response_fingerprint="b" * 64,
        )
        invocation_request = PrepareOriginalResolverInvocationV1(
            command_id=invocation.invocation_id, invocation=invocation
        )
        invocation_call = call.model_copy(
            update={
                "operation_id": "agent_loop.prepare_original_resolver_invocation.v1",
                "request_id": "prepare-invocation-1",
                "canonical_payload": invocation_request.canonical_bytes(),
            }
        )
        invocation_reply = runtime.call_sync(invocation_call)
        assert isinstance(invocation_reply, PublicPortSuccess), invocation_reply
        rejected = decode_original_recovery_result(
            invocation_reply.schema_id, invocation_reply.canonical_payload
        ).value
        assert rejected.kind == "CALL_PREPARATION_REJECTED_V1"
        assert "exact selected physical record" in rejected.reason

        # The V32 owner advertises the entire inherited V31 route set plus invocation.
        v32_owner = next(
            owner
            for owner in R14_EXECUTION_ORIGINAL_RESOLVER_INVOCATION_V32_PRODUCTION_MANIFEST.owners
            if owner.owner_id == "agent_loop"
        )
        route_ids = {
            route.operation_id
            for route in R14_EXECUTION_ORIGINAL_RESOLVER_INVOCATION_V32_PRODUCTION_MANIFEST.routes
            if route.callee_owner_id == "agent_loop"
        }
        assert set(v32_owner.public_operations) == route_ids
        assert "agent_loop.prepare_accepted_direct_terminal.v1" in route_ids
        assert "agent_loop.prepare_original_resolver_invocation.v1" in route_ids
