"""Actual two accepted consequential seals through the default native client."""

import hashlib
import json
import time
from pathlib import Path

from chiplog.adapters.driven.effects_hermetic import HermeticEffectsProvider
from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy
from chiplog.client import open_execution_client
from chiplog.composition.postgres_effects import (
    PostgresEffectsBinding,
    adopt_effect,
    commit_effect_send,
    emit_effect,
    preview_effect,
    reconcile_effect,
    resolve_original_effect,
)
from chiplog.composition.postgres_planning import PostgresPlanningRuntime
from chiplog.platform.postgres import PostgresServer
from tests.composition.test_postgres_completion import completion, eligibility, origin
from tests.composition.test_postgres_effects import IDENTITY, KEY, PEER
from tests.composition.test_postgres_execution import SECRET
from tests.composition.test_postgres_planning import verifier
from tests.support.deployment_gate import request, signature


def effect_response(*labels: str) -> bytes:
    return json.dumps(
        {
            "kind": "Continue",
            "tool_calls": [
                {
                    "call_id": label,
                    "tool": "request_self_effect",
                    "arguments": {"payload": "aGVsbG8=", "bundle_members": [label]},
                }
                for label in labels
            ],
        }
    ).encode()


async def test_two_actual_accepted_effect_seals_reopen_and_complete_without_repeat_send(
    postgres_server: PostgresServer,
    tmp_path: Path,
) -> None:
    database = postgres_server.user_database("t")
    database.provision()
    await PostgresPlanningRuntime(database, "t", SECRET).bootstrap(
        database_instance_id="db", token="token", **IDENTITY
    )
    provider = HermeticEffectsProvider(
        receipt_key=KEY,
        scenarios=("LOST_RESPONSE_AFTER_EFFECT", "CONFIRM"),
        journal_path=tmp_path / "transfers.jsonl",
        observation_journal_path=tmp_path / "observations.jsonl",
    )
    binding = PostgresEffectsBinding(provider, KEY)
    model = HermeticModel((effect_response("first", "cancel"), effect_response("second")))
    async with open_execution_client(
        tenant_id="t",
        **IDENTITY,
        operator_secret=SECRET,
        model=model,
        origin=origin(binding),
        effects_binding=binding,
        authenticate_entitlement=verifier,
    ) as client:
        created = await client.create_execution(
            PEER, "run", "two actions", BudgetPolicy(), execution_version="v3"
        )
        started = await client.begin_execution(PEER, "run", created.head)
        captured = await client.capture_execution(PEER, "run", started.head)
        first_seal = await client.seal_execution(PEER, "run", captured.head)
        first, cancelled = client.initialized_calls()
        await client.cancel_execution_call(PEER, cancelled.original_call_id, "explicit-cancel")
        preview = await preview_effect(
            client, binding, PEER, first.original_call_id, time.time_ns() + 10**12
        )
        intent = await adopt_effect(client, binding, PEER, preview.head, "first-adoption")
        current, first_request = eligibility(
            intent.canonical_bytes(), "effects.first_send", "first-send"
        )
        current = current.model_copy(
            update={"bounds": current.bounds.model_copy(update={"cap": 2})}
        )
        first_request = first_request.model_copy(update={"bounds": current.bounds})
        client.import_entitlement(current, signature(current))
        await commit_effect_send(client, binding, PEER, first.original_call_id, first_request)
        first_claim = await emit_effect(client, binding, PEER, first.original_call_id)
        assert client.call_state(first.original_call_id).outcome == "OUTCOME_UNKNOWN"
        await reconcile_effect(client, binding, PEER, first.original_call_id)
        await resolve_original_effect(client, binding, PEER, first.original_call_id)
        continued = await client.continue_execution(
            PEER, "run", client.execution_record("run").head
        )
        captured = await client.capture_execution(PEER, "run", continued.head)
        second_seal = await client.seal_execution(PEER, "run", captured.head)
        second = client.initialized_calls()[-1]
        assert second.original_call_id not in {first.original_call_id, cancelled.original_call_id}
        assert second_seal.turns[0].response_seal == first_seal.turns[0].response_seal
        preview = await preview_effect(
            client, binding, PEER, second.original_call_id, time.time_ns() + 10**12
        )
        intent = await adopt_effect(client, binding, PEER, preview.head, "second-adoption")
        second_request = request(current, "second-send").model_copy(
            update={
                "surface_id": "effects.first_send",
                "payload_digest": hashlib.sha256(intent.canonical_bytes()).hexdigest(),
            }
        )
        await commit_effect_send(client, binding, PEER, second.original_call_id, second_request)
        second_claim = await emit_effect(client, binding, PEER, second.original_call_id)
        await resolve_original_effect(client, binding, PEER, second.original_call_id)
        retained = client.execution_record("run")
        assert len(retained.original_obligations) == len(retained.no_retry_references) == 2
        assert len(model.requests) == len(provider.transfers) == 2
    provider = HermeticEffectsProvider(
        receipt_key=KEY,
        scenarios=("LOST_RESPONSE_AFTER_EFFECT", "CONFIRM"),
        journal_path=tmp_path / "transfers.jsonl",
        observation_journal_path=tmp_path / "observations.jsonl",
    )
    binding = PostgresEffectsBinding(provider, KEY)
    final_model = HermeticModel((completion(3),))
    async with open_execution_client(
        tenant_id="t",
        **IDENTITY,
        operator_secret=SECRET,
        model=final_model,
        origin=origin(binding),
        effects_binding=binding,
        authenticate_entitlement=verifier,
    ) as client:
        assert client.execution_record("run") == retained
        before = client.snapshot()
        assert await emit_effect(client, binding, PEER, first.original_call_id) == first_claim
        assert await emit_effect(client, binding, PEER, second.original_call_id) == second_claim
        assert client.snapshot() == before
        continued = await client.continue_execution(PEER, "run", retained.head)
        captured = await client.capture_execution(PEER, "run", continued.head)
        final_seal = await client.seal_execution(PEER, "run", captured.head)
        await client.accept_completion(PEER, "run", final_seal.head)
        complete = client.execution_record("run")
        assert complete.state == "SUCCEEDED"
        assert complete.original_obligations == retained.original_obligations
        assert complete.no_retry_references == retained.no_retry_references
        assert len(provider.transfers) == 2 and len(final_model.requests) == 1
        assert "NOT_EXECUTED" in final_model.requests[0].request
        assert "CONFIRMED" in final_model.requests[0].request


async def test_new_fanout_growth_still_has_normal_byte_bound(
    postgres_server: PostgresServer,
) -> None:
    import pytest

    from chiplog.capabilities.agent_loop.contracts import LoopRejected
    from tests.composition.test_postgres_execution import bootstrap, opened

    database = await bootstrap(postgres_server)
    raw = json.dumps(
        {
            "kind": "Continue",
            "tool_calls": [
                {
                    "call_id": str(index),
                    "tool": "propose_intent",
                    "text": "one",
                }
                for index in range(64)
            ],
        }
    ).encode()
    policy = BudgetPolicy(max_tool_calls=64)
    assert len(raw) < policy.max_response_bytes
    client = opened(database, HermeticModel((raw,)))
    created = await client.create_execution("local-cli", "bounded", "hello", policy)
    started = await client.begin_execution("local-cli", "bounded", created.head)
    captured = await client.capture_execution("local-cli", "bounded", started.head)
    assert len(captured.turns[-1].attempts[-1].request.encode()) < policy.max_request_bytes
    before = client.snapshot()
    with pytest.raises(LoopRejected, match="serialized sealed batch"):
        await client.seal_execution("local-cli", "bounded", captured.head)
    assert client.snapshot() == before
