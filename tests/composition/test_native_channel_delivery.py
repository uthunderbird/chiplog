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

from chiplog.adapters.driven.effects_hermetic import HermeticEffectsProvider
from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.adapters.driven.native_ingress_cli import CliIngressFrame, NativeCliIngress
from chiplog.adapters.driven.native_ingress_telegram import NativeTelegramIngress
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy
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
from chiplog.composition.postgres_effects import PostgresEffectsBinding
from chiplog.composition.postgres_execution import PostgresExecutionRuntime
from chiplog.composition.postgres_ingress import (
    PostgresIngressService,
    RegisteredIngressSource,
    subject,
)
from chiplog.composition.postgres_planning import PostgresPlanningRuntime
from chiplog.platform.deployment_gate import CurrentEntitlement, DeploymentGateRequest
from chiplog.platform.postgres import PostgresServer, UserDatabase
from tests.support.deployment_gate import KEY as GATE_KEY
from tests.support.deployment_gate import entitlement, request, signature

PRINCIPAL, PEER = "hermetic-principal", "local-cli"
ISSUER_KEY, RECEIPT_KEY = b"stable-channel-issuer", b"stable-provider-receipt"
OPERATOR_SECRET, WEBHOOK_SECRET = b"operator-fixture", b"webhook-fixture"
type Channel = Literal["CLI", "TELEGRAM"]


def verifier(payload: bytes, proof: bytes) -> bool:
    return hmac.compare_digest(hmac.digest(GATE_KEY, payload, "sha256"), proof)


def binding(directory: Path) -> PostgresEffectsBinding:
    return PostgresEffectsBinding(
        HermeticEffectsProvider(
            receipt_key=RECEIPT_KEY,
            scenarios=("LOST_RESPONSE_AFTER_EFFECT",),
            journal_path=directory / "transfers.jsonl",
            observation_journal_path=directory / "observations.jsonl",
        ),
        RECEIPT_KEY,
    )


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


def eligibility(payload: bytes) -> tuple[CurrentEntitlement, DeploymentGateRequest]:
    now = time.time_ns()
    current = entitlement().model_copy(
        update={
            "entitlement_head": "delivery.first_send:entitlement",
            "freshness_leases": (("evidence", now + 10**12),),
            "bounds": entitlement().bounds.model_copy(
                update={
                    "capability_id": "delivery.first_send",
                    "starts_ns": now - 1,
                    "expires_ns": now + 10**12,
                }
            ),
        }
    )
    gate = request(current, "channel-delivery").model_copy(
        update={
            "surface_id": "delivery.first_send",
            "payload_digest": hashlib.sha256(payload).hexdigest(),
        }
    )
    return current, gate


@pytest.mark.parametrize("channel", ["CLI", "TELEGRAM"])
async def test_actual_channel_driver_delivery_reopens_without_model_or_transfer_repeat(
    postgres_server: PostgresServer,
    tmp_path: Path,
    channel: Channel,
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
    transport = binding(tmp_path)
    source = registered(channel, transport)
    identity = NativeIngressIdentityV1(
        tenant_id="t",
        channel=channel,
        registered_source_id=channel,
        replay_id="3",
    )
    run_id = "ingress-run:" + hashlib.sha256(identity.canonical_bytes()).hexdigest()
    model = HermeticModel(
        (
            json.dumps(
                {
                    "kind": "Complete",
                    "tenant": "t",
                    "run_id": run_id,
                    "turn_id": run_id + "/turn/1",
                    "deliveries": [
                        {"payload": [{"kind": "NonAuthoritativeText", "text": "accepted hello"}]}
                    ],
                }
            ).encode(),
        )
    )
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
    receipt = await through_channel(channel, ingress, driver, raw, "3")
    assert receipt.identity == identity and receipt.raw_bytes == raw and receipt.run_id == run_id
    completed = runtime.execution_record(run_id)
    assert completed.state == "SUCCEEDED" and completed.delivery_acceptance is not None
    assert completed.origin == source.origin and len(model.requests) == 1
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
    runtime.import_entitlement(current, signature(current))
    await runtime.commit_delivery_send(PEER, intent.delivery_id, gate)
    claim = await runtime.emit_delivery(PEER, intent.delivery_id)
    assert len(transport.provider.transfers) == 1
    assert transport.provider.transfers[0].ticket.payload == rendered
    assert transport.provider.transfers[0].ticket.provider == "hermetic-effects"
    fresh_transport = binding(tmp_path)
    forbidden_model = HermeticModel(())  # Any invocation raises; there is no fallback.
    fresh, fresh_ingress, fresh_driver = opened(database, source, fresh_transport, forbidden_model)
    replay = await through_channel(channel, fresh_ingress, fresh_driver, raw, "3")
    assert replay == receipt
    assert fresh.execution_record(run_id) == completed
    assert forbidden_model.requests == []
    assert await fresh.emit_delivery(PEER, intent.delivery_id) == claim
    result = await fresh.reconcile_delivery(PEER, intent.delivery_id)
    assert result is not None and result.outcome == "CONFIRMED"
    assert len(fresh_transport.provider.transfers) == 1
    assert len(fresh.read_user_history_at(fresh.snapshot().head, PRINCIPAL)) == 2
