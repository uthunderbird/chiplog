"""An unrelated Run cannot change an accepted delivery's selected query cut."""

import time
from pathlib import Path

from chiplog.adapters.driven.effects_hermetic import HermeticEffectsProvider
from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy
from chiplog.composition.postgres_completion import provider_queries
from chiplog.composition.postgres_effects import (
    PostgresEffectsBinding,
    adopt_effect,
    commit_effect_send,
    emit_effect,
    preview_effect,
    resolve_original_effect,
)
from chiplog.composition.postgres_execution import PostgresExecutionRuntime
from chiplog.composition.postgres_planning import PostgresPlanningRuntime
from chiplog.platform.deployment_gate import CurrentEntitlement
from chiplog.platform.postgres import PostgresServer
from tests.composition.test_postgres_completion import completion, eligibility, opened, origin
from tests.composition.test_postgres_effects import IDENTITY, KEY, PEER
from tests.composition.test_postgres_execution import SECRET
from tests.composition.test_postgres_planning import verifier
from tests.composition.test_postgres_two_seals import effect_response
from tests.support.deployment_gate import signature


def install_entitlement(runtime: PostgresExecutionRuntime, current: CurrentEntitlement) -> None:
    with runtime.database.read() as connection:
        row = connection.execute("SELECT canonical_bytes FROM gate_current").fetchone()
    runtime.import_entitlement(current, signature(current), previous=bytes(row[0]) if row else None)


async def execute_effect(runtime: PostgresExecutionRuntime, run_id: str) -> None:
    created = await runtime.create_execution(PEER, run_id, "one effect", BudgetPolicy())
    started = await runtime.begin_execution(PEER, run_id, created.head)
    captured = await runtime.capture_execution(PEER, run_id, started.head)
    await runtime.seal_execution(PEER, run_id, captured.head)
    call_id = runtime.initialized_calls()[-1].original_call_id
    assert runtime.effects_binding is not None
    preview = await preview_effect(
        runtime, runtime.effects_binding, PEER, call_id, time.time_ns() + 10**12
    )
    adopted = await adopt_effect(
        runtime, runtime.effects_binding, PEER, preview.head, run_id + "/adopt"
    )
    current, request = eligibility(
        adopted.canonical_bytes(), "effects.first_send", run_id + "/send"
    )
    current = current.model_copy(update={"entitlement_head": run_id + "/entitlement"})
    request = request.model_copy(update={"entitlement_head": current.entitlement_head})
    install_entitlement(runtime, current)
    assert runtime.effects_binding is not None
    await commit_effect_send(runtime, runtime.effects_binding, PEER, call_id, request)
    await emit_effect(runtime, runtime.effects_binding, PEER, call_id)
    await resolve_original_effect(runtime, runtime.effects_binding, PEER, call_id)


async def test_unrelated_reduction_does_not_hold_accepted_delivery_commit_claim_or_reopen(
    postgres_server: PostgresServer,
    tmp_path: Path,
) -> None:
    database = postgres_server.user_database("t")
    database.provision()
    await PostgresPlanningRuntime(database, "t", SECRET).bootstrap(
        database_instance_id="db", token="token", **IDENTITY
    )
    binding = PostgresEffectsBinding(
        HermeticEffectsProvider(
            receipt_key=KEY,
            scenarios=("CONFIRM", "CONFIRM", "CONFIRM"),
            journal_path=tmp_path / "transfers.jsonl",
            observation_journal_path=tmp_path / "observations.jsonl",
        ),
        KEY,
    )
    runtime = PostgresExecutionRuntime(
        database,
        "t",
        SECRET,
        **IDENTITY,
        model=HermeticModel((effect_response("a-effect"), completion(2))),
        origin=origin(binding),
        effects_binding=binding,
        authenticate_entitlement=verifier,
    )
    await execute_effect(runtime, "run")
    continued = await runtime.continue_execution(PEER, "run", runtime.execution_record("run").head)
    captured = await runtime.capture_execution(PEER, "run", continued.head)
    source = await runtime.seal_execution(PEER, "run", captured.head)
    accepted = await runtime.accept_completion(PEER, "run", source.head)
    selected_before = provider_queries(runtime, runtime.snapshot(), source)
    runtime = opened(runtime, binding, HermeticModel((effect_response("b-effect"),)))
    await execute_effect(runtime, "unrelated")
    intent = runtime.delivery_intents(PEER, "run")[0]
    current, request = eligibility(intent.canonical_bytes(), "delivery.first_send", "a-delivery")
    install_entitlement(runtime, current)
    send = await runtime.commit_delivery_send(PEER, intent.delivery_id, request)
    claim = await runtime.emit_delivery(PEER, intent.delivery_id)
    assert provider_queries(runtime, runtime.snapshot(), source) == selected_before
    assert len(binding.provider.transfers) == 3
    assert binding.provider.transfers[-1].ticket.payload == intent.delivery.rendered_bytes
    binding = PostgresEffectsBinding(
        HermeticEffectsProvider(
            receipt_key=KEY,
            scenarios=("CONFIRM", "CONFIRM", "CONFIRM"),
            journal_path=tmp_path / "transfers.jsonl",
            observation_journal_path=tmp_path / "observations.jsonl",
        ),
        KEY,
    )
    fresh_model = HermeticModel()
    runtime = opened(runtime, binding, fresh_model)
    before = runtime.snapshot()
    assert await runtime.accept_completion(PEER, "run", source.head) == accepted
    assert await runtime.commit_delivery_send(PEER, intent.delivery_id, request) == send
    assert await runtime.emit_delivery(PEER, intent.delivery_id) == claim
    receipt = await runtime.reconcile_delivery(PEER, intent.delivery_id)
    assert receipt is not None and receipt.outcome == "CONFIRMED"
    assert runtime.snapshot() == before
    assert len(binding.provider.transfers) == 3 and fresh_model.requests == []
