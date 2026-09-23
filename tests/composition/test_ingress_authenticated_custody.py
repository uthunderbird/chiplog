"""Actual Unix peer, isolated trust owner, selected writer and restart observations."""

import asyncio
import hashlib
import json
import shutil
import sqlite3
import struct
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest

from chiplog.capabilities.deployment_trust.cli_custody_contracts import (
    CliCustodyOffer,
    CliCustodyResponse,
)
from chiplog.composition.r17_ingress_history import wire_record
from chiplog.composition.r17_ingress_runtime import R17IngressRuntime, open_r17_runtime
from chiplog.platform._owner_publication_contracts import (
    JournalSelectedPublication,
    PublicationRejected,
    SingleOwnerBatch,
)
from chiplog.platform._sqlite import EventAppender, PhysicalPublicationCommand, PublicationResult
from chiplog.platform.ingress_authenticated_contracts import AuthenticatedCustodyRecord
from chiplog.platform.ingress_custody_records import digest
from chiplog.platform.owner_decision_journal import OwnerJournalIntegrityError
from chiplog.platform.owner_publications import (
    OwnerPublicationUncertain,
    PreparedOwnerPublication,
    SelectedOwnerDecision,
)


async def _client(path: Path, *, mutation: str = "none") -> CliCustodyOffer:
    reader, writer = await asyncio.open_unix_connection(path)
    try:
        size = struct.unpack("!I", await reader.readexactly(4))[0]
        assert 0 < size <= 262144
        raw = await reader.readexactly(size)
        offer = CliCustodyOffer.model_validate_json(raw)
        assert offer.canonical_bytes() == raw
        response = CliCustodyResponse(
            challenge_fingerprint=hashlib.sha256(offer.challenge.canonical_bytes()).hexdigest()
        ).canonical_bytes()
        if mutation == "challenge":
            response = CliCustodyResponse(challenge_fingerprint="0" * 64).canonical_bytes()
        elif mutation == "peer":
            value = json.loads(response)
            value["peer_uid"] = 0
            response = json.dumps(value).encode()
        elif mutation == "token":
            value = json.loads(response)
            value["token_id"] = offer.scope.token_id + ":other"
            response = json.dumps(value).encode()
        elif mutation == "noncanonical":
            response += b" "
        elif mutation == "oversize":
            response = b"x" * 4097
        writer.write(struct.pack("!I", len(response)) + response)
        if mutation == "trailing":
            writer.write(b"second-frame")
        await writer.drain()
        writer.write_eof()
        return offer
    finally:
        # Shutdown-write has ended the one response. Closing after this fixture's
        # flush leaves OS credentials observable on the accepted server socket.
        writer.close()
        await writer.wait_closed()


async def _stage(runtime: R17IngressRuntime) -> str:
    runtime.provision_retained("slot", b"\xff\x00retained CLI command")
    assert (await runtime.allocate_receipt("slot")).kind == "COMMITTED"
    assert (await runtime.stage_receipt("slot")).kind == "COMMITTED"
    return runtime.ingress_history().custody.entries[0].token.token_id


async def test_actual_peer_admits_one_embedded_inbox_and_replays_after_restart(
    tmp_path: Path,
) -> None:
    database = tmp_path / "cli.sqlite"
    async with open_r17_runtime(database) as runtime:
        token = await _stage(runtime)
        transfers = runtime._retained_source().transfers
        async with runtime.cli_custody("slot") as admission:
            client = asyncio.create_task(_client(admission.socket.path))
            result = await admission.admit()
            offer = await client
        assert isinstance(result, JournalSelectedPublication) and result.kind == "COMMITTED"
        observed = runtime.read_admitted_inbox(token)
        assert observed is not None
        assert observed.record.inbox.raw_bytes == b"\xff\x00retained CLI command"
        assert (
            observed.record.inbox.token.source.endpoint_account_binding.kind == "UNKNOWN_PRE_AUTH"
        )
        assert observed.record.command.token.token_id == offer.scope.token_id
        assert runtime._retained_source().transfers == transfers
        assert (await runtime.retry_receipt("slot")).kind == "CONFLICT"
        with sqlite3.connect(database) as connection:
            actual = connection.execute(
                "SELECT canonical_bytes FROM records WHERE record_id=?",
                (observed.physical_record.head,),
            ).fetchone()
        assert actual == (result.complete_records[0].canonical_bytes,)
        before = runtime.ingress_history().records
    async with open_r17_runtime(database) as reopened:
        assert reopened.read_admitted_inbox(token) == observed
        async with reopened.cli_custody("slot") as admission:
            client = asyncio.create_task(_client(admission.socket.path))
            replay = await admission.admit()
            await client
        assert replay.kind == "EXACT_REPLAY"
        assert reopened.ingress_history().records == before


