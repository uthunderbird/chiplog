import hashlib
import json
import time
from pathlib import Path
from typing import Literal

import pytest

from chiplog.adapters.driven.effects_hermetic import HermeticEffectsProvider
from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy, LoopHold, LoopRejected
from chiplog.capabilities.effects.client_effect_contracts import NativeEffectIntentV1
from chiplog.composition.postgres_effects import (
    PostgresEffectsBinding,
    adopt_effect,
    append_effect_receipt,
    commit_effect_send,
    current_effect_result,
    emit_effect,
    preview_effect,
    reconcile_effect,
    resolve_original_effect,
)
from chiplog.composition.postgres_execution import PostgresExecutionRuntime
from chiplog.composition.postgres_planning import PostgresPlanningRuntime
from chiplog.platform.deployment_gate import CurrentEntitlement, DeploymentGateRequest
from chiplog.platform.postgres import PostgresServer
from tests.composition.test_postgres_execution import SECRET, origin
from tests.composition.test_postgres_planning import verifier
from tests.support.deployment_gate import entitlement, request, signature

PEER = "local-cli"
IDENTITY = dict(principal_id="hermetic-principal", credential_id="c", session_id="s")
KEY = b"registered-pg-provider-receipt-key"


async def setup_effect(
    server: PostgresServer,
    directory: Path,
    scenario: Literal["CONFIRM", "LOST_RESPONSE_AFTER_EFFECT"],
) -> tuple[
    PostgresExecutionRuntime,
    PostgresEffectsBinding,
    str,
    NativeEffectIntentV1,
    CurrentEntitlement,
    DeploymentGateRequest,
]:
    database = server.user_database("t")
    database.provision()
    await PostgresPlanningRuntime(database, "t", SECRET).bootstrap(
        database_instance_id="db", token="token", **IDENTITY
    )
    binding = PostgresEffectsBinding(
        HermeticEffectsProvider(
            receipt_key=KEY,
            scenarios=(scenario,),
            late_receipt_scenarios=(("COMPATIBLE", "RIVAL"),),
            journal_path=directory / "provider.jsonl",
            observation_journal_path=directory / "observations.jsonl",
        ),
        KEY,
    )
    response = json.dumps(
        {
            "kind": "Continue",
            "tool_calls": [
                {
                    "call_id": "effect",
                    "tool": "request_self_effect",
                    "arguments": {"payload": "aGVsbG8=", "bundle_members": ["m"]},
                }
            ],
        }
    ).encode()
    runtime = PostgresExecutionRuntime(
        database,
        "t",
        SECRET,
        **IDENTITY,
        model=HermeticModel((response,)),
        origin=origin(),
        effects_binding=binding,
        authenticate_entitlement=verifier,
    )
    created = await runtime.create_execution(PEER, "run", "Send hello", BudgetPolicy())
    started = await runtime.begin_execution(PEER, "run", created.head)
    captured = await runtime.capture_execution(PEER, "run", started.head)
    await runtime.seal_execution(PEER, "run", captured.head)
    call_id = runtime.initialized_calls()[0].original_call_id
    preview = await preview_effect(runtime, binding, PEER, call_id, time.time_ns() + 10**12)
    intent = await adopt_effect(runtime, binding, PEER, preview.head, "explicit-adoption")
    now = time.time_ns()
    current = entitlement()
    current = current.model_copy(
        update={
            "freshness_leases": (("evidence", now + 10**12),),
            "bounds": current.bounds.model_copy(
                update={
                    "capability_id": "effects.first_send",
                    "starts_ns": now - 1,
                    "expires_ns": now + 10**12,
                }
            ),
        }
    )
    gate_request = request(current).model_copy(
        update={
            "surface_id": "effects.first_send",
            "payload_digest": hashlib.sha256(intent.canonical_bytes()).hexdigest(),
        }
    )
    return runtime, binding, call_id, intent, current, gate_request


