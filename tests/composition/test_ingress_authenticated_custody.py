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
from types import SimpleNamespace
from typing import Literal

import pytest

from chiplog.capabilities.deployment_trust.cli_custody_contracts import (
    CliCustodyOffer,
    CliCustodyResponse,
)
from chiplog.composition.r14 import open_r14_loop
from chiplog.composition.r17_admission_records import (
    SCHEMA_CAPACITY,
    SCHEMA_EPOCH_V2,
    SCHEMA_READY,
    selected_aggregate,
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
    selected_records,
)
from tests.support.composition.plan_effect_publication import prepare_display, response


def _selected_bytes(decision: SelectedOwnerDecision) -> bytes:
    return json.dumps(
        {
            "prepared": {
                "request": decision.prepared.request.model_dump(mode="json"),
                "issuance_id": decision.prepared.issuance_id,
                "fence_generation": decision.prepared.fence_generation,
                "fence_frontier": decision.prepared.fence_frontier,
                "predecessor_commitment": decision.prepared.predecessor_commitment,
            },
            "decision_id": decision.decision_id,
            "decision_head": decision.decision_head,
            "decision_fingerprint": decision.decision_fingerprint,
            "resulting_commitment": decision.resulting_commitment,
            "tenant_commit_sequence": decision.tenant_commit_sequence,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


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
        assert tuple(row.schema_id for row in result.complete_records) == (
            "chiplog.ingress.authenticated-record.v2",
            SCHEMA_EPOCH_V2,
            SCHEMA_CAPACITY,
            SCHEMA_READY,
        )
        with sqlite3.connect(database) as connection:
            aggregate_rows = connection.execute(
                "SELECT schema_id FROM records WHERE tenant_id=? AND commit_sequence=? "
                "ORDER BY rowid",
                ("hermetic-tenant", result.tenant_commit_sequence),
            ).fetchall()
        assert aggregate_rows == [(row.schema_id,) for row in result.complete_records]
        before = runtime.ingress_history().records
    async with open_r17_runtime(database) as reopened:
        assert reopened.read_admitted_inbox(token) == observed
        async with reopened.cli_custody("slot") as admission:
            client = asyncio.create_task(_client(admission.socket.path))
            replay = await admission.admit()
            await client
        assert replay.kind == "EXACT_REPLAY"
        assert reopened.ingress_history().records == before


async def test_ordinary_first_then_cli_reconstructs_one_chronological_admission(
    tmp_path: Path,
) -> None:
    database = tmp_path / "mixed-ordinary-cli.sqlite"
    async with open_r14_loop(database, responses=(response(),)) as loop:
        runtime, display = await prepare_display(loop)
        ordinary = await runtime.publish_effect(
            "hermetic-ingress", display.display_id, display.display_digest, display.adoption_act_id
        )
        assert isinstance(ordinary, JournalSelectedPublication)
        assert tuple(row.schema_id for row in ordinary.complete_records[-2:]) == (
            SCHEMA_EPOCH_V2,
            SCHEMA_CAPACITY,
        )
    async with open_r17_runtime(database) as runtime:
        token = await _stage(runtime)
        async with runtime.cli_custody("slot") as admission:
            client = asyncio.create_task(_client(admission.socket.path))
            cli = await admission.admit()
            await client
        assert isinstance(cli, JournalSelectedPublication)
        assert runtime.read_admitted_inbox(token) is not None
        aggregate = selected_aggregate(runtime._owner_decisions().snapshot().decisions)
        assert tuple(claim.source_class for claim in aggregate.claims) == ("ORDINARY", "CLI")
        assert len(runtime.ingress_history().records) == 3
    async with open_r17_runtime(database) as reopened:
        aggregate = selected_aggregate(reopened._owner_decisions().snapshot().decisions)
        assert tuple(claim.source_class for claim in aggregate.claims) == ("ORDINARY", "CLI")
        assert reopened.read_admitted_inbox(token) is not None


async def test_cli_v2_first_then_ordinary_reconstructs_one_chronological_admission(
    tmp_path: Path,
) -> None:
    database = tmp_path / "mixed-cli-ordinary.sqlite"
    async with open_r17_runtime(database) as runtime:
        token = await _stage(runtime)
        async with runtime.cli_custody("slot") as admission:
            client = asyncio.create_task(_client(admission.socket.path))
            cli = await admission.admit()
            await client
        assert isinstance(cli, JournalSelectedPublication)
        assert SCHEMA_EPOCH_V2 in {row.schema_id for row in cli.complete_records}
    async with open_r14_loop(database, responses=(response(),)) as loop:
        runtime, display = await prepare_display(loop)
        ordinary = await runtime.publish_effect(
            "hermetic-ingress", display.display_id, display.display_digest, display.adoption_act_id
        )
        assert isinstance(ordinary, JournalSelectedPublication)
        assert ordinary.complete_records[-1].schema_id == SCHEMA_CAPACITY
        assert sum(row.schema_id == SCHEMA_EPOCH_V2 for row in ordinary.complete_records) == 0
        aggregate = selected_aggregate(runtime._owner_decisions().snapshot().decisions)
        assert tuple(claim.source_class for claim in aggregate.claims) == ("CLI", "ORDINARY")
    async with open_r17_runtime(database) as reopened:
        assert reopened.read_admitted_inbox(token) is not None
        aggregate = selected_aggregate(reopened._owner_decisions().snapshot().decisions)
        assert tuple(claim.source_class for claim in aggregate.claims) == ("CLI", "ORDINARY")


async def test_selected_v1_cli_admission_replays_exact_bytes_then_keeps_v1_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stored v1 epoch fixes its profile across reopen; it never upgrades in place."""
    from chiplog.composition import r17_authenticated_custody

    database = tmp_path / "selected-v1.sqlite"
    original_builder = r17_authenticated_custody.cli_batch_events

    def v1_builder(**kwargs: object):
        return original_builder(**kwargs, epoch_schema="v1")  # type: ignore[arg-type]

    async with open_r17_runtime(database) as runtime:
        await _stage(runtime)
        with monkeypatch.context() as patch:
            patch.setattr(r17_authenticated_custody, "cli_batch_events", v1_builder)
            async with runtime.cli_custody("slot") as admission:
                client = asyncio.create_task(_client(admission.socket.path))
                first = await admission.admit()
                await client
        assert isinstance(first, JournalSelectedPublication)
        decision = runtime._owner_decisions().snapshot().decisions[-1]
        assert (
            decision.prepared.request.complete_records[1].schema_id
            == "chiplog.ingress.admission-epoch.v1"
        )
        selected_before = _selected_bytes(decision)
        with sqlite3.connect(database) as connection:
            physical_before = tuple(
                connection.execute(
                    "SELECT record_id, canonical_bytes FROM records WHERE tenant_id=? "
                    "AND commit_sequence=? ORDER BY rowid",
                    ("hermetic-tenant", decision.tenant_commit_sequence),
                )
            )
    async with open_r17_runtime(database) as reopened:
        restored = reopened._owner_decisions().lookup(
            "hermetic-tenant", decision.prepared.request.identity.command_id
        )
        assert restored is not None and _selected_bytes(restored) == selected_before
        with sqlite3.connect(database) as connection:
            assert (
                tuple(
                    connection.execute(
                        "SELECT record_id, canonical_bytes FROM records WHERE tenant_id=? "
                        "AND commit_sequence=? ORDER BY rowid",
                        ("hermetic-tenant", decision.tenant_commit_sequence),
                    )
                )
                == physical_before
            )
        reopened.provision_retained("second", b"v1 second item")
        assert (await reopened.allocate_receipt("second")).kind == "COMMITTED"
        assert (await reopened.stage_receipt("second")).kind == "COMMITTED"
        async with reopened.cli_custody("second") as admission:
            client = asyncio.create_task(_client(admission.socket.path))
            second = await admission.admit()
            await client
        assert isinstance(second, JournalSelectedPublication)
        assert "chiplog.ingress.admission-epoch.v2" not in {
            item.schema_id for item in second.complete_records
        }
    async with open_r14_loop(database, responses=(response(),)) as loop:
        runtime, display = await prepare_display(loop)
        with pytest.raises(ValueError, match="epoch differs"):
            await runtime.publish_effect(
                "hermetic-ingress",
                display.display_id,
                display.display_digest,
                display.adoption_act_id,
            )


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


async def test_cli_capacity_claim_rejects_n_plus_one_without_selected_partial_batch(
    tmp_path: Path,
) -> None:
    async with open_r17_runtime(tmp_path / "capacity.sqlite") as runtime:
        for slot in ("capacity-1", "capacity-2"):
            runtime.provision_retained(slot, b"x" * 2048)
            assert (await runtime.allocate_receipt(slot)).kind == "COMMITTED"
            assert (await runtime.stage_receipt(slot)).kind == "COMMITTED"
            async with runtime.cli_custody(slot) as admission:
                client = asyncio.create_task(_client(admission.socket.path))
                assert (await admission.admit()).kind == "COMMITTED"
                await client
        aggregate = selected_aggregate(runtime._owner_decisions().snapshot().decisions)
        bounds = tuple(item.bound for item in aggregate.ready if item.source_class == "CLI")
        assert len(bounds) == 2
        assert bounds[0].absolute_selection_deadline_slot - bounds[0].origin_scheduler_slot == 16
        assert bounds[1].absolute_selection_deadline_slot - bounds[1].origin_scheduler_slot == 32
        runtime.provision_retained("capacity-3", b"over-capacity")
        assert (await runtime.allocate_receipt("capacity-3")).kind == "COMMITTED"
        assert (await runtime.stage_receipt("capacity-3")).kind == "COMMITTED"
        selected = runtime._owner_decisions().snapshot()
        async with runtime.cli_custody("capacity-3") as admission:
            client = asyncio.create_task(_client(admission.socket.path))
            with pytest.raises(ValueError, match="reserve exhausted"):
                await admission.admit()
            await client
        assert runtime._owner_decisions().snapshot() == selected


async def test_selected_records_preserve_cli_batch_bytes_and_reject_unknown_request(
    tmp_path: Path,
) -> None:
    async with open_r17_runtime(tmp_path / "selected-records.sqlite") as runtime:
        await _stage(runtime)
        async with runtime.cli_custody("slot") as admission:
            client = asyncio.create_task(_client(admission.socket.path))
            result = await admission.admit()
            await client
        assert isinstance(result, JournalSelectedPublication)
        decision = runtime._owner_decisions().snapshot().decisions[-1]
        assert selected_records(decision) == result.complete_records
        assert tuple(item.canonical_bytes for item in selected_records(decision)) == tuple(
            item.canonical_bytes for item in result.complete_records
        )

    unknown = SimpleNamespace(prepared=SimpleNamespace(request=SimpleNamespace(kind="UNKNOWN")))
    with pytest.raises(ValueError, match="unsupported selected publication kind"):
        selected_aggregate((unknown,))  # type: ignore[arg-type]


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
