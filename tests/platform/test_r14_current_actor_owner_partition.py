"""Exact V40 launcher partition and genuine isolated owner route admission."""

import time
from dataclasses import replace

import pytest

from chiplog.adapters.driven.effects_hermetic import HermeticEffectsProvider
from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.architecture.r7_runtime import (
    R14_EXECUTION_CURRENT_ACTOR_RESUME_V40_PRODUCTION_MANIFEST,
    R14_EXECUTION_OBLIGATION_RESUME_V37_PRODUCTION_MANIFEST,
)
from chiplog.capabilities.agent_loop.execution_recovery_contracts import ExecutionRecoveryClassified
from chiplog.capabilities.agent_loop.execution_recovery_preparation import (
    CURRENT_ACTOR_RESUME_DISPOSITION,
)
from chiplog.capabilities.agent_loop.recovery_wire_contracts import (
    EXECUTION_RECOVERY_REQUEST_SCHEMA,
    EXECUTION_RECOVERY_RESULT_SCHEMA,
    decode_execution_recovery_result,
)
from chiplog.platform.broker import BrokerSession, CallBudget, PublicPortCall, PublicPortSuccess
from chiplog.platform.r7_leaves import ProductionClock, ProductionPlanningStore
from chiplog.platform.r7_runtime import (
    AuthorityBrokerRuntime,
    OwnerProcessFailure,
    OwnerProcessIdentity,
    _owner_module,
)
from tests.capabilities.agent_loop.test_execution_recovery_preparation import _resume_request


def test_v40_owner_partition_is_exact_and_preserves_v37() -> None:
    for manifest in (
        R14_EXECUTION_CURRENT_ACTOR_RESUME_V40_PRODUCTION_MANIFEST,
        R14_EXECUTION_OBLIGATION_RESUME_V37_PRODUCTION_MANIFEST,
    ):
        owner = next(row for row in manifest.owners if row.owner_id == "agent_loop")
        identity = OwnerProcessIdentity(
            "tenant", 1, "agent_loop", "generation", "session", owner.capability_ids
        )
        assert _owner_module(identity) == (
            "chiplog.capabilities.agent_loop._original_recovery_process"
        )
        foreign = replace(
            identity,
            capability_ids=tuple(
                sorted(
                    (
                        *identity.capability_ids,
                        "agent_loop.foreign_authority",
                    )
                )
            ),
        )
        with pytest.raises(OwnerProcessFailure, match="unknown agent-loop capability partition"):
            _owner_module(foreign)


async def test_v40_spawn_routes_current_actor_request_to_real_owner() -> None:
    # Cancellation-only input must reach the owner and be classified HOLD;
    # broker success is transport success, never permission to resume this input.
    request = await _resume_request()
    tenant = request.cut.tenant_id
    with AuthorityBrokerRuntime(
        tenant,
        1,
        "generation",
        R14_EXECUTION_CURRENT_ACTOR_RESUME_V40_PRODUCTION_MANIFEST,
        b"fixture-secret",
        realized_leaves={
            "clock": ProductionClock(),
            "model": HermeticModel(),
            "planning_store": ProductionPlanningStore(),
            "effects_transport": HermeticEffectsProvider(receipt_key=b"fixture", scenarios=()),
        },
    ) as runtime:
        attestation = next(row for row in runtime.attest() if row.identity.owner_id == "agent_loop")
        assert attestation.process_id != attestation.parent_process_id
        assert attestation.target_ids == (
            "chiplog.capabilities.agent_loop._original_recovery_process:dispatch",
        )
        callee = runtime.session("agent_loop")
        request = request.model_copy(
            update={
                "disposition_version": CURRENT_ACTOR_RESUME_DISPOSITION,
                "fence": request.fence.model_copy(
                    update={
                        "worker_session_id": (
                            f"{callee.broker_epoch}:{callee.generation_id}:{callee.session_id}"
                        ),
                        "runtime_generation": callee.generation_id,
                    }
                ),
            }
        )
        call = PublicPortCall(
            operation_id="agent_loop.prepare_execution_resume_current_actor.v2",
            request_id="current-actor-scope-negative",
            caller=BrokerSession(
                tenant_id=tenant,
                broker_epoch=1,
                generation_id="generation",
                owner_id="broker",
                session_id="broker",
            ),
            callee=callee,
            schema_id=EXECUTION_RECOVERY_REQUEST_SCHEMA,
            canonical_payload=request.canonical_bytes(),
            budget=CallBudget(
                remaining_calls=1,
                remaining_depth=1,
                policy_version=1,
                absolute_deadline_ns=time.monotonic_ns() + 5_000_000_000,
            ),
        )
        reply = runtime.call_sync(call)
        assert isinstance(reply, PublicPortSuccess), reply
        assert reply.schema_id == EXECUTION_RECOVERY_RESULT_SCHEMA
        result = decode_execution_recovery_result(reply.schema_id, reply.canonical_payload).value
        assert isinstance(result, ExecutionRecoveryClassified)
        assert result.disposition == "RECOVERY_HOLD"
        assert result.source_request_fingerprint == request.digest()
