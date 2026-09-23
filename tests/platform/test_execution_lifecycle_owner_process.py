"""Real isolated initialization exchange, without journal/publication claims."""

import os
import time

from pydantic import TypeAdapter

from chiplog.adapters.driven.effects_hermetic import HermeticEffectsProvider
from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.architecture.r7_runtime import R14_EXECUTION_LIFECYCLE_PRODUCTION_MANIFEST
from chiplog.capabilities.agent_loop.execution_transition_contracts import (
    ActivateExecutionRun,
    CreateExecutionRun,
    ExecutionTransitionProposal,
    ExecutionTransitionRequest,
    ExecutionTransitionResult,
    StartInitialExecutionTurn,
)
from chiplog.platform.broker import (
    BrokerSession,
    CallBudget,
    PublicPortCall,
    PublicPortRejected,
    PublicPortSuccess,
)
from chiplog.platform.r7_leaves import ProductionClock, ProductionPlanningStore
from chiplog.platform.r7_runtime import AuthorityBrokerRuntime
from tests.support.execution_fan_out import fixture


async def test_execution_initialization_isolated_owner_and_protocol_rejection() -> None:
    seed = (await fixture()).captured_run
    manifest = R14_EXECUTION_LIFECYCLE_PRODUCTION_MANIFEST
    leaves = {
        "clock": ProductionClock(),
        "planning_store": ProductionPlanningStore(),
        "model": HermeticModel(),
        "effects_transport": HermeticEffectsProvider(receipt_key=b"fixture", scenarios=()),
    }
    with AuthorityBrokerRuntime(
        "tenant", 1, "generation", manifest, b"secret", realized_leaves=leaves
    ) as runtime:
        owner = next(item for item in runtime.attest() if item.identity.owner_id == "agent_loop")
        assert owner.process_id != os.getpid()
        callee = runtime.session("agent_loop")
        caller = BrokerSession(
            tenant_id=callee.tenant_id,
            broker_epoch=callee.broker_epoch,
            generation_id=callee.generation_id,
            owner_id="broker",
            session_id="broker",
        )

        async def send(command: ExecutionTransitionRequest, *, malformed: bool = False) -> object:
            raw = command.canonical_bytes()
            if malformed:
                raw = raw[:-1] + b',"authorized":true}'
            return await runtime.call(
                PublicPortCall(
                    operation_id="agent_loop.prepare_execution_transition",
                    request_id=command.command_id,
                    caller=caller,
                    callee=callee,
                    schema_id="chiplog.execution.transition-request.v2",
                    canonical_payload=raw,
                    budget=CallBudget(
                        remaining_calls=1,
                        remaining_depth=1,
                        policy_version=1,
                        absolute_deadline_ns=time.monotonic_ns() + 5_000_000_000,
                    ),
                )
            )

        command = CreateExecutionRun(
            command_id="create",
            tenant=seed.tenant,
            principal=seed.principal,
            run_id=seed.run_id,
            prompt=seed.prompt,
            policy=seed.policy,
            origin=seed.origin,
            contour_head=seed.contour_head,
            policy_head=seed.policy_head,
            worker_session=f"{callee.broker_epoch}:{callee.generation_id}:{callee.session_id}",
        )
        rejected = await send(command, malformed=True)
        assert isinstance(rejected, PublicPortRejected)
        adapter: TypeAdapter[ExecutionTransitionResult] = TypeAdapter(ExecutionTransitionResult)
        created_reply = await send(command)
        assert isinstance(created_reply, PublicPortSuccess)
        assert created_reply.schema_id == "chiplog.execution.transition-result.v2"
        created = adapter.validate_json(created_reply.canonical_payload)
        assert isinstance(created, ExecutionTransitionProposal)
        assert created.source_request_fingerprint == command.digest()
        assert created.run.state == "CREATED" and created.run.turns == ()
        assert created.run.origin == command.origin
        active_reply = await send(ActivateExecutionRun(command_id="activate", run=created.run))
        assert isinstance(active_reply, PublicPortSuccess)
        active = adapter.validate_json(active_reply.canonical_payload)
        assert isinstance(active, ExecutionTransitionProposal)
        first_reply = await send(StartInitialExecutionTurn(command_id="first", run=active.run))
        assert isinstance(first_reply, PublicPortSuccess)
        first = adapter.validate_json(first_reply.canonical_payload)
        assert isinstance(first, ExecutionTransitionProposal)
        assert first.run.turns[-1].state == "PREPARING"
        assert first.run.turns[-1].attempts == ()
        assert runtime.session("agent_loop") == callee
