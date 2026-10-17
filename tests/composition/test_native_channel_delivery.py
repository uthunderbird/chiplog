"""Real local ingress/shared driver and hermetic delivery, including restart.

Telegram is an authenticated webhook adapter boundary, not a live Telegram send.
Both sources explicitly select the registered hermetic self-delivery transport.
"""

import asyncio
import hashlib
import hmac
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Literal

import pytest

from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.adapters.driven.native_ingress_cli import CliIngressFrame, NativeCliIngress
from chiplog.adapters.driven.native_ingress_telegram import NativeTelegramIngress
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy, LoopHold
from chiplog.capabilities.agent_loop.delivery_contracts import (
    ExactHead,
    OriginSelection,
    ProviderRecipient,
)
from chiplog.capabilities.agent_loop.ingress_client_contracts import (
    NativeIngressIdentityV1,
    NativeIngressReceiptV1,
)
from chiplog.composition.postgres_application import PostgresApplicationDriver
from chiplog.composition.postgres_effects import (
    PostgresEffectsBinding,
    adopt_effect,
    commit_effect_send,
    emit_effect,
    preview_effect,
    reconcile_effect,
    resolve_original_effect,
)
from chiplog.composition.postgres_execution import PostgresExecutionRuntime
from chiplog.composition.postgres_ingress import (
    PostgresIngressService,
    RegisteredIngressSource,
    subject,
)
from chiplog.composition.postgres_planning import PostgresPlanningRuntime
from chiplog.platform.deployment_gate import CurrentEntitlement, DeploymentGateRequest
from chiplog.platform.postgres import PostgresServer, UserDatabase
from chiplog.platform.postgres_gate import canonical
from tests.support.composition.native_channel_delivery import (
    ISSUER_KEY,
    binding,
)
from tests.support.deployment_gate import KEY as GATE_KEY
from tests.support.deployment_gate import entitlement, request, signature

PRINCIPAL, PEER = "hermetic-principal", "local-cli"
OPERATOR_SECRET, WEBHOOK_SECRET = b"operator-fixture", b"webhook-fixture"
type Channel = Literal["CLI", "TELEGRAM"]


def verifier(payload: bytes, proof: bytes) -> bool:
    return hmac.compare_digest(hmac.digest(GATE_KEY, payload, "sha256"), proof)


def registered(channel: Channel, transport: PostgresEffectsBinding) -> RegisteredIngressSource:
    target = transport.target_for_principal(PRINCIPAL)
    head = ExactHead(identity=channel + "-source", head=channel + ":v1", fingerprint="a" * 64)
    return RegisteredIngressSource(
        tenant_id="t",
        channel=channel,
        source_id=channel,
        principal_id=PRINCIPAL,
        issuer_id="trusted-" + channel,
        binding=head,
        origin=OriginSelection(
            ingress_binding=head,
            recipient=ProviderRecipient(
                provider_id=target.provider,
                account_id=target.account,
                recipient_id=target.recipient,
                canonical_address=target.canonical_address,
                endpoint=ExactHead(
                    identity=target.endpoint.subject_id,
                    head=target.endpoint.head,
                    fingerprint=target.endpoint.fingerprint,
                ),
                credential_binding=ExactHead(
                    identity=target.credential_binding.subject_id,
                    head=target.credential_binding.head,
                    fingerprint=target.credential_binding.fingerprint,
                ),
            ),
        ),
        transport_subject=subject("uid", os.getuid())
        if channel == "CLI"
        else subject("telegram", "webhook-endpoint", "bot-account", 7, 9),
    )


def opened(
    database: UserDatabase,
    source: RegisteredIngressSource,
    transport: PostgresEffectsBinding,
    model: HermeticModel,
) -> tuple[PostgresExecutionRuntime, PostgresIngressService, PostgresApplicationDriver]:
    runtime = PostgresExecutionRuntime(
        database,
        "t",
        OPERATOR_SECRET,
        principal_id=PRINCIPAL,
        credential_id="c",
        session_id="s",
        model=model,
        origin=source.origin,
        effects_binding=transport,
        authenticate_entitlement=verifier,
    )
    ingress = PostgresIngressService(runtime, (source,), issuer_key=ISSUER_KEY)
    return runtime, ingress, PostgresApplicationDriver(runtime, ingress=ingress)


