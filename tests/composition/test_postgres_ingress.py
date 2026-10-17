"""Actual PostgreSQL custody and actual local transport boundaries; no live service."""

import asyncio
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import pytest

from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.adapters.driven.native_ingress_cli import CliIngressFrame, NativeCliIngress
from chiplog.adapters.driven.native_ingress_telegram import NativeTelegramIngress
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy, LoopRejected
from chiplog.capabilities.agent_loop.delivery_contracts import (
    ExactHead,
    OriginSelection,
    ProviderRecipient,
)
from chiplog.capabilities.agent_loop.execution_client_transitions import ExecutionRun
from chiplog.capabilities.agent_loop.ingress_client_contracts import NativeIngressReceiptV1
from chiplog.composition.postgres_execution import PostgresExecutionRuntime
from chiplog.composition.postgres_ingress import (
    NativeInboxRecord,
    PostgresIngressService,
    RegisteredIngressSource,
    subject,
)
from chiplog.composition.postgres_planning import PostgresPlanningRuntime
from chiplog.platform.postgres import PostgresIntegrityError, PostgresServer, UserDatabase

KEY = b"stable-issuer-fixture-key"
SECRET = b"operator-secret-fixture"
PEER = "local-cli"


def source(channel: Literal["CLI", "TELEGRAM"]) -> RegisteredIngressSource:
    head = ExactHead(identity=channel, head=channel + ":v1", fingerprint="a" * 64)
    return RegisteredIngressSource(
        tenant_id="t",
        channel=channel,
        source_id=channel,
        principal_id="p",
        issuer_id="trusted:" + channel,
        binding=head,
        origin=OriginSelection(
            ingress_binding=head,
            recipient=ProviderRecipient(
                provider_id="hermetic",
                account_id=channel,
                recipient_id="p",
                canonical_address=channel.encode(),
                endpoint=head,
                credential_binding=head,
            ),
        ),
        transport_subject=subject("uid", os.getuid())
        if channel == "CLI"
        else subject("telegram", "endpoint", "account", 7, 9),
    )


def opened(database: UserDatabase) -> PostgresExecutionRuntime:
    return PostgresExecutionRuntime(
        database,
        "t",
        SECRET,
        principal_id="p",
        credential_id="c",
        session_id="s",
        model=HermeticModel(),
        origin=source("CLI").origin,
    )


async def service(server: PostgresServer) -> PostgresIngressService:
    database = server.user_database("t")
    database.provision()
    await PostgresPlanningRuntime(database, "t", SECRET).bootstrap(
        database_instance_id="db",
        principal_id="p",
        credential_id="c",
        session_id="s",
        token="token",
    )
    return PostgresIngressService(
        opened(database), (source("CLI"), source("TELEGRAM")), issuer_key=KEY
    )


@dataclass
class ObserveDriver:
    """Injected consumer only; root integration must supply the actual shared driver."""

    ingress: PostgresIngressService
    calls: list[str] = field(default_factory=list)

    async def drive(self, peer: str, inbox_id: str) -> ExecutionRun:
        receipt = self.ingress.receipt_for_inbox(peer, inbox_id)
        self.calls.append(inbox_id)
        return self.ingress.runtime.execution_record(receipt.run_id)


def telegram(raw_user: int = 7, text: str = "hello") -> bytes:
    return json.dumps(
        {
            "update_id": 3,
            "message": {
                "message_id": 11,
                "chat": {"id": 9, "type": "private"},
                "from": {"id": raw_user, "is_bot": False},
                "text": text,
            },
        },
        indent=2,
    ).encode()


async def test_native_ingress_retains_exact_raw_and_reopens_same_atomic_run(
    postgres_server: PostgresServer,
) -> None:
    ingress = await service(postgres_server)
    policy = BudgetPolicy()
    raw = b"  hello\r\n"
    submitted = ingress.issuer("CLI")("request:1", raw, policy, source("CLI").transport_subject)
    receipt = await ingress.submit(PEER, submitted, policy)
    batch = ingress.runtime.snapshot().batches[-1]
    assert batch.operation == "ingress.admit"
    assert len(batch.records) == 3
    assert receipt.commit_sequence == batch.sequence
    assert receipt.raw_bytes == raw
    assert ingress.receipt_for_inbox(PEER, receipt.inbox_id) == receipt
    inbox = NativeInboxRecord.model_validate_json(batch.records[0].canonical_bytes)
    assert inbox.submission == submitted
    run = ingress.runtime.execution_record(receipt.run_id)
    assert run.origin == source("CLI").origin
    assert ingress.runtime.read_user_history_at(batch.sequence, "p")[0].text.encode() == raw
    reopened = PostgresIngressService(
        opened(ingress.runtime.database), (source("CLI"), source("TELEGRAM")), issuer_key=KEY
    )
    assert reopened.lookup(PEER, submitted.identity) == receipt
    assert await reopened.submit(PEER, submitted, policy) == receipt
    assert reopened.runtime.snapshot().head == batch.sequence


