import json
import time
from pathlib import Path
from typing import Any, Literal

import pytest

from chiplog.adapters.driven.effects_hermetic import HermeticEffectsProvider
from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.capabilities.agent_loop.completion_client_contracts import NativeDeliveryIntentV1
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy, LoopHold, LoopRejected
from chiplog.composition.postgres_completion import (
    accept_completion,
    commit_delivery_send,
    emit_delivery,
    reconcile_delivery,
    rows,
)
from chiplog.composition.postgres_effects import PostgresEffectsBinding
from chiplog.composition.postgres_execution import PostgresExecutionRuntime
from chiplog.composition.postgres_planning import PostgresPlanningRuntime
from chiplog.platform.postgres import PostgresServer
from tests.support.composition.postgres_completion import (
    completion,
    eligibility,
    opened,
    origin,
    prepared,
)
from tests.support.composition.postgres_effects import IDENTITY, KEY, PEER
from tests.support.composition.postgres_execution import SECRET
from tests.support.composition.postgres_planning import verifier
from tests.support.deployment_gate import signature


@pytest.mark.parametrize("scenario", ["CONFIRM", "LOST_RESPONSE_AFTER_EFFECT"])
async def test_native_complete_accepts_exact_rendered_history_and_delivers_once_after_reopen(
    postgres_server: PostgresServer,
    tmp_path: Path,
    scenario: Literal["CONFIRM", "LOST_RESPONSE_AFTER_EFFECT"],
) -> None:
    runtime, binding, source = await prepared(postgres_server, tmp_path, scenario)
    accepted = await accept_completion(runtime, PEER, "run", source.head)
    current_run = runtime.execution_record("run")
    assert current_run.state == "SUCCEEDED" and current_run.delivery_acceptance is not None
    assert accepted.source_run_head == source.head
    intent = rows(runtime, runtime.snapshot(), NativeDeliveryIntentV1)[0]
    batch = runtime.snapshot().batches[-1]
    assert len(batch.records) == 5
    assert {item.owner for item in batch.records} == {"agent_loop", "effects", "conversation"}
    rendered = intent.delivery.rendered_bytes
    assert rendered == b'UNVERIFIED MODEL COMMENTARY\n"hello"\nEND UNVERIFIED MODEL COMMENTARY'
    assert (
        runtime.read_user_history_at(runtime.snapshot().head, runtime.principal_id)[
            -1
        ].text.encode()
        == rendered
    )
    current, gate_request = eligibility(intent.canonical_bytes(), "delivery.first_send")
    before = runtime.snapshot()
    with pytest.raises(PermissionError, match="HOLD"):
        await commit_delivery_send(runtime, PEER, intent.delivery_id, gate_request)
    assert runtime.snapshot() == before and binding.provider.transfers == ()
    runtime.import_entitlement(current, signature(current))
    await commit_delivery_send(runtime, PEER, intent.delivery_id, gate_request)
    claim = await emit_delivery(runtime, PEER, intent.delivery_id)
    assert len(binding.provider.transfers) == 1
    assert binding.provider.transfers[0].ticket.payload == rendered
    binding = PostgresEffectsBinding(
        HermeticEffectsProvider(
            receipt_key=KEY,
            scenarios=(scenario,),
            journal_path=tmp_path / "transfers.jsonl",
            observation_journal_path=tmp_path / "observations.jsonl",
        ),
        KEY,
    )
    fresh = opened(runtime, binding, HermeticModel())
    assert await accept_completion(fresh, PEER, "run", source.head) == accepted
    assert await emit_delivery(fresh, PEER, intent.delivery_id) == claim
    receipt = await reconcile_delivery(fresh, PEER, intent.delivery_id)
    assert receipt is not None and receipt.outcome == "CONFIRMED"
    assert len(binding.provider.transfers) == 1
    assert len(fresh.read_user_history_at(fresh.snapshot().head, fresh.principal_id)) == 2