@pytest.mark.parametrize(
    "mutation", ["challenge", "peer", "token", "noncanonical", "oversize", "trailing"]
)
async def test_forged_control_frame_never_selects_custody(tmp_path: Path, mutation: str) -> None:
    async with open_r17_runtime(tmp_path / "deny.sqlite") as runtime:
        token = await _stage(runtime)
        before = runtime._owner_decisions().snapshot()
        async with runtime.cli_custody("slot") as admission:
            client = asyncio.create_task(_client(admission.socket.path, mutation=mutation))
            with pytest.raises(ValueError):
                await admission.admit()
            await client
        assert runtime._owner_decisions().snapshot() == before
        assert runtime.read_admitted_inbox(token) is None


@pytest.mark.parametrize("mutation", ["source", "endpoint"])
async def test_changed_actual_source_at_writer_cut_cannot_select_authenticated_custody(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    async with open_r17_runtime(tmp_path / "cut.sqlite") as runtime:
        token = await _stage(runtime)
        before = runtime._owner_decisions().snapshot()
        original = runtime._appender.submit
        source = runtime._retained_source()
        async with runtime.cli_custody("slot") as admission:

            async def mutate(command: PhysicalPublicationCommand) -> PublicationResult:
                if command.operation_kind == "ingress.publish_custody_successor":
                    if mutation == "source":
                        raw = source.directory / (hashlib.sha256(b"slot").hexdigest() + ".raw")
                        raw.write_bytes(b"changed retained source")
                    else:
                        admission.socket.path.unlink()
                return await original(command)

            monkeypatch.setattr(runtime._appender, "submit", mutate)
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(_client(admission.socket.path))
                result = await admission.admit()
        assert isinstance(result, PublicationRejected) and result.kind == "STALE"
        assert runtime._owner_decisions().snapshot() == before
        assert runtime.read_admitted_inbox(token) is None


async def test_copied_authentication_and_batch_do_not_gain_private_issuance(tmp_path: Path) -> None:
    async with open_r17_runtime(tmp_path / "private.sqlite") as runtime:
        token = await _stage(runtime)
        async with runtime.cli_custody("slot") as admission:
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(_client(admission.socket.path))
                await admission.authenticate()
            with pytest.raises(ValueError, match="unissued"):
                admission.socket.verify(admission._request.model_copy())
            batch = admission.issue_authenticated()
            assert isinstance(await admission.prepare(batch.model_copy()), PublicationRejected)
            prepared = await admission.prepare(batch)
            assert isinstance(prepared, PreparedOwnerPublication)
            assert admission.check_prepared(replace(prepared)) == "DENIED"
        assert runtime.read_admitted_inbox(token) is None


@pytest.mark.parametrize("cancelled", [False, True])
async def test_unfinished_peer_is_closed_and_joined_on_context_exit(
    tmp_path: Path, cancelled: bool
) -> None:
    async with open_r17_runtime(tmp_path / "lifetime.sqlite") as runtime:
        token = await _stage(runtime)
        before = runtime._owner_decisions().snapshot()
        with pytest.raises(asyncio.CancelledError) if cancelled else nullcontext():
            async with asyncio.timeout(1):
                async with runtime.cli_custody("slot") as admission:
                    path = admission.socket.path
                    reader, writer = await asyncio.open_unix_connection(path)
                    size = struct.unpack("!I", await reader.readexactly(4))[0]
                    await reader.readexactly(size)
                    task = admission.socket._exchange_task
                    if cancelled:
                        raise asyncio.CancelledError
        assert task is not None and task.done()
        assert not path.exists()
        assert await reader.read() == b""
        writer.close()
        await writer.wait_closed()
        assert runtime._owner_decisions().snapshot() == before
        assert runtime.read_admitted_inbox(token) is None


@pytest.mark.parametrize("fault", ["before_commit", "after_commit"])
async def test_selected_authenticated_custody_recovers_without_peer_or_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: Literal["before_commit", "after_commit"]
) -> None:
    database = tmp_path / "crash.sqlite"
    async with open_r17_runtime(database) as runtime:
        token = await _stage(runtime)
        source_path = runtime._retained_source().directory
        original = runtime._appender.submit

        async def fail(command: PhysicalPublicationCommand) -> PublicationResult:
            if command.operation_kind == "ingress.publish_custody_successor":
                command = replace(command, fault=fault)
            return await original(command)

        monkeypatch.setattr(runtime._appender, "submit", fail)
        async with runtime.cli_custody("slot") as admission:
            client = asyncio.create_task(_client(admission.socket.path))
            with pytest.raises(OwnerPublicationUncertain):
                await admission.admit()
            await client
        selected = runtime._owner_decisions().snapshot().decisions[-1]
    shutil.rmtree(source_path)
    async with open_r17_runtime(database) as reopened:
        observed = reopened.read_admitted_inbox(token)
        assert observed is not None
        assert (
            observed.physical_record.head == selected.prepared.request.complete_records[0].record_id
        )
        assert observed.record.inbox.raw_bytes == b"\xff\x00retained CLI command"


