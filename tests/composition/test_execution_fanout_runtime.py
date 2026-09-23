"""Actual executable response publication, complete inventory and crash recovery."""

import base64
import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from chiplog.adapters.driven.effects_hermetic import HermeticEffectsProvider
from chiplog.adapters.driven.loop_prompts import OwnedStaticPrompts
from chiplog.capabilities.agent_loop.application import AgentLoop
from chiplog.capabilities.agent_loop.contracts import (
    BudgetPolicy,
    EndpointSelection,
    LoopRejected,
    ToolCall,
)
from chiplog.capabilities.agent_loop.delivery_preparation import (
    Commentary,
    DeliveryCompletion,
    ProposedDelivery,
)
from chiplog.capabilities.agent_loop.execution_contracts import (
    ConsequentialToolCall,
    ExecutionContinue,
    SelfEffectArguments,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Absent
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import InitializedCall
from chiplog.composition.r13_workspace import R13Workspace
from chiplog.composition.r14_execution_fanout_contracts import EXECUTION_FANOUT_OPERATION
from chiplog.composition.r14_execution_runtime import open_execution_runtime
from chiplog.composition.r14_loop_history import (
    read_call_history,
    read_call_inventory,
    read_execution_call_history,
)
from chiplog.composition.r14_loop_store import R14LoopStore
from chiplog.platform._sqlite import PhysicalPublicationCommand, PublicationResult
from chiplog.platform.broker import PublicPortCall, PublicPortResult
from chiplog.platform.r7_runtime import AuthorityBrokerRuntime


def _response(complete: bool = False) -> bytes:
    parsed = (
        DeliveryCompletion(
            tenant="hermetic-tenant",
            run_id="run",
            turn_id="run/turn/1",
            deliveries=(ProposedDelivery(payload=(Commentary(text="Done"),)),),
        )
        if complete
        else ExecutionContinue(
            kind="Continue",
            tool_calls=(
                ToolCall(call_id="plan", tool="propose_planning", text="Plan"),
                ToolCall(call_id="proposal", tool="propose_intent", text="Proposal"),
                ConsequentialToolCall(
                    call_id="effect",
                    tool="request_self_effect",
                    arguments=SelfEffectArguments(
                        payload=b"\xff\x00exact", bundle_members=("b", "a")
                    ),
                ),
            ),
        )
    )
    return json.dumps(parsed.model_dump(mode="json"), indent=2).encode() + b"\n"


@pytest.mark.parametrize("complete", [False, True])
async def test_real_fanout_retains_owner_bytes_and_never_executes_initialized_calls(
    tmp_path: Path, complete: bool
) -> None:
    database = tmp_path / "fanout.sqlite"
    raw = _response(complete)
    async with open_execution_runtime(database, responses=(raw,)) as runtime:
        created = await runtime.create_execution("hermetic-ingress", "run", "Plan", BudgetPolicy())
        started = await runtime.begin_execution("hermetic-ingress", "run", created.head)
        captured = await runtime.capture_execution("hermetic-ingress", "run", started.head)
        sealed = await runtime.seal_execution("hermetic-ingress", "run", captured.head)
        snapshot, inventory, preparations = read_execution_call_history(runtime)
        assert len(preparations) == 1
        evidence = preparations[0]
        assert evidence.proposal.sealed_run == sealed
        assert evidence.request.captured_run == captured
        assert base64.b64decode(evidence.request.request.canonical_response_base64) == raw
        assert len(inventory.ordered_calls) == (0 if complete else 3)
        assert all(isinstance(row.acceptance, InitializedCall) for row in inventory.ordered_calls)
        assert all(isinstance(row.terminal, Absent) for row in inventory.ordered_calls)
        assert sealed.state == "ACTIVE" and sealed.delivery_acceptance is None
        if complete:
            assert sealed.turns[-1].attempts == captured.turns[-1].attempts
            assert sealed.turns[-1].state == "RESPONSE_AVAILABLE"
        else:
            assert sealed.turns[-1].attempts[-1].state == "TERMINAL_ACCEPTED"
            assert {
                row.initialized_record.call.classification for row in inventory.ordered_calls
            } == {"PROPOSAL_ONLY", "CONSEQUENTIAL"}
        provider = runtime._supervisor.runtime()._realized_leaves["effects_transport"]
        assert isinstance(provider, HermeticEffectsProvider)
        assert provider.transfers == ()
        with pytest.raises(LoopRejected):
            await runtime.seal_execution("hermetic-ingress", "run", captured.head)
        assert read_call_inventory(runtime) == inventory
        with sqlite3.connect(database) as connection:
            members = connection.execute(
                "SELECT canonical_bytes FROM records WHERE commit_sequence=? ORDER BY rowid",
                (snapshot.tenant_head,),
            ).fetchall()
        assert len(members) == (2 if complete else 5)
        assert members[0][0] == sealed.canonical_bytes()
    async with open_execution_runtime(database) as reopened:
        assert read_execution_call_history(reopened) == (snapshot, inventory, preparations)
        assert reopened._execution_model.requests == []


async def test_selected_fanout_recovers_all_members_without_owner_reissue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "crash.sqlite"
    async with open_execution_runtime(database, responses=(_response(),)) as runtime:
        created = await runtime.create_execution("hermetic-ingress", "run", "Plan", BudgetPolicy())
        started = await runtime.begin_execution("hermetic-ingress", "run", created.head)
        captured = await runtime.capture_execution("hermetic-ingress", "run", started.head)
        submit = runtime._appender.submit

        async def crash(command: PhysicalPublicationCommand) -> PublicationResult:
            if command.operation_kind == EXECUTION_FANOUT_OPERATION:
                command = replace(command, fault="before_commit")
            return await submit(command)

        with monkeypatch.context() as fault:
            fault.setattr(runtime._appender, "submit", crash)
            with pytest.raises(RuntimeError, match="before commit"):
                await runtime.seal_execution("hermetic-ingress", "run", captured.head)
        pending = runtime._pending()
        assert len(pending) == 1
        selected = runtime._publication(pending[0])
        assert len(selected.records) == 5
        with sqlite3.connect(database) as connection:
            assert connection.execute(
                "SELECT count(*) FROM records WHERE commit_sequence=?",
                (selected.expected_head + 1,),
            ).fetchone() == (0,)
    original = AuthorityBrokerRuntime.call

    async def deny_reissue(
        self: AuthorityBrokerRuntime, request: PublicPortCall
    ) -> PublicPortResult:
        if request.operation_id == "agent_loop.prepare_execution_captured_fan_out":
            raise AssertionError("restart regenerated original fanout")
        return await original(self, request)

    monkeypatch.setattr(AuthorityBrokerRuntime, "call", deny_reissue)
    async with open_execution_runtime(database) as reopened:
        _, inventory, preparations = read_execution_call_history(reopened)
        assert len(preparations) == 1 and len(inventory.ordered_calls) == 3
        assert reopened._pending() == ()
        with sqlite3.connect(database) as connection:
            physical = connection.execute(
                "SELECT record_id, canonical_bytes FROM records "
                "WHERE commit_sequence=? ORDER BY rowid",
                (selected.expected_head + 1,),
            ).fetchall()
        assert physical == [(row.record_id, row.canonical_bytes) for row in selected.records]


async def test_legacy_and_execution_fanouts_share_complete_inventory(tmp_path: Path) -> None:
    legacy = (
        b'{"kind":"Continue","tool_calls":[{"call_id":"p",'
        b'"tool":"propose_planning","text":"Plan"}]}'
    )
    database = tmp_path / "mixed.sqlite"
    async with open_execution_runtime(database, responses=(legacy, _response(), legacy)) as runtime:
        loop = AgentLoop(
            R14LoopStore(runtime),
            runtime._execution_model,
            OwnedStaticPrompts(),
            tenant="hermetic-tenant",
            principal="hermetic-principal",
            origin=EndpointSelection(
                kind="ORIGIN_EXACT",
                ingress_binding_head="hermetic-ingress-v1",
                endpoint_head="hermetic-endpoint-v1",
                endpoint_id="hermetic-local",
                provider="hermetic-local",
                recipient="hermetic-principal",
                canonical_address="local://hermetic-principal",
                credential_binding_head="hermetic-v1",
            ),
            contour_head="hermetic-contour-v1",
            policy_head="hermetic-policy-v1",
            worker_session=runtime.current_worker(),
            planning=runtime,
            workspace=R13Workspace(runtime),
            session=runtime,
        )
        old = await loop.create("old-before", "Plan", BudgetPolicy())
        active = await loop.activate(old.run_id, old.head)
        await loop.step(old.run_id, active.head)
        before = read_call_inventory(runtime)
        assert len(before.ordered_calls) == 1
        created = await runtime.create_execution(
            "hermetic-ingress", "run", "Execute", BudgetPolicy()
        )
        started = await runtime.begin_execution("hermetic-ingress", "run", created.head)
        captured = await runtime.capture_execution("hermetic-ingress", "run", started.head)
        await runtime.seal_execution("hermetic-ingress", "run", captured.head)
        _, combined, executions = read_execution_call_history(runtime)
        assert len(combined.ordered_calls) == 4
        assert (
            executions[0].request.request.cut.predecessor_inventory.ordered_calls
            == before.ordered_calls
        )
        after = await loop.create("old-after", "Another plan", BudgetPolicy())
        active = await loop.activate(after.run_id, after.head)
        await loop.step(after.run_id, active.head)
        _, old_fanouts, _ = read_call_history(runtime)
        assert (
            old_fanouts[-1].request.request.cut.predecessor_inventory.ordered_calls
            == combined.ordered_calls
        )
        final = read_call_inventory(runtime)
        assert len(final.ordered_calls) == 5
    async with open_execution_runtime(database) as reopened:
        assert read_call_inventory(reopened) == final