@pytest.mark.parametrize("scenario", ["CONFIRM", "LOST_RESPONSE_AFTER_EFFECT"])
async def test_original_effect_closes_with_fresh_resolver_no_repeat_send(
    postgres_server: PostgresServer,
    tmp_path: Path,
    scenario: Literal["CONFIRM", "LOST_RESPONSE_AFTER_EFFECT"],
) -> None:
    runtime, binding, call_id, intent, current, gate_request = await setup_effect(
        postgres_server, tmp_path, scenario
    )
    with pytest.raises(PermissionError, match="HOLD"):
        await commit_effect_send(runtime, binding, PEER, call_id, gate_request)
    assert binding.provider.transfers == ()
    runtime.import_entitlement(current, signature(current))
    send = await commit_effect_send(runtime, binding, PEER, call_id, gate_request)
    claim = await emit_effect(runtime, binding, PEER, call_id)
    assert len(binding.provider.transfers) == 1
    opened = runtime.execution_record("run")
    assert len(opened.original_obligations) == 1
    with pytest.raises(LoopHold):
        await runtime.continue_execution(PEER, "run", opened.head)
    with pytest.raises(LoopRejected, match="resolver"):
        await runtime.record_execution_call_outcome(
            PEER,
            call_id,
            runtime.call_state(call_id).head,
            outcome="SUCCEEDED",
            result=b"caller flag",
        )
    binding = PostgresEffectsBinding(
        HermeticEffectsProvider(
            receipt_key=KEY,
            scenarios=(scenario,),
            journal_path=tmp_path / "provider.jsonl",
            observation_journal_path=tmp_path / "observations.jsonl",
        ),
        KEY,
    )
    assert len(binding.provider.transfers) == 1
    fresh = PostgresExecutionRuntime(
        runtime.database,
        "t",
        SECRET,
        **IDENTITY,
        model=HermeticModel(),
        origin=origin(),
        effects_binding=binding,
        authenticate_entitlement=verifier,
    )
    assert await emit_effect(fresh, binding, PEER, call_id) == claim
    await reconcile_effect(fresh, binding, PEER, call_id)
    closure = await resolve_original_effect(fresh, binding, PEER, call_id)
    assert closure.outcome == "CONFIRMED"
    assert json.loads(current_effect_result(fresh, binding, fresh.snapshot(), call_id)) == {
        "effect_outcome": "CONFIRMED"
    }
    continued = await fresh.continue_execution(PEER, "run", fresh.execution_record("run").head)
    assert len(continued.turns) == 2
    assert continued.original_obligations == opened.original_obligations
    assert len(binding.provider.transfers) == 1
    assert send.intent_head == intent.head


async def test_late_evidence_requires_current_join_and_rival_holds_every_old_seal(
    postgres_server: PostgresServer,
    tmp_path: Path,
) -> None:
    runtime, binding, call_id, _, current, gate_request = await setup_effect(
        postgres_server, tmp_path, "CONFIRM"
    )
    runtime.import_entitlement(current, signature(current))
    send = await commit_effect_send(runtime, binding, PEER, call_id, gate_request)
    await emit_effect(runtime, binding, PEER, call_id)
    await resolve_original_effect(runtime, binding, PEER, call_id)
    # A later closed seal cannot hide contradictory current evidence for the first.
    response = json.dumps(
        {
            "kind": "Continue",
            "tool_calls": [{"call_id": "proposal", "tool": "propose_intent", "text": "next"}],
        }
    ).encode()
    runtime = PostgresExecutionRuntime(
        runtime.database,
        "t",
        SECRET,
        **IDENTITY,
        model=HermeticModel((response,)),
        origin=origin(),
        effects_binding=binding,
        authenticate_entitlement=verifier,
    )
    continued = await runtime.continue_execution(PEER, "run", runtime.execution_record("run").head)
    captured = await runtime.capture_execution(PEER, "run", continued.head)
    await runtime.seal_execution(PEER, "run", captured.head)
    compatible = binding.provider.observe_late_receipt(send.transmission.transmission.subject_id)
    await append_effect_receipt(runtime, binding, PEER, call_id, compatible)
    assert b"CONFIRMED" in current_effect_result(runtime, binding, runtime.snapshot(), call_id)
    await resolve_original_effect(runtime, binding, PEER, call_id)
    rival = binding.provider.observe_late_receipt(send.transmission.transmission.subject_id)
    await append_effect_receipt(runtime, binding, PEER, call_id, rival)
    with pytest.raises(LoopHold):
        await resolve_original_effect(runtime, binding, PEER, call_id)
    assert runtime.call_state(call_id).state == "TERMINAL"
    with pytest.raises(LoopHold):
        await runtime.continue_execution(PEER, "run", runtime.execution_record("run").head)
    assert len(binding.provider.transfers) == 1


