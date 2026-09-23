"""Actual retained PRE_AUTH custody, sole writer and exact recovery counterhistories."""

import hashlib
import json
import os
import shutil
import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest

from chiplog.adapters.driven.ingress_retained_source import (
    RetainedSourceError,
    RetainedSourceTransfer,
)
from chiplog.composition.r14_runtime import open_r14_runtime
from chiplog.composition.r17_ingress_authority import IngressAuthority
from chiplog.composition.r17_ingress_history import wire_record
from chiplog.composition.r17_ingress_runtime import open_r17_runtime
from chiplog.platform._owner_publication_contracts import (
    ExactReplayQuery,
    JournalSelectedPublication,
    PublicationRejected,
    SingleOwnerBatch,
)
from chiplog.platform._sqlite import EventAppender, PhysicalPublicationCommand, PublicationResult
from chiplog.platform.authority_reads import capture_authority_storage_state
from chiplog.platform.ingress_custody_records import CustodyRecord, digest
from chiplog.platform.owner_decision_journal import OwnerJournalIntegrityError
from chiplog.platform.owner_publications import (
    OwnerPublicationUncertain,
    PreparedOwnerPublication,
    SelectedOwnerDecision,
)


async def test_actual_token_stage_retry_retains_source_and_exact_restart_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "custody.sqlite"
    async with open_r17_runtime(database) as runtime:
        runtime.provision_retained("slot", b"untrusted CLI bytes")
        source = runtime._retained_source()
        assert source.transfers == ()
        allocation = await runtime.allocate_receipt("slot")
        assert allocation.kind == "COMMITTED"
        assert isinstance(allocation, JournalSelectedPublication)
        assert source.transfers == ()
        raw_path = source.directory / (hashlib.sha256(b"slot").hexdigest() + ".raw")
        raw_identity = (raw_path.stat().st_dev, raw_path.stat().st_ino)
        original_read = os.read
        body_observations: list[str] = []

        def independent_handoff(fd: int, maximum: int) -> bytes:
            file = os.fstat(fd)
            if (file.st_dev, file.st_ino) == raw_identity:
                with closing(sqlite3.connect(database)) as connection:
                    publication = connection.execute(
                        "SELECT record_ids FROM main.publications WHERE tenant_id=? "
                        "AND operation_kind='ingress.allocate_receipt_token' AND idempotency_key=?",
                        (runtime._tenant_id, allocation.command_id),
                    ).fetchone()
                    assert publication == (allocation.complete_records[0].record_id,)
                    row = connection.execute(
                        "SELECT canonical_bytes FROM main.records "
                        "WHERE tenant_id=? AND record_id=?",
                        (runtime._tenant_id, publication[0]),
                    ).fetchone()
                    assert row == (allocation.complete_records[0].canonical_bytes,)
                    body_observations.append(publication[0])
            return original_read(fd, maximum)

        monkeypatch.setattr(os, "read", independent_handoff)
        staged = await runtime.stage_receipt("slot")
        assert staged.kind == "COMMITTED"
        assert body_observations
        observed_transfers: tuple[RetainedSourceTransfer, ...] = tuple(source.transfers)
        assert len(observed_transfers) == 1
        record = runtime.ingress_history().records[-1]
        assert isinstance(record, CustodyRecord)
        assert observed_transfers[0].token_id == record.command.token.token_id
        assert record.resulting_entry.staged_bytes == b"untrusted CLI bytes"
        assert record.command.token.source.endpoint_account_binding.kind == "UNKNOWN_PRE_AUTH"
        retry = await runtime.retry_receipt("slot")
        assert retry.kind == "COMMITTED"
        retry_record = runtime.ingress_history().records[-1]
        assert isinstance(retry_record, CustodyRecord)
        custody = retry_record.resulting_entry.custody
        assert custody is not None and custody.kind == "RETRY_WITH_SOURCE_CUSTODY"
        assert source.verify(source.observe("slot"))
        with closing(sqlite3.connect(database)) as connection:
            assert connection.execute("SELECT COUNT(*) FROM evidence_inbox").fetchone() == (0,)
        original_history = runtime.ingress_history().records
    async with open_r17_runtime(database) as restarted:
        result = await restarted.receive_retained("slot")
        assert result.kind == "EXACT_REPLAY"
        assert isinstance(result, JournalSelectedPublication)
        assert isinstance(retry, JournalSelectedPublication)
        assert result.complete_records == retry.complete_records
        assert restarted.ingress_history().records == original_history
        assert restarted._retained_source().transfers == observed_transfers


async def test_missing_token_never_transfers_and_changed_limit_conflicts(tmp_path: Path) -> None:
    async with open_r17_runtime(tmp_path / "missing.sqlite") as runtime:
        runtime.provision_retained("slot", b"abc")
        with pytest.raises(ValueError, match="allocation"):
            await runtime.stage_receipt("slot")
        assert runtime._retained_source().transfers == ()
        assert (await runtime.allocate_receipt("slot", 8)).kind == "COMMITTED"
        assert (await runtime.allocate_receipt("slot", 9)).kind == "CONFLICT"
        assert runtime._retained_source().transfers == ()


