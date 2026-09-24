"""Mounted H1 terminal-work preparation crosses the broker-owner IPC boundary."""

from __future__ import annotations

import asyncio
import time

from chiplog.adapters.driven.effects_hermetic import HermeticEffectsProvider
from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.architecture.r7_runtime import R14_R17_H1_PRODUCTION_MANIFEST
from chiplog.capabilities.agent_loop.post_terminal_contracts import PreparedPostTerminalWork
from chiplog.platform.broker import BrokerSession, CallBudget, PublicPortCall, PublicPortSuccess
from chiplog.platform.r7_leaves import ProductionClock, ProductionPlanningStore
from chiplog.platform.r7_runtime import AuthorityBrokerRuntime
from tests.support.completion_assembly import accepted_completion_fixture


def test_h1_terminal_work_owner_returns_canonical_empty_result_over_ipc() -> None:
    async def exercise() -> None:
        fixture = await accepted_completion_fixture("v3", "empty")
        request = fixture.assembly.terminal_work_request
        leaves = {
            "clock": ProductionClock(),
            "planning_store": ProductionPlanningStore(),
            "model": HermeticModel(),
            "effects_transport": HermeticEffectsProvider(receipt_key=b"fixture", scenarios=()),
        }
        with AuthorityBrokerRuntime(
            "tenant", 1, "terminal-work-h1", R14_R17_H1_PRODUCTION_MANIFEST, b"secret",
            realized_leaves=leaves,
        ) as runtime:
            callee = runtime.session("agent_loop")
            caller = BrokerSession(
                tenant_id=callee.tenant_id,
                broker_epoch=callee.broker_epoch,
                generation_id=callee.generation_id,
                owner_id="broker",
                session_id="broker",
            )
            reply = await runtime.call(
                PublicPortCall(
                    operation_id="agent_loop.prepare_terminal_work",
                    request_id=request.identity.command_id,
                    caller=caller,
                    callee=callee,
                    schema_id="chiplog.agent-loop.prepare-terminal-work.v1",
                    canonical_payload=request.canonical_bytes(),
                    budget=CallBudget(
                        remaining_calls=1,
                        remaining_depth=1,
                        policy_version=1,
                        absolute_deadline_ns=time.monotonic_ns() + 5_000_000_000,
                    ),
                )
            )

        assert isinstance(reply, PublicPortSuccess)
        assert reply.schema_id == "chiplog.agent-loop.prepared-post-terminal-work-result.v1"
        result = PreparedPostTerminalWork.model_validate_json(reply.canonical_payload)
        assert result.canonical_bytes() == reply.canonical_payload
        assert result.ordered_work == ()
        assert result.complete_records == ()

    asyncio.run(exercise())
