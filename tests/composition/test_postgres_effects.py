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