async def test_existing_r14_database_can_initialize_fixed_source_root(tmp_path: Path) -> None:
    database = tmp_path / "upgraded.sqlite"
    async with open_r14_runtime(database):
        pass
    async with open_r17_runtime(database) as runtime:
        runtime.provision_retained("slot", b"abc")
        assert (await runtime.receive_retained("slot")).kind == "COMMITTED"


async def test_source_mutation_at_writer_admission_cannot_select_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with open_r17_runtime(tmp_path / "race.sqlite") as runtime:
        runtime.provision_retained("slot", b"abc")
        source = runtime._retained_source()
        original = runtime._appender.submit

        async def mutate(command: PhysicalPublicationCommand) -> PublicationResult:
            if command.operation_kind == "ingress.allocate_receipt_token":
                raw = source.directory / (hashlib.sha256(b"slot").hexdigest() + ".raw")
                raw.write_bytes(b"xyz")
            return await original(command)

        monkeypatch.setattr(runtime._appender, "submit", mutate)
        result = await runtime.allocate_receipt("slot")
        assert isinstance(result, PublicationRejected)
        assert result.kind == "STALE"
        assert runtime.ingress_history().records == ()
        assert source.transfers == ()


@pytest.mark.parametrize("fault", ["before_commit", "after_commit"])
async def test_selected_allocation_fault_recovers_exact_original_without_transfer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: Literal["before_commit", "after_commit"],
) -> None:
    database = tmp_path / "recovery.sqlite"
    async with open_r17_runtime(database) as runtime:
        runtime.provision_retained("slot", b"abc")
        original = runtime._appender.submit

        async def fail(command: PhysicalPublicationCommand) -> PublicationResult:
            if command.operation_kind == "ingress.allocate_receipt_token":
                command = replace(command, fault=fault)
            return await original(command)

        monkeypatch.setattr(runtime._appender, "submit", fail)
        with pytest.raises(OwnerPublicationUncertain):
            await runtime.allocate_receipt("slot")
        selected = runtime._owner_decisions().snapshot().decisions[-1]
        assert runtime._retained_source().transfers == ()
    async with open_r17_runtime(database) as reopened:
        result = await reopened.allocate_receipt("slot")
        assert isinstance(result, JournalSelectedPublication)
        assert result.kind == "EXACT_REPLAY"
        assert result.complete_records == selected.prepared.request.complete_records
        assert reopened._retained_source().transfers == ()
        assert (await reopened.stage_receipt("slot")).kind == "COMMITTED"


async def test_startup_recovers_historical_token_even_when_source_root_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "missing-source.sqlite"
    async with open_r17_runtime(database) as runtime:
        runtime.provision_retained("slot", b"abc")
        directory = runtime._retained_source().directory
        original = runtime._appender.submit

        async def fail(command: PhysicalPublicationCommand) -> PublicationResult:
            return await original(replace(command, fault="before_commit"))

        monkeypatch.setattr(runtime._appender, "submit", fail)
        with pytest.raises(OwnerPublicationUncertain):
            await runtime.allocate_receipt("slot")
    shutil.rmtree(directory)
    async with open_r17_runtime(database) as reopened:
        assert len(reopened.ingress_history().records) == 1
        with pytest.raises((OSError, ValueError)):
            await reopened.stage_receipt("slot")
        assert not directory.exists()


async def test_unissued_equal_batch_does_not_gain_private_authority(tmp_path: Path) -> None:
    async with open_r17_runtime(tmp_path / "forged.sqlite") as runtime:
        runtime.provision_retained("slot", b"abc")
        assert (await runtime.allocate_receipt("slot")).kind == "COMMITTED"
        authority = IngressAuthority(runtime, "slot")
        token = runtime.ingress_history().records[0].command.token
        batch = authority.issue("ingress.publish_custody_successor", token, None)
        assert isinstance(await authority.prepare(batch.model_copy()), PublicationRejected)
        prepared = await authority.prepare(batch)
        assert isinstance(prepared, PreparedOwnerPublication)
        assert authority.check_prepared(replace(prepared)) == "DENIED"


@pytest.mark.parametrize(
    "mutation", ["foreign_schema_owner", "payload_alias", "extra_publication", "missing_frontier"]
)
async def test_history_rejects_anchored_orphan_schema_and_extra_publication(
    tmp_path: Path,
    mutation: str,
) -> None:
    database = tmp_path / "orphans.sqlite"
    async with open_r17_runtime(database) as runtime:
        runtime.provision_retained("slot", b"abc")
        selected = await runtime.allocate_receipt("slot")
        assert isinstance(selected, JournalSelectedPublication)
        with closing(sqlite3.connect(database)) as connection, connection:
            if mutation in ("foreign_schema_owner", "payload_alias"):
                connection.execute(
                    "INSERT INTO records VALUES (?,?,?,?,?,?)",
                    (
                        runtime._tenant_id,
                        "orphan",
                        "effects",
                        "chiplog.ingress.custody-record.v1"
                        if mutation == "foreign_schema_owner"
                        else "foreign.schema",
                        selected.complete_records[0].canonical_bytes,
                        selected.tenant_commit_sequence + 1,
                    ),
                )
            elif mutation == "missing_frontier":
                connection.execute(
                    "DELETE FROM tenant_heads WHERE tenant_id=?", (runtime._tenant_id,)
                )
            else:
                connection.execute(
                    "INSERT INTO publications VALUES (?,?,?,?,?,?)",
                    (
                        runtime._tenant_id,
                        "unrelated.operation",
                        "extra",
                        "f" * 64,
                        selected.tenant_commit_sequence,
                        selected.complete_records[0].record_id,
                    ),
                )
        runtime._commitment_journal.commit(
            runtime._tenant_id, capture_authority_storage_state(database)[0]
        )
        with pytest.raises(OwnerJournalIntegrityError):
            runtime.ingress_history()