async def test_ingress_forgery_changed_replay_and_wrong_scope_do_not_admit(
    postgres_server: PostgresServer,
) -> None:
    ingress = await service(postgres_server)
    policy = BudgetPolicy()
    issue = ingress.issuer("CLI")
    submitted = issue("request:1", b"hello", policy, source("CLI").transport_subject)
    forged = submitted.model_copy(update={"raw_bytes": b"forged"})
    before = ingress.runtime.snapshot()
    with pytest.raises(PermissionError, match="witness"):
        await ingress.submit(PEER, forged, policy)
    with pytest.raises(PermissionError, match="peer"):
        await ingress.submit("wrong-peer", submitted, policy)
    assert ingress.runtime.snapshot() == before
    receipt = await ingress.submit(PEER, submitted, policy)
    changed = issue("request:1", b"changed", policy, source("CLI").transport_subject)
    with pytest.raises(LoopRejected, match="replay"):
        await ingress.submit(PEER, changed, policy)
    changed_policy = policy.model_copy(update={"max_turns": policy.max_turns + 1})
    changed_budget = issue("request:1", b"hello", changed_policy, source("CLI").transport_subject)
    with pytest.raises(LoopRejected, match="replay"):
        await ingress.submit(PEER, changed_budget, changed_policy)
    with pytest.raises(PermissionError, match="scope"):
        ingress.lookup(PEER, submitted.identity.model_copy(update={"tenant_id": "other"}))
    assert ingress.lookup(PEER, submitted.identity) == receipt