async def test_claim_revalidates_live_gate_after_send_commit(
    postgres_server: PostgresServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, binding, call_id, _, current, gate_request = await setup_effect(
        postgres_server, tmp_path, "CONFIRM"
    )
    current = current.model_copy(
        update={"bounds": current.bounds.model_copy(update={"expires_ns": time.time_ns() + 10**9})}
    )
    gate_request = gate_request.model_copy(update={"bounds": current.bounds})
    runtime.import_entitlement(current, signature(current))
    await commit_effect_send(runtime, binding, PEER, call_id, gate_request)
    before = runtime.snapshot()
    monkeypatch.setattr(time, "time_ns", lambda: current.bounds.expires_ns)
    with pytest.raises((PermissionError, LoopHold)):
        await emit_effect(runtime, binding, PEER, call_id)
    assert runtime.snapshot() == before
    assert binding.provider.transfers == ()


async def test_claim_revalidates_revoked_actor(
    postgres_server: PostgresServer,
    tmp_path: Path,
) -> None:
    runtime, binding, call_id, _, current, gate_request = await setup_effect(
        postgres_server, tmp_path, "CONFIRM"
    )
    runtime.import_entitlement(current, signature(current))
    await commit_effect_send(runtime, binding, PEER, call_id, gate_request)
    runtime.revoke("credential:1")
    with pytest.raises(PermissionError, match="authenticated"):
        await emit_effect(runtime, binding, PEER, call_id)
    assert binding.provider.transfers == ()


async def test_bounded_worker_reopens_claimed_tool_work_without_resend(
    postgres_server: PostgresServer,
    tmp_path: Path,
) -> None:
    from chiplog.capabilities.effects.effect_work_contracts import EffectWorkScan
    from chiplog.composition.postgres_effect_work import discover_effect_work, step_effect_work

    runtime, binding, call_id, _, current, gate_request = await setup_effect(
        postgres_server, tmp_path, "LOST_RESPONSE_AFTER_EFFECT"
    )
    runtime.import_entitlement(current, signature(current))
    await commit_effect_send(runtime, binding, PEER, call_id, gate_request)
    scan = EffectWorkScan(tenant_id="t", principal_id="hermetic-principal", limit=1)
    pending = await discover_effect_work(runtime, binding, PEER, scan)
    assert pending.items[0].kind == "PENDING_SEND"
    assert (
        await step_effect_work(runtime, binding, PEER, pending.items[0])
    ).disposition == "EMITTED"
    reopened = PostgresExecutionRuntime(
        runtime.database,
        "t",
        SECRET,
        **IDENTITY,
        model=HermeticModel(),
        origin=origin(),
        effects_binding=binding,
        authenticate_entitlement=verifier,
    )
    claimed = await discover_effect_work(reopened, binding, PEER, scan)
    assert claimed.items[0].kind == "RECONCILE"
    assert (
        await step_effect_work(reopened, binding, PEER, claimed.items[0])
    ).disposition == "RECONCILED"
    assert len(binding.provider.transfers) == 1
    # Evidence retention did not silently close an original obligation.
    assert (await discover_effect_work(reopened, binding, PEER, scan)).items
    await resolve_original_effect(reopened, binding, PEER, call_id)
    assert not (await discover_effect_work(reopened, binding, PEER, scan)).items


async def test_compensation_has_separate_origin_fresh_adoption_and_current_original_evidence(
    postgres_server: PostgresServer,
    tmp_path: Path,
) -> None:
    from chiplog.capabilities.effects.action_lifecycle_contracts import CompensationActionProposal
    from chiplog.capabilities.effects.effect_work_contracts import CompensationEffectRequest
    from chiplog.composition.postgres_effect_work import adopt_compensation, preview_action
    from chiplog.composition.postgres_effects import evidence_inventory_head, exact
    from chiplog.platform.postgres_gate import canonical

    runtime, binding, call_id, original, current, gate_request = await setup_effect(
        postgres_server, tmp_path, "LOST_RESPONSE_AFTER_EFFECT"
    )
    runtime.import_entitlement(current, signature(current))
    await commit_effect_send(runtime, binding, PEER, call_id, gate_request)
    await emit_effect(runtime, binding, PEER, call_id)
    old_state = runtime.call_state(call_id)
    proposal = CompensationActionProposal(
        tenant_id="t",
        principal_id="hermetic-principal",
        intent_id="compensation",
        payload=b"compensate",
        bundle_members=("compensated",),
        target=binding.target(runtime),
        expires_at_ns=time.time_ns() + 10**12,
        original_intent=exact(original.intent_id, original.canonical_bytes()),
        original_stream_id=call_id,
        original_evidence_head=evidence_inventory_head(runtime, runtime.snapshot(), call_id),
    )
    preview = await preview_action(runtime, binding, PEER, proposal)
    adopted = CompensationEffectRequest.model_validate(
        {
            **proposal.model_dump(),
            "adopted_preview": exact(preview.head, preview.canonical_bytes()),
            "authority_act_id": "new-compensation-adoption",
        }
    )
    comp_current = current.model_copy(
        update={
            "entitlement_head": "compensation-send",
            "bounds": current.bounds.model_copy(update={"cap": 4}),
        }
    )
    runtime.import_entitlement(
        comp_current, signature(comp_current), previous=canonical(current.model_dump(mode="json"))
    )
    binding = PostgresEffectsBinding(
        HermeticEffectsProvider(
            receipt_key=KEY,
            scenarios=("LOST_RESPONSE_AFTER_EFFECT", "CONFIRM"),
            journal_path=tmp_path / "provider.jsonl",
            observation_journal_path=tmp_path / "observations.jsonl",
        ),
        KEY,
    )
    authorize = request(comp_current, "compensation-adopt").model_copy(
        update={
            "operation_id": "compensation-adopt",
            "surface_id": "effects.first_send",
            "payload_digest": hashlib.sha256(adopted.canonical_bytes()).hexdigest(),
        }
    )
    value = await adopt_compensation(runtime, binding, PEER, adopted, authorize)
    assert value.request.original_stream_id == call_id
    assert runtime.call_state(call_id) == old_state
    comp_gate = request(comp_current, "compensation-send").model_copy(
        update={
            "surface_id": "effects.first_send",
            "payload_digest": hashlib.sha256(value.canonical_bytes()).hexdigest(),
        }
    )
    await commit_effect_send(runtime, binding, PEER, adopted.intent_id, comp_gate)
    from chiplog.capabilities.effects.effect_work_contracts import EffectWorkScan
    from chiplog.composition.postgres_effect_work import discover_effect_work, step_effect_work

    scan = EffectWorkScan(tenant_id="t", principal_id="hermetic-principal", limit=1)
    first_page = await discover_effect_work(runtime, binding, PEER, scan)
    assert first_page.items[0].stream_id == call_id and first_page.has_more
    second_page = await discover_effect_work(
        runtime, binding, PEER, scan.model_copy(update={"after_send": first_page.next_send})
    )
    assert second_page.items[0].stream_id == adopted.intent_id
    await step_effect_work(runtime, binding, PEER, second_page.items[0])
    assert len(binding.provider.transfers) == 2
    assert runtime.call_state(call_id) == old_state
    assert (
        "original_call_id"
        not in runtime.snapshot().batches[-1].records[-1].canonical_bytes.decode()
    )
    # A second distinct fresh compensation cannot send once original evidence advances.
    next_proposal = proposal.model_copy(update={"intent_id": "second-compensation"})
    next_preview = await preview_action(runtime, binding, PEER, next_proposal)
    next_request = CompensationEffectRequest.model_validate(
        {
            **next_proposal.model_dump(),
            "adopted_preview": exact(next_preview.head, next_preview.canonical_bytes()),
            "authority_act_id": "second-new-adoption",
        }
    )
    next_gate = comp_gate.model_copy(
        update={
            "operation_id": "second-adopt",
            "payload_digest": hashlib.sha256(next_request.canonical_bytes()).hexdigest(),
        }
    )
    await adopt_compensation(runtime, binding, PEER, next_request, next_gate)
    await reconcile_effect(runtime, binding, PEER, call_id)
    with pytest.raises(LoopHold, match="evidence changed"):
        await commit_effect_send(runtime, binding, PEER, next_request.intent_id, comp_gate)
    assert len(binding.provider.transfers) == 2


async def test_plan_and_effect_share_transaction_and_keep_non_tool_origin(
    postgres_server: PostgresServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from chiplog.capabilities.effects.action_lifecycle_contracts import PlanActionProposal
    from chiplog.capabilities.effects.effect_work_contracts import EffectWorkScan, PlanEffectRequest
    from chiplog.composition.postgres_effect_work import (
        discover_effect_work,
        preview_action,
        publish_plan_effect,
        step_effect_work,
    )
    from chiplog.composition.postgres_effects import exact
    from chiplog.composition.r8 import command_bytes
    from chiplog.platform.postgres import UserDatabase
    from chiplog.platform.postgres_gate import canonical
    from tests.support.r8_journey import command

    runtime, binding, _, _, current, _ = await setup_effect(postgres_server, tmp_path, "CONFIRM")
    from chiplog.capabilities.agent_loop.execution_client_fanout import seal

    # An action may precede a subsequently initialized tool with a colliding identity.
    response = json.dumps(
        {
            "kind": "Continue",
            "tool_calls": [
                {
                    "call_id": "future",
                    "tool": "request_self_effect",
                    "arguments": {"payload": "aGVsbG8=", "bundle_members": ["m"]},
                }
            ],
        }
    ).encode()
    runtime = PostgresExecutionRuntime(
        runtime.database,
        "t",
        SECRET,
        **IDENTITY,
        model=HermeticModel((response,)),
        origin=origin(),
        effects_binding=binding,
        authenticate_entitlement=verifier,
    )
    future = await runtime.create_execution(PEER, "future", "future action", BudgetPolicy())
    future = await runtime.begin_execution(PEER, "future", future.head)
    future = await runtime.capture_execution(PEER, "future", future.head)
    future_call_id = seal(future)[2][0].original_call_id
    value = command("atomic")
    planning_current = current.model_copy(
        update={
            "entitlement_head": "planning-atomic",
            "bounds": current.bounds.model_copy(
                update={"capability_id": "planning.create_intention_line", "cap": 3}
            ),
        }
    )
    runtime.import_entitlement(planning_current, signature(planning_current))
    planning_gate = request(planning_current, value.command_id.value).model_copy(
        update={
            "surface_id": "planning.create",
            "payload_digest": hashlib.sha256(command_bytes(value)).hexdigest(),
        }
    )
    proposal = PlanActionProposal(
        tenant_id="t",
        principal_id="hermetic-principal",
        intent_id=future_call_id,
        payload=b"plan action",
        bundle_members=("action",),
        target=binding.target(runtime),
        expires_at_ns=time.time_ns() + 10**12,
        planning_command_schema="chiplog.planning.public.create.v2",
        planning_command_bytes=command_bytes(value),
        expected_planning_revision=None,
    )
    preview = await preview_action(runtime, binding, PEER, proposal)
    adopted = PlanEffectRequest.model_validate(
        {
            **proposal.model_dump(),
            "adopted_preview": exact(preview.head, preview.canonical_bytes()),
            "authority_act_id": value.authority_act_id,
        }
    )
    before = runtime.snapshot()
    original_append = UserDatabase.append

    def fail_companion(
        self: UserDatabase, connection: object, operation: str, *args: object
    ) -> object:
        if operation == "effects.plan-intent":
            raise RuntimeError("companion fault after Planning append")
        return original_append(self, connection, operation, *args)  # type: ignore[arg-type]

    with monkeypatch.context() as scoped:
        scoped.setattr(UserDatabase, "append", fail_companion)
        with pytest.raises(RuntimeError, match="companion fault"):
            await publish_plan_effect(runtime, binding, PEER, value, adopted, planning_gate)
    assert runtime.snapshot() == before
    intent = await publish_plan_effect(runtime, binding, PEER, value, adopted, planning_gate)
    committed = runtime.snapshot()
    assert [batch.operation for batch in committed.batches[-2:]] == [
        "planning.create_intention_line",
        "effects.plan-intent",
    ]
    assert len(committed.batches[-2].records) == 5
    assert intent.planning_revision.subject_id == value.revision_id.value
    assert (
        await publish_plan_effect(runtime, binding, PEER, value, adopted, planning_gate) == intent
    )
    assert runtime.snapshot() == committed
    # Stale owner revision and a Planning semantic denial leave no half publication.
    for failure in ("stale", "denied"):
        bad_command = command(failure, purpose="" if failure == "denied" else "valid")
        bad_proposal = proposal.model_copy(
            update={
                "intent_id": "effect-" + failure,
                "planning_command_bytes": command_bytes(bad_command),
                "expected_planning_revision": intent.planning_revision
                if failure == "stale"
                else None,
            }
        )
        bad_preview = await preview_action(runtime, binding, PEER, bad_proposal)
        bad_request = PlanEffectRequest.model_validate(
            {
                **bad_proposal.model_dump(),
                "adopted_preview": exact(bad_preview.head, bad_preview.canonical_bytes()),
                "authority_act_id": bad_command.authority_act_id,
            }
        )
        bad_gate = planning_gate.model_copy(
            update={
                "operation_id": bad_command.command_id.value,
                "payload_digest": hashlib.sha256(command_bytes(bad_command)).hexdigest(),
            }
        )
        before_denial = runtime.snapshot()
        with pytest.raises((LoopHold, LoopRejected)):
            await publish_plan_effect(runtime, binding, PEER, bad_command, bad_request, bad_gate)
        assert runtime.snapshot() == before_denial
    # A fresh send gate applies to the actual owner-produced intent, after Planning commit.
    send_current = current.model_copy(update={"entitlement_head": "plan-effect-send"})
    runtime.import_entitlement(
        send_current,
        signature(send_current),
        previous=canonical(planning_current.model_dump(mode="json")),
    )
    send_gate = request(send_current, "plan-send").model_copy(
        update={
            "surface_id": "effects.first_send",
            "payload_digest": hashlib.sha256(intent.canonical_bytes()).hexdigest(),
        }
    )
    await commit_effect_send(runtime, binding, PEER, adopted.intent_id, send_gate)
    work = await discover_effect_work(
        runtime,
        binding,
        PEER,
        EffectWorkScan(tenant_id="t", principal_id="hermetic-principal", limit=1),
    )
    assert work.items[0].stream_id == adopted.intent_id
    assert (await step_effect_work(runtime, binding, PEER, work.items[0])).disposition == "EMITTED"
    await runtime.seal_execution(PEER, "future", future.head)
    conflicting_preview = await preview_effect(
        runtime, binding, PEER, future_call_id, time.time_ns() + 10**12
    )
    with pytest.raises(LoopRejected, match="collides"):
        await adopt_effect(runtime, binding, PEER, conflicting_preview.head, "future-tool-adoption")
    # Rejection preserved the action's single subject and its recovery path.
    closure = await resolve_original_effect(runtime, binding, PEER, adopted.intent_id)
    assert closure.outcome == "CONFIRMED"
    assert len(binding.provider.transfers) == 1


async def test_claim_before_transport_fault_reopens_as_unknown_without_send(
    postgres_server: PostgresServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from chiplog.adapters.driven.effects_hermetic import IssuedEffectSendTicket
    from chiplog.capabilities.effects.effect_work_contracts import EffectWorkScan
    from chiplog.composition.postgres_effect_work import discover_effect_work, step_effect_work

    runtime, binding, call_id, _, current, gate_request = await setup_effect(
        postgres_server, tmp_path, "CONFIRM"
    )
    runtime.import_entitlement(current, signature(current))
    await commit_effect_send(runtime, binding, PEER, call_id, gate_request)
    scan = EffectWorkScan(tenant_id="t", principal_id="hermetic-principal", limit=1)
    pending = (await discover_effect_work(runtime, binding, PEER, scan)).items[0]

    async def fail_before_transport(ticket: IssuedEffectSendTicket) -> bytes:
        # A second connection can acquire the writer lock: provider is outside its transaction.
        with runtime.database.connect() as connection:
            connection.execute("SELECT head FROM user_state FOR UPDATE NOWAIT").fetchone()
        raise RuntimeError("fault after durable claim before transport")

    with monkeypatch.context() as scoped:
        scoped.setattr(binding.provider, "emit_issued", fail_before_transport)
        with pytest.raises(RuntimeError, match="fault after durable claim"):
            await step_effect_work(runtime, binding, PEER, pending)
    assert binding.provider.transfers == ()
    reopened = PostgresExecutionRuntime(
        runtime.database,
        "t",
        SECRET,
        **IDENTITY,
        model=HermeticModel(),
        origin=origin(),
        effects_binding=binding,
        authenticate_entitlement=verifier,
    )
    claimed = (await discover_effect_work(reopened, binding, PEER, scan)).items[0]
    assert claimed.kind == "RECONCILE"
    assert (await step_effect_work(reopened, binding, PEER, claimed)).disposition == "HELD"
    with pytest.raises(LoopHold, match="does not close"):
        await resolve_original_effect(reopened, binding, PEER, call_id)
    assert binding.provider.transfers == ()