async def through_channel(
    channel: Channel,
    ingress: PostgresIngressService,
    driver: PostgresApplicationDriver,
    raw: bytes,
    replay_id: str,
) -> NativeIngressReceiptV1:
    if channel == "TELEGRAM":
        adapter = NativeTelegramIngress(
            ingress,
            driver,
            peer=PEER,
            tenant_id="t",
            endpoint_id="webhook-endpoint",
            account_id="bot-account",
            secret=WEBHOOK_SECRET,
            user_id=7,
            chat_id=9,
            policy=BudgetPolicy(),
            issue=ingress.issuer(channel),
        )
        return await adapter.receive(
            endpoint_id="webhook-endpoint",
            account_id="bot-account",
            secret_header=WEBHOOK_SECRET,
            raw=raw,
        )
    cli = NativeCliIngress(
        ingress,
        driver,
        peer=PEER,
        uid=os.getuid(),
        policy=BudgetPolicy(),
        issue=ingress.issuer(channel),
    )
    finished: asyncio.Future[None] = asyncio.get_running_loop().create_future()

    async def accepted(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await cli.receive(reader, writer)
            finished.set_result(None)
        except BaseException as error:
            finished.set_exception(error)
        finally:
            # resource-contexts: justified-manual-close — accepted writer has no context manager
            writer.close()
            await writer.wait_closed()

    with tempfile.TemporaryDirectory(dir="/tmp") as directory:
        path = Path(directory) / "channel.sock"
        server = await asyncio.start_unix_server(accepted, path=path)
        os.chmod(path, 0o600)
        async with server:
            reader, writer = await asyncio.open_unix_connection(path)
            try:
                frame = CliIngressFrame(replay_id=replay_id, raw_bytes=raw).canonical_bytes()
                writer.write(len(frame).to_bytes(4, "big") + frame)
                await writer.drain()
                writer.write_eof()
                size = int.from_bytes(await reader.readexactly(4), "big")
                receipt = NativeIngressReceiptV1.model_validate_json(await reader.readexactly(size))
                assert ingress.lookup(PEER, receipt.identity) == receipt
                # ACK confirms custody; completion requires the actual handler to finish.
                await finished
                return receipt
            finally:
                # resource-contexts: justified-manual-close — client writer has no context manager
                writer.close()
                await writer.wait_closed()


def eligibility(
    payload: bytes, surface: str = "delivery.first_send"
) -> tuple[CurrentEntitlement, DeploymentGateRequest]:
    now = time.time_ns()
    current = entitlement().model_copy(
        update={
            "entitlement_head": surface + ":entitlement",
            "freshness_leases": (("evidence", now + 10**12),),
            "bounds": entitlement().bounds.model_copy(
                update={
                    "capability_id": surface,
                    "starts_ns": now - 1,
                    "expires_ns": now + 10**12,
                }
            ),
        }
    )
    operation = "channel-delivery" if surface == "delivery.first_send" else "channel-effect"
    gate = request(current, operation).model_copy(
        update={
            "surface_id": surface,
            "payload_digest": hashlib.sha256(payload).hexdigest(),
        }
    )
    return current, gate


@pytest.mark.parametrize("channel", ["CLI", "TELEGRAM"])
@pytest.mark.parametrize("flow", ["DIRECT", "EFFECT"])
async def test_actual_channel_driver_delivery_reopens_without_model_or_transfer_repeat(
    postgres_server: PostgresServer,
    tmp_path: Path,
    channel: Channel,
    flow: Literal["DIRECT", "EFFECT"],
) -> None:
    database = postgres_server.user_database("t")
    database.provision()
    await PostgresPlanningRuntime(database, "t", OPERATOR_SECRET).bootstrap(
        database_instance_id="db",
        principal_id=PRINCIPAL,
        credential_id="c",
        session_id="s",
        token="token",
    )
    with_effect = flow == "EFFECT"
    transport = binding(tmp_path, with_effect=with_effect)
    source = registered(channel, transport)
    identity = NativeIngressIdentityV1(
        tenant_id="t",
        channel=channel,
        registered_source_id=channel,
        replay_id="3",
    )
    run_id = "ingress-run:" + hashlib.sha256(identity.canonical_bytes()).hexdigest()
    completion = json.dumps(
        {
            "kind": "Complete",
            "tenant": "t",
            "run_id": run_id,
            "turn_id": run_id + ("/turn/2" if with_effect else "/turn/1"),
            "deliveries": [
                {"payload": [{"kind": "NonAuthoritativeText", "text": "accepted hello"}]}
            ],
        }
    ).encode()
    proposal = json.dumps(
        {
            "kind": "Continue",
            "tool_calls": [
                {
                    "call_id": "effect",
                    "tool": "request_self_effect",
                    "arguments": {"payload": "aGVsbG8=", "bundle_members": ["channel-effect"]},
                }
            ],
        }
    ).encode()
    model = HermeticModel((proposal, completion) if with_effect else (completion,))
    runtime, ingress, driver = opened(database, source, transport, model)
    raw = (
        b" hello\r\n"
        if channel == "CLI"
        else json.dumps(
            {
                "update_id": 3,
                "message": {
                    "message_id": 11,
                    "text": " hello\r\n",
                    "chat": {"id": 9, "type": "private"},
                    "from": {"id": 7, "is_bot": False},
                },
            },
            indent=2,
        ).encode()
    )
    previous_entitlement: bytes | None = None
    effect_call_id: str | None = None
    effect_claim_head: str | None = None
    effect_intent_id: str | None = None
    if with_effect:
        # CLI has emitted its custody ACK; Telegram has custody but no returned ACK.
        with pytest.raises(LoopHold, match="explicit user adoption"):
            await through_channel(channel, ingress, driver, raw, "3")
        admitted = ingress.lookup(PEER, identity)
        assert admitted is not None and admitted.run_id == run_id
        assert len(model.requests) == 1 and transport.provider.transfers == ()
        (call,) = runtime.initialized_calls()
        effect_call_id = call.original_call_id
        assert runtime.call_state(effect_call_id).state == "INITIALIZED"
        preview = await preview_effect(
            runtime, transport, PEER, effect_call_id, time.time_ns() + 10**12
        )
        adopted = await adopt_effect(
            runtime, transport, PEER, preview.head, "explicit-channel-user-adoption"
        )
        effect_intent_id = adopted.intent_id
        effect_current, effect_gate = eligibility(adopted.canonical_bytes(), "effects.first_send")
        runtime.import_entitlement(effect_current, signature(effect_current))
        previous_entitlement = canonical(effect_current.model_dump(mode="json"))
        await commit_effect_send(runtime, transport, PEER, effect_call_id, effect_gate)
        effect_claim = await emit_effect(runtime, transport, PEER, effect_call_id)
        effect_claim_head = effect_claim.head
        assert len(transport.provider.transfers) == 1
        assert transport.provider.transfers[0].ticket.intent_id == effect_intent_id
        assert transport.provider.transfers[0].ticket.payload == b"hello"
        with pytest.raises(LoopHold):
            await resolve_original_effect(runtime, transport, PEER, effect_call_id)
        assert await reconcile_effect(runtime, transport, PEER, effect_call_id) is not None
        closure = await resolve_original_effect(runtime, transport, PEER, effect_call_id)
        assert closure.outcome == "CONFIRMED"
        assert runtime.call_state(effect_call_id).state == "TERMINAL"
    receipt = await through_channel(channel, ingress, driver, raw, "3")
    assert receipt.identity == identity and receipt.raw_bytes == raw and receipt.run_id == run_id
    completed = runtime.execution_record(run_id)
    assert completed.state == "SUCCEEDED" and completed.delivery_acceptance is not None
    assert completed.origin == source.origin and len(model.requests) == (2 if with_effect else 1)
    (intent,) = runtime.delivery_intents(PEER, run_id)
    rendered = intent.delivery.rendered_bytes
    assert (
        rendered
        == b'UNVERIFIED MODEL COMMENTARY\n"accepted hello"\nEND UNVERIFIED MODEL COMMENTARY'
    )
    assert (
        runtime.read_user_history_at(runtime.snapshot().head, PRINCIPAL)[-1].text.encode()
        == rendered
    )
    current, gate = eligibility(intent.canonical_bytes())
    runtime.import_entitlement(current, signature(current), previous=previous_entitlement)
    await runtime.commit_delivery_send(PEER, intent.delivery_id, gate)
    claim = await runtime.emit_delivery(PEER, intent.delivery_id)
    expected_transfers = 2 if with_effect else 1
    assert len(transport.provider.transfers) == expected_transfers
    assert transport.provider.transfers[-1].ticket.payload == rendered
    assert transport.provider.transfers[-1].ticket.intent_id == intent.delivery_id
    assert transport.provider.transfers[-1].ticket.provider == "hermetic-effects"
    assert (
        len({entry.ticket.transmission_id for entry in transport.provider.transfers})
        == expected_transfers
    )
    fresh_transport = binding(tmp_path, with_effect=with_effect)
    forbidden_model = HermeticModel(())  # Any invocation raises; there is no fallback.
    fresh, fresh_ingress, fresh_driver = opened(database, source, fresh_transport, forbidden_model)
    replay = await through_channel(channel, fresh_ingress, fresh_driver, raw, "3")
    assert replay == receipt
    assert fresh.execution_record(run_id) == completed
    assert forbidden_model.requests == []
    if effect_call_id is not None:
        replayed_effect = await emit_effect(fresh, fresh_transport, PEER, effect_call_id)
        assert replayed_effect.head == effect_claim_head
        assert fresh_transport.provider.transfers[0].ticket.intent_id == effect_intent_id
        assert fresh_transport.provider.transfers[0].ticket.payload == b"hello"
    assert await fresh.emit_delivery(PEER, intent.delivery_id) == claim
    result = await fresh.reconcile_delivery(PEER, intent.delivery_id)
    assert result is not None and result.outcome == "CONFIRMED"
    assert len(fresh_transport.provider.transfers) == expected_transfers
    assert len(fresh.read_user_history_at(fresh.snapshot().head, PRINCIPAL)) == 2
