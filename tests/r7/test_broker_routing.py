from __future__ import annotations

import asyncio
import time

from chiplog.architecture.r7_runtime import R7_PRODUCTION_MANIFEST
from chiplog.capabilities.planning.r7_boundary import R7PlanningCreateDTO
from chiplog.platform.broker import (
    BrokerSession,
    CallBudget,
    PublicPortCall,
    PublicPortRejected,
    PublicPortSuccess,
)
from chiplog.platform.r7_runtime import AuthorityBrokerRuntime


def _planning_request(runtime: AuthorityBrokerRuntime) -> PublicPortCall:
    payload = R7PlanningCreateDTO(
        tenant_id="tenant-1",
        principal_id="principal-1",
        command_id="command-1",
        intention_line_id="intention-1",
        revision_id="revision-1",
        purpose="Prepare release",
        authority_act_id="act-1",
        trust_reference_bytes=(
            b'{"contour":"CLI","credential_head":"credential-1",'
            b'"freshness_sequence":1,"materialization_head":"materialization-1",'
            b'"peer_credential":"uid:test","session_head":"session-1",'
            b'"source_head":"local","trust_head":"trust-1"}'
        ),
        planning_snapshot_bytes=b'{"commands":[],"head":0,"record_ids":[]}',
    )
    return PublicPortCall(
        operation_id="planning.create_intention_line",
        request_id="request-1",
        caller=BrokerSession(
            tenant_id="tenant-1",
            broker_epoch=1,
            generation_id="generation-1",
            owner_id="broker",
            session_id="broker-session-1",
        ),
        callee=runtime.session("planning"),
        schema_id="chiplog.planning.public.create.v1",
        canonical_payload=payload.canonical_bytes(),
        budget=CallBudget(
            remaining_calls=4,
            remaining_depth=2,
            absolute_deadline_ns=time.monotonic_ns() + 10_000_000_000,
            policy_version=1,
        ),
    )


def test_authenticated_public_dto_routes_to_isolated_planning_owner() -> None:
    with AuthorityBrokerRuntime(
        "tenant-1", 1, "generation-1", R7_PRODUCTION_MANIFEST, b"session-secret"
    ) as runtime:
        result = asyncio.run(runtime.call(_planning_request(runtime)))
        assert isinstance(result, PublicPortSuccess)
        assert result.responder.owner_id == "planning"
        assert b'"disposition":"COMMITTED"' in result.canonical_payload


def test_stale_callee_and_exclusive_resource_hold_reject_before_delivery() -> None:
    with AuthorityBrokerRuntime(
        "tenant-1", 1, "generation-1", R7_PRODUCTION_MANIFEST, b"session-secret"
    ) as runtime:
        request = _planning_request(runtime)
        stale = request.model_copy(
            update={"callee": request.callee.model_copy(update={"session_id": "stale"})}
        )
        result = asyncio.run(runtime.call(stale))
        assert isinstance(result, PublicPortRejected)
        assert result.failure.kind == "STALE_SESSION"

        held = request.model_copy(update={"held_resources": ("sqlite_transaction",)})
        result = asyncio.run(runtime.call(held))
        assert isinstance(result, PublicPortRejected)
        assert result.failure.kind == "PROTOCOL_REJECTED"