async def test_completion_changed_source_cannot_publish_acceptance(
    postgres_server: PostgresServer,
    tmp_path: Path,
) -> None:
    runtime, _, source = await prepared(postgres_server, tmp_path)
    before = runtime.snapshot()
    assert source.predecessor is not None
    with pytest.raises(LoopRejected, match="exact prepared"):
        await accept_completion(runtime, PEER, "run", source.predecessor)
    assert runtime.snapshot() == before


async def test_complete_acceptance_rolls_back_manifest_history_and_intent_together(
    postgres_server: PostgresServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from chiplog.platform.postgres import Connection, PublicationResult, UserDatabase

    runtime, _, source = await prepared(postgres_server, tmp_path)
    before = runtime.snapshot()
    original = UserDatabase.append

    def fail_after_insert(
        self: UserDatabase, connection: Connection, operation: str, *args: Any, **kwargs: Any
    ) -> PublicationResult:
        result = original(self, connection, operation, *args, **kwargs)
        if operation == "completion.accept":
            raise RuntimeError("acceptance transaction interrupted after inserts")
        return result

    with monkeypatch.context() as patched:
        patched.setattr(UserDatabase, "append", fail_after_insert)
        with pytest.raises(RuntimeError, match="after inserts"):
            await accept_completion(runtime, PEER, "run", source.head)
    assert runtime.snapshot() == before
    assert runtime.execution_record("run") == source
    await accept_completion(runtime, PEER, "run", source.head)
    assert len(runtime.read_user_history_at(runtime.snapshot().head, runtime.principal_id)) == 2


@pytest.mark.parametrize("late_stage", [None, "before_acceptance", "before_claim"])
async def test_current_provider_assertion_and_old_seal_rival_at_both_acceptance_and_claim(
    postgres_server: PostgresServer,
    tmp_path: Path,
    late_stage: str | None,
) -> None:
    from chiplog.composition.postgres_completion import provider_queries
    from chiplog.composition.postgres_effects import (
        adopt_effect,
        append_effect_receipt,
        commit_effect_send,
        emit_effect,
        preview_effect,
        resolve_original_effect,
    )
    from chiplog.platform.postgres_gate import canonical

    effect = json.dumps(
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
    runtime, binding, _ = await prepared(postgres_server, tmp_path, raw=effect, late=True)
    call_id = runtime.initialized_calls()[0].original_call_id
    preview = await preview_effect(runtime, binding, PEER, call_id, time.time_ns() + 10**12)
    adopted = await adopt_effect(runtime, binding, PEER, preview.head, "explicit-adoption")
    current, gate_request = eligibility(
        adopted.canonical_bytes(), "effects.first_send", "original-send"
    )
    runtime.import_entitlement(current, signature(current))
    send = await commit_effect_send(runtime, binding, PEER, call_id, gate_request)
    await emit_effect(runtime, binding, PEER, call_id)
    await resolve_original_effect(runtime, binding, PEER, call_id)
    query = provider_queries(runtime, runtime.snapshot(), runtime.execution_record("run"))[0]
    payload = {
        "kind": "DeliveryAssertion",
        "assertion_kind": "PROVIDER",
        "subject": query.subject.model_dump(mode="json"),
        "correlation": query.correlation.model_dump(mode="json"),
        "query": query.query.model_dump(mode="json"),
        "records": [value.model_dump(mode="json") for value in query.records],
        "source_frontier": query.source_frontier,
        "codes": ["PROVIDER_CONFIRMED_SUCCESS"],
    }
    raw = json.dumps(
        {
            "kind": "Complete",
            "tenant": "t",
            "run_id": "run",
            "turn_id": "run/turn/2",
            "deliveries": [{"payload": [payload]}],
        }
    ).encode()
    model = HermeticModel((raw,))
    runtime = opened(runtime, binding, model)
    continued = await runtime.continue_execution(PEER, "run", runtime.execution_record("run").head)
    captured = await runtime.capture_execution(PEER, "run", continued.head)
    source = await runtime.seal_execution(PEER, "run", captured.head)
    assert query.query.identity in model.requests[0].request
    if late_stage == "before_acceptance":
        rival = binding.provider.observe_late_receipt(send.transmission.transmission.subject_id)
        await append_effect_receipt(runtime, binding, PEER, call_id, rival)
        before = runtime.snapshot()
        with pytest.raises(LoopHold):
            await accept_completion(runtime, PEER, "run", source.head)
        assert runtime.snapshot() == before
        return
    accepted = await accept_completion(runtime, PEER, "run", source.head)
    assert b"Provider outcome: CONFIRMED_SUCCESS" in accepted.proposal.history_rendered_bytes[0]
    if late_stage == "before_claim":
        intent = rows(runtime, runtime.snapshot(), NativeDeliveryIntentV1)[0]
        delivery_current, delivery_request = eligibility(
            intent.canonical_bytes(), "delivery.first_send"
        )
        runtime.import_entitlement(
            delivery_current,
            signature(delivery_current),
            previous=canonical(current.model_dump(mode="json")),
        )
        await commit_delivery_send(runtime, PEER, intent.delivery_id, delivery_request)
        rival = binding.provider.observe_late_receipt(send.transmission.transmission.subject_id)
        await append_effect_receipt(runtime, binding, PEER, call_id, rival)
        before = runtime.snapshot()
        with pytest.raises(LoopHold):
            await emit_delivery(runtime, PEER, intent.delivery_id)
        assert runtime.snapshot() == before
        assert len(binding.provider.transfers) == 1


@pytest.mark.parametrize("change", ["recipient", "disclosure"])
async def test_completion_rejects_unregistered_recipient_and_historical_disclosure(
    postgres_server: PostgresServer,
    tmp_path: Path,
    change: str,
) -> None:
    # First build the actual immutable source under the ordinary registered origin.
    runtime, binding, _ = await prepared(postgres_server, tmp_path)
    selected = origin(binding).recipient
    if change == "recipient":
        selected = selected.model_copy(update={"account_id": "foreign-account"})
        selected_origin = origin(binding)
    else:
        endpoint = selected.endpoint.model_copy(update={"identity": "private-ingress-only"})
        selected_origin = origin(binding).model_copy(
            update={"recipient": selected.model_copy(update={"endpoint": endpoint})}
        )
    raw = json.dumps(
        {
            "kind": "Complete",
            "tenant": "t",
            "run_id": "restricted",
            "turn_id": "restricted/turn/1",
            "deliveries": [
                {
                    "selection": {
                        "kind": "MODEL_SELECTED_EXACT",
                        "recipient": selected.model_dump(mode="json"),
                    },
                    "payload": [{"kind": "NonAuthoritativeText", "text": "private"}],
                }
            ],
        }
    ).encode()
    runtime = PostgresExecutionRuntime(
        runtime.database,
        "t",
        SECRET,
        **IDENTITY,
        model=HermeticModel((raw,)),
        origin=selected_origin,
        effects_binding=binding,
        authenticate_entitlement=verifier,
    )
    created = await runtime.create_execution(PEER, "restricted", "private", BudgetPolicy())
    started = await runtime.begin_execution(PEER, "restricted", created.head)
    captured = await runtime.capture_execution(PEER, "restricted", started.head)
    source = await runtime.seal_execution(PEER, "restricted", captured.head)
    before = runtime.snapshot()
    with pytest.raises(LoopRejected, match=r"registered|disclosure"):
        await accept_completion(runtime, PEER, "restricted", source.head)
    assert runtime.snapshot() == before
    assert binding.provider.transfers == ()


async def test_actual_codex_noncanonical_complete_keeps_original_raw_and_accepts_semantics(
    postgres_server: PostgresServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import base64

    import httpx
    from oauth_cli_kit.models import OAuthToken  # type: ignore[import-untyped]

    from chiplog.adapters.driven.codex_auth import CodexTokenStorage, credential_identity
    from chiplog.adapters.driven.codex_model import CodexModel
    from chiplog.capabilities.agent_loop.live_contract import LiveModelBinding

    database = postgres_server.user_database("t")
    database.provision()
    await PostgresPlanningRuntime(database, "t", SECRET).bootstrap(
        database_instance_id="db", token="token", **IDENTITY
    )
    binding = PostgresEffectsBinding(
        HermeticEffectsProvider(
            receipt_key=KEY,
            scenarios=("CONFIRM",),
            journal_path=tmp_path / "transfers.jsonl",
            observation_journal_path=tmp_path / "observations.jsonl",
        ),
        KEY,
    )
    storage = CodexTokenStorage(tmp_path / "oauth")
    token = OAuthToken(
        "test-access", "test-refresh", int(time.time() * 1000) + 600000, "native-account"
    )
    storage.save(token)
    live = LiveModelBinding(credential_identity=credential_identity(token))
    model = CodexModel(storage, live)
    runtime = PostgresExecutionRuntime(
        database,
        "t",
        SECRET,
        **IDENTITY,
        model=model,
        origin=origin(binding),
        effects_binding=binding,
        authenticate_entitlement=verifier,
    )
    raw = completion()
    requests: list[str] = []
    actual_client = httpx.AsyncClient

    def respond(value: httpx.Request) -> httpx.Response:
        requests.append(value.method)
        event = {
            "type": "response.completed",
            "response": {
                "id": "complete-response",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": raw.decode()}],
                    }
                ],
            },
        }
        return httpx.Response(200, content="data: " + json.dumps(event) + "\n\n")

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: actual_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    created = await runtime.create_execution(PEER, "run", "hello", BudgetPolicy(live_model=live))
    started = await runtime.begin_execution(PEER, "run", created.head)
    captured = await runtime.capture_execution(PEER, "run", started.head)
    source = await runtime.seal_execution(PEER, "run", captured.head)
    attempt = source.turns[-1].attempts[-1]
    assert base64.b64decode(attempt.response_base64 or "", validate=True) == raw
    accepted = await accept_completion(runtime, PEER, "run", source.head)
    assert accepted.source_run_head == source.head
    assert requests == ["POST"]


async def test_delivery_claim_rechecks_expired_gate_after_recorded_handoff(
    postgres_server: PostgresServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, binding, source = await prepared(postgres_server, tmp_path)
    await accept_completion(runtime, PEER, "run", source.head)
    intent = rows(runtime, runtime.snapshot(), NativeDeliveryIntentV1)[0]
    current, gate_request = eligibility(intent.canonical_bytes(), "delivery.first_send")
    runtime.import_entitlement(current, signature(current))
    await commit_delivery_send(runtime, PEER, intent.delivery_id, gate_request)
    before = runtime.snapshot()
    monkeypatch.setattr(time, "time_ns", lambda: current.bounds.expires_ns)
    with pytest.raises(PermissionError, match="HOLD"):
        await emit_delivery(runtime, PEER, intent.delivery_id)
    assert runtime.snapshot() == before and binding.provider.transfers == ()


async def test_delivery_claim_rechecks_revoked_actor_before_send(
    postgres_server: PostgresServer,
    tmp_path: Path,
) -> None:
    runtime, binding, source = await prepared(postgres_server, tmp_path)
    await accept_completion(runtime, PEER, "run", source.head)
    intent = rows(runtime, runtime.snapshot(), NativeDeliveryIntentV1)[0]
    current, gate_request = eligibility(intent.canonical_bytes(), "delivery.first_send")
    runtime.import_entitlement(current, signature(current))
    await commit_delivery_send(runtime, PEER, intent.delivery_id, gate_request)
    runtime.revoke("credential:1")
    with pytest.raises(PermissionError, match="authenticated"):
        await emit_delivery(runtime, PEER, intent.delivery_id)
    assert binding.provider.transfers == ()
