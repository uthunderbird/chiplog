"""Checkpoint writer ordering and mandatory mutation admission."""

import hashlib
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from chiplog.platform._sqlite import (
    AuthorityMutationAdmissionError,
    CheckpointBundleAdmissionError,
    EventAppender,
    FenceAdvanceCommand,
    PhysicalPublicationCommand,
    PhysicalRecord,
    SQLiteMaterializer,
)
from chiplog.platform.authority_gate import AuthorityGate, checked_file_identity
from chiplog.platform.authority_reads import capture_authority_snapshot_bytes


def _command(*, hook=None) -> PhysicalPublicationCommand:
    payload = b"seal"
    return PhysicalPublicationCommand(
        "tenant",
        "seal",
        "operation",
        "request",
        0,
        "fence",
        0,
        0,
        (PhysicalRecord("seal", "owner", "schema", payload, hashlib.sha256(payload).hexdigest()),),
        authority_checkpoint_guard=hook,
    )


async def test_checkpoint_hook_sees_writer_postimage_and_reader_predecessor(tmp_path: Path) -> None:
    database = tmp_path / "authority.sqlite"
    observed: list[bytes] = []
    with SQLiteMaterializer(database, record_contracts={"owner": "schema"}) as store:
        async with EventAppender(store, capacity=1) as appender:
            await appender.advance_fence(FenceAdvanceCommand("tenant", "fence", 0))

            def hook(resulting: str, raw: bytes) -> None:
                assert resulting == hashlib.sha256(raw).hexdigest()
                assert raw == capture_authority_snapshot_bytes(store._connection, "tenant")
                with sqlite3.connect(database) as reader:
                    assert reader.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 0
                observed.append(raw)

            assert (await appender.submit(_command(hook=hook))).disposition == "COMMITTED"
    assert observed


async def test_checkpoint_hook_failure_rolls_back_sql_before_decision(tmp_path: Path) -> None:
    database = tmp_path / "authority.sqlite"
    decisions: list[str] = []
    with SQLiteMaterializer(database, record_contracts={"owner": "schema"}) as store:
        async with EventAppender(store, capacity=1) as appender:
            await appender.advance_fence(FenceAdvanceCommand("tenant", "fence", 0))

            def hook(_: str, __: bytes) -> None:
                raise RuntimeError("stage failed")

            def decide(resulting: str) -> None:
                decisions.append(resulting)

            with pytest.raises(RuntimeError, match="stage failed"):
                await appender.submit(replace(_command(hook=hook), decision_guard=decide))
            assert decisions == []
            assert store.durable_records() == ()


async def test_checkpoint_enabled_bundle_rejects_callbackless_attach(tmp_path: Path) -> None:
    with pytest.raises(Exception, match="requires mutation admission"):
        SQLiteMaterializer(
            tmp_path / "authority.sqlite", record_contracts={}, checkpoint_enabled=True
        )


async def test_pending_admission_rejects_all_mutation_lanes(tmp_path: Path) -> None:
    def reject(*_: object) -> None:
        raise AuthorityMutationAdmissionError("pending")

    database = tmp_path / "authority.sqlite"
    gate = AuthorityGate.for_database(database)
    with SQLiteMaterializer._open_for_broker_bootstrap(
        database, record_contracts={"owner": "schema"}, authority_gate=gate
    ) as store:
        async with EventAppender(store, capacity=1) as appender:
            appender.complete_broker_admission(
                reject, database_identity=checked_file_identity(database)
            )
            with pytest.raises(AuthorityMutationAdmissionError, match="pending"):
                await appender.advance_fence(FenceAdvanceCommand("tenant", "fence", 0))


async def test_marker_is_persistent_excluded_from_hash_and_rejects_raw_opens(
    tmp_path: Path,
) -> None:
    database = tmp_path / "authority.sqlite"
    gate = AuthorityGate.for_database(database)
    with SQLiteMaterializer._open_for_broker_bootstrap(
        database, record_contracts={"owner": "schema"}, authority_gate=gate
    ) as store:
        async with EventAppender(store, capacity=1) as appender:
            appender.complete_broker_admission(
                lambda *_: None, database_identity=checked_file_identity(database)
            )
            with sqlite3.connect(database) as reader:
                reader.execute("BEGIN")
                before = capture_authority_snapshot_bytes(reader, "tenant")
            appender.activate_checkpoint_bundle()
            with sqlite3.connect(database) as reader:
                reader.execute("BEGIN")
                assert capture_authority_snapshot_bytes(reader, "tenant") == before
                assert reader.execute("PRAGMA user_version").fetchone()[0] != 0
    with pytest.raises(CheckpointBundleAdmissionError, match="broker bootstrap"):
        SQLiteMaterializer(database, record_contracts={"owner": "schema"})
    with pytest.raises(CheckpointBundleAdmissionError, match="broker bootstrap"):
        SQLiteMaterializer(
            database,
            record_contracts={"owner": "schema"},
            authority_gate=gate,
            authority_mutation_admission=lambda *_: None,
            checkpoint_enabled=True,
        )


async def test_stale_preactivation_writer_fails_before_authority_rows_change(
    tmp_path: Path,
) -> None:
    database = tmp_path / "authority.sqlite"
    stale = SQLiteMaterializer(database, record_contracts={"owner": "schema"})
    stale_appender = EventAppender(stale, capacity=1)
    gate = AuthorityGate.for_database(database)
    try:
        with SQLiteMaterializer._open_for_broker_bootstrap(
            database, record_contracts={"owner": "schema"}, authority_gate=gate
        ) as broker:
            async with EventAppender(broker, capacity=1) as appender:
                appender.complete_broker_admission(
                    lambda *_: None, database_identity=checked_file_identity(database)
                )
                appender.activate_checkpoint_bundle()
        with pytest.raises(CheckpointBundleAdmissionError, match="not broker-bound"):
            await stale_appender.advance_fence(FenceAdvanceCommand("tenant", "fence", 0))
        assert stale.durable_records() == ()
    finally:
        await stale_appender.close()