async def test_malformed_selected_output_fails_before_any_startup_recovery_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "selected-malformed.sqlite"
    async with open_r17_runtime(database) as runtime:
        runtime.provision_retained("slot", b"abc")
        journal = runtime._owner_decisions()
        original_select = journal.select

        def select_bad(
            prepared: PreparedOwnerPublication, commitment: str
        ) -> SelectedOwnerDecision:
            request = prepared.request
            assert isinstance(request, SingleOwnerBatch)
            parsed = CustodyRecord.model_validate_json(request.complete_records[0].canonical_bytes)
            changed = parsed.model_copy(
                update={
                    "resulting_entry": parsed.resulting_entry.model_copy(
                        update={"staged_bytes": b"forged", "staged_digest": digest(b"forged")},
                    )
                }
            )
            wire = wire_record(changed)
            batch = request.model_copy(
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
            original_select(replace(prepared, request=batch), commitment)
            raise RuntimeError("selected malformed output before SQL commit")

        monkeypatch.setattr(journal, "select", select_bad)
        with pytest.raises(OwnerPublicationUncertain):
            await runtime.allocate_receipt("slot")
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
            pytest.fail("malformed selected output must not be exposed")
    assert writes == []


async def test_retry_and_restart_do_not_release_item_reserves(tmp_path: Path) -> None:
    database = tmp_path / "reserves.sqlite"
    async with open_r17_runtime(database) as runtime:
        for index in range(8):
            slot = str(index)
            runtime.provision_retained(slot, b"x")
            assert (await runtime.allocate_receipt(slot)).kind == "COMMITTED"
            assert (await runtime.retry_receipt(slot)).kind == "COMMITTED"
    async with open_r17_runtime(database) as reopened:
        assert len(reopened.ingress_history().custody.entries) == 8
        with pytest.raises(RetainedSourceError, match="reserve exhausted"):
            reopened.provision_retained("ninth", b"x")
        assert reopened._retained_source().transfers == ()


async def test_denied_current_replay_cannot_recover_selected_absent_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "denied-recovery.sqlite"
    async with open_r17_runtime(database) as runtime:
        runtime.provision_retained("slot", b"abc")
        original = runtime._appender.submit

        async def fail(command: PhysicalPublicationCommand) -> PublicationResult:
            return await original(replace(command, fault="before_commit"))

        monkeypatch.setattr(runtime._appender, "submit", fail)
        with pytest.raises(OwnerPublicationUncertain):
            await runtime.allocate_receipt("slot")

        def deny(self: IngressAuthority, query: ExactReplayQuery) -> PublicationRejected:
            return PublicationRejected(
                kind="DENIED",
                tenant_id=query.identity.tenant_id,
                command_id=query.identity.command_id,
                reason="expired actual invocation",
            )

        async def forbidden(command: PhysicalPublicationCommand) -> PublicationResult:
            raise AssertionError("denied current invocation cannot submit recovery")

        monkeypatch.setattr(IngressAuthority, "authenticate_replay", deny)
        monkeypatch.setattr(runtime._appender, "submit", forbidden)
        result = await runtime.allocate_receipt("slot")
        assert result.kind == "DENIED"
        assert runtime._retained_source().transfers == ()
        with closing(sqlite3.connect(database)) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM publications WHERE operation_kind LIKE 'ingress.%'"
            ).fetchone() == (0,)


async def test_competing_durable_loop_pending_blocks_owner_recovery_guard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with open_r17_runtime(tmp_path / "competing.sqlite") as runtime:
        runtime.provision_retained("slot", b"abc")
        original = runtime._appender.submit

        async def fail(command: PhysicalPublicationCommand) -> PublicationResult:
            return await original(replace(command, fault="before_commit"))

        monkeypatch.setattr(runtime._appender, "submit", fail)
        with pytest.raises(OwnerPublicationUncertain):
            await runtime.allocate_receipt("slot")
        selected = runtime._owner_decisions().snapshot().decisions[-1]
        authority = IngressAuthority(runtime, "slot")
        assert authority.check_selected_predecessor(selected)
        runtime._append_decision({"version": 1, "kind": "DECIDED", "operation_id": "rival-loop"})
        assert not authority.check_selected_predecessor(selected)