async def test_admission_rollback_and_lost_ack_resolve_without_another_run(
    postgres_server: PostgresServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ingress = await service(postgres_server)
    policy = BudgetPolicy()
    submission = ingress.issuer("CLI")(
        "request:1", b"hello", policy, source("CLI").transport_subject
    )
    original = UserDatabase.append
    before = ingress.runtime.snapshot()

    def fail_after_append(self: UserDatabase, *args: object, **kwargs: object) -> object:
        result = original(self, *args, **kwargs)  # type: ignore[arg-type]
        raise RuntimeError(f"injected before commit: {result.disposition}")

    with monkeypatch.context() as patch:
        patch.setattr(UserDatabase, "append", fail_after_append)
        with pytest.raises(RuntimeError, match="before commit"):
            await ingress.submit(PEER, submission, policy)
    assert ingress.runtime.snapshot() == before
    assert ingress.lookup(PEER, submission.identity) is None
    receipt = await ingress.submit(PEER, submission, policy)
    # Caller loses the committed response, then reauthenticates through a fresh runtime.
    fresh = PostgresIngressService(
        opened(ingress.runtime.database), (source("CLI"),), issuer_key=KEY
    )
    assert fresh.lookup(PEER, submission.identity) == receipt
    assert await fresh.submit(PEER, submission, policy) == receipt
    assert len(fresh.runtime._runs(fresh.runtime.snapshot())) == 1


async def test_selected_inbox_corruption_is_an_integrity_error(
    postgres_server: PostgresServer,
) -> None:
    ingress = await service(postgres_server)
    policy = BudgetPolicy()
    submission = ingress.issuer("CLI")(
        "request:1", b"hello", policy, source("CLI").transport_subject
    )
    receipt = await ingress.submit(PEER, submission, policy)
    with ingress.runtime.database.connect() as connection:
        connection.execute(
            "UPDATE records SET schema_id=%s WHERE record_id=%s",
            ("chiplog.agent-loop.native-inbox.v999", receipt.inbox_id),
        )
    with pytest.raises(PostgresIntegrityError, match=r"operation=ingress\.read tenant=t") as error:
        ingress.receipt_for_inbox(PEER, receipt.inbox_id)
    assert error.value.__cause__ is not None


async def test_registration_change_at_publication_cut_rejects_whole_admission(
    postgres_server: PostgresServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ingress = await service(postgres_server)
    policy = BudgetPolicy()
    submission = ingress.issuer("CLI")(
        "request:1", b"hello", policy, source("CLI").transport_subject
    )
    original = ingress.runtime.publish_execution_batch
    before = ingress.runtime.snapshot()

    async def changed(*args: object, **kwargs: object) -> object:
        ingress.sources["CLI"] = source("CLI").model_copy(update={"transport_subject": "changed"})
        return await original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(ingress.runtime, "publish_execution_batch", changed)
    with pytest.raises(PermissionError, match="source changed"):
        await ingress.submit(PEER, submission, policy)
    assert ingress.runtime.snapshot() == before


@pytest.mark.parametrize("wrong_uid", [False, True])
async def test_actual_unix_peer_ack_is_after_selected_custody(
    postgres_server: PostgresServer,
    wrong_uid: bool,
) -> None:
    ingress = await service(postgres_server)
    driver = ObserveDriver(ingress)
    adapter = NativeCliIngress(
        ingress,
        driver,
        peer=PEER,
        uid=os.getuid() + int(wrong_uid),
        policy=BudgetPolicy(),
        issue=ingress.issuer("CLI"),
    )
    finished = asyncio.get_running_loop().create_future()

    async def accepted(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await adapter.receive(reader, writer)
            finished.set_result(None)
        except BaseException as error:
            finished.set_exception(error)
        finally:
            # resource-contexts: justified-manual-close — accepted writer has no context manager
            writer.close()
            await writer.wait_closed()

    with tempfile.TemporaryDirectory(dir="/tmp") as directory:
        path = Path(directory) / "ingress.sock"
        server = await asyncio.start_unix_server(accepted, path=path)
        os.chmod(path, 0o600)
        async with server:
            reader, writer = await asyncio.open_unix_connection(path)
            try:
                if wrong_uid:
                    with pytest.raises(PermissionError, match="OS peer"):
                        await finished
                    assert await reader.read() == b""
                    assert driver.calls == []
                    assert ingress.runtime._runs(ingress.runtime.snapshot()) == ()
                    return
                frame = CliIngressFrame(replay_id="cli:1", raw_bytes=b"hello\r\n").canonical_bytes()
                writer.write(len(frame).to_bytes(4, "big") + frame)
                await writer.drain()
                writer.write_eof()
                size = int.from_bytes(await reader.readexactly(4), "big")
                receipt = NativeIngressReceiptV1.model_validate_json(await reader.readexactly(size))
                assert ingress.lookup(PEER, receipt.identity) == receipt
                assert receipt.raw_bytes == b"hello\r\n"
                await finished
                assert driver.calls == [receipt.inbox_id]
            finally:
                # resource-contexts: justified-manual-close — client writer has no context manager
                writer.close()
                await writer.wait_closed()


async def test_webhook_transport_authenticates_before_shared_driver_and_ack(
    postgres_server: PostgresServer,
) -> None:
    ingress = await service(postgres_server)
    driver = ObserveDriver(ingress)
    adapter = NativeTelegramIngress(
        ingress,
        driver,
        peer=PEER,
        tenant_id="t",
        endpoint_id="endpoint",
        account_id="account",
        secret=b"independent-webhook-secret",
        user_id=7,
        chat_id=9,
        policy=BudgetPolicy(),
        issue=ingress.issuer("TELEGRAM"),
    )
    before = ingress.runtime.snapshot()
    for endpoint, account, secret, raw in (
        ("endpoint", "account", b"forged", telegram()),
        ("wrong", "account", b"independent-webhook-secret", telegram()),
        ("endpoint", "wrong", b"independent-webhook-secret", telegram()),
        ("endpoint", "account", b"independent-webhook-secret", telegram(raw_user=8)),
    ):
        with pytest.raises(PermissionError):
            await adapter.receive(
                endpoint_id=endpoint, account_id=account, secret_header=secret, raw=raw
            )
    assert ingress.runtime.snapshot() == before and driver.calls == []
    raw = telegram()
    receipt = await adapter.receive(
        endpoint_id="endpoint",
        account_id="account",
        secret_header=b"independent-webhook-secret",
        raw=raw,
    )
    assert receipt.raw_bytes == raw
    assert ingress.lookup(PEER, receipt.identity) == receipt
    assert ingress.runtime.execution_record(receipt.run_id).origin == source("TELEGRAM").origin
    assert driver.calls == [receipt.inbox_id]
    assert hashlib.sha256(raw).hexdigest() != hashlib.sha256(b"hello").hexdigest()