async def test_selected_retry_cannot_be_promoted_by_authenticated_peer(tmp_path: Path) -> None:
    async with open_r17_runtime(tmp_path / "retry.sqlite") as runtime:
        token = await _stage(runtime)
        assert (await runtime.retry_receipt("slot")).kind == "COMMITTED"
        before = runtime.ingress_history().records
        async with runtime.cli_custody("slot") as admission:
            client = asyncio.create_task(_client(admission.socket.path))
            result = await admission.admit()
            await client
        assert result.kind == "CONFLICT"
        assert runtime.ingress_history().records == before
        assert runtime.read_admitted_inbox(token) is None


@pytest.mark.parametrize("mutation", ["inbox_bytes", "authentication_reference"])
async def test_malformed_selected_v2_fails_before_recovery_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    database = tmp_path / "malformed.sqlite"
    async with open_r17_runtime(database) as runtime:
        await _stage(runtime)
        journal = runtime._owner_decisions()
        original_select = journal.select

        def select_bad(
            prepared: PreparedOwnerPublication, commitment: str
        ) -> SelectedOwnerDecision:
            batch = prepared.request
            assert isinstance(batch, SingleOwnerBatch)
            record = AuthenticatedCustodyRecord.model_validate_json(
                batch.complete_records[0].canonical_bytes
            )
            changed = record.model_copy(
                update={
                    "inbox": record.inbox.model_copy(
                        update={"raw_bytes": b"forged raw"}
                        if mutation == "inbox_bytes"
                        else {"authentication_request_fingerprint": "f" * 64}
                    )
                }
            )
            wire = wire_record(changed)
            altered = batch.model_copy(
                update={
                    "complete_records": (wire,),
                    "complete_batch_fingerprint": digest(
                        json.dumps(
                            [wire.model_dump(mode="json")],
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode()
                    ),
                }
            )
            original_select(replace(prepared, request=altered), commitment)
            raise RuntimeError("selected corrupted v2 before physical materialization")

        monkeypatch.setattr(journal, "select", select_bad)
        async with runtime.cli_custody("slot") as admission, asyncio.TaskGroup() as tasks:
            tasks.create_task(_client(admission.socket.path))
            with pytest.raises(OwnerPublicationUncertain):
                await admission.admit()
    writes: list[PhysicalPublicationCommand] = []
    original_submit = EventAppender.submit

    async def observed_submit(
        self: EventAppender, command: PhysicalPublicationCommand
    ) -> PublicationResult:
        writes.append(command)
        return await original_submit(self, command)

    monkeypatch.setattr(EventAppender, "submit", observed_submit)
    with pytest.raises(OwnerJournalIntegrityError):
        async with open_r17_runtime(database):
            pytest.fail("corrupted v2 custody must fail before recovery")
    assert writes == []
