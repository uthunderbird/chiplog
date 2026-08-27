from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

import chiplog.platform as platform
import chiplog.platform._sqlite as sqlite_platform
from chiplog.platform._sqlite import (
    EventAppender,
    FenceAdvanceCommand,
    LostCommitAcknowledgement,
    PhysicalPublicationCommand,
    PhysicalRecord,
    PublicationResult,
    SQLiteMaterializer,
    StoreAdmissionError,
)


def record(
    record_id: str, payload: bytes, *, owner: str = "fixture-owner", schema: str = "schema.v1"
) -> PhysicalRecord:
    return PhysicalRecord(record_id, owner, schema, payload, sha256(payload).hexdigest())


def command(
    *,
    key: str = "key-1",
    fingerprint: str = "fingerprint-1",
    expected_head: int = 0,
    fault: str = "none",
    records: tuple[PhysicalRecord, ...] | None = None,
) -> PhysicalPublicationCommand:
    return PhysicalPublicationCommand(
        "tenant-1",
        "fixture",
        key,
        fingerprint,
        expected_head,
        "fence-1",
        0,
        0,
        records or (record("record-1", b"one"), record("record-2", b"two")),
        fault,  # type: ignore[arg-type]
    )


async def opened(path: Path, *, capacity: int = 4) -> tuple[SQLiteMaterializer, EventAppender]:
    store = SQLiteMaterializer(path, record_contracts={"fixture-owner": "schema.v1"})
    appender = EventAppender(store, capacity=capacity)
    await appender.advance_fence(FenceAdvanceCommand("tenant-1", "fence-1", 0))
    return store, appender


def test_platform_public_surface_does_not_export_physical_writer() -> None:
    assert platform.__all__ == ["DeterministicClock", "DeterministicIdSource"]
    assert not hasattr(platform, "EventAppender")


async def test_atomic_publication_replay_and_changed_fingerprint_conflict(tmp_path: Path) -> None:
    store, appender = await opened(tmp_path / "store.sqlite3")
    assert (await appender.submit(command())).disposition == "COMMITTED"
    assert len(store.durable_records()) == 2
    assert (await appender.submit(command())).disposition == "REPLAY"
    assert (await appender.submit(command(fingerprint="changed"))).disposition == "CONFLICT"
    assert len(store.durable_records()) == 2
    await appender.close()


async def test_fault_before_commit_leaves_no_partial_publication(tmp_path: Path) -> None:
    store, appender = await opened(tmp_path / "store.sqlite3")
    with pytest.raises(RuntimeError, match="before commit"):
        await appender.submit(command(fault="before_commit"))
    assert store.durable_records() == ()
    await appender.close()


async def test_lost_commit_acknowledgement_replays_durable_result(tmp_path: Path) -> None:
    store, appender = await opened(tmp_path / "store.sqlite3")
    with pytest.raises(LostCommitAcknowledgement):
        await appender.submit(command(fault="after_commit"))
    assert len(store.durable_records()) == 2
    assert (await appender.submit(command())).disposition == "REPLAY"
    await appender.close()


@pytest.mark.parametrize(
    "invalid", [record("one", b"x", owner="unknown"), record("one", b"x", schema="wrong")]
)
async def test_owner_or_schema_mismatch_writes_nothing(
    tmp_path: Path, invalid: PhysicalRecord
) -> None:
    store, appender = await opened(tmp_path / "store.sqlite3")
    with pytest.raises(ValueError, match="owner/schema"):
        await appender.submit(command(records=(invalid,)))
    assert store.durable_records() == ()
    await appender.close()


async def test_canonical_fingerprint_mismatch_writes_nothing(tmp_path: Path) -> None:
    store, appender = await opened(tmp_path / "store.sqlite3")
    invalid = replace(record("one", b"x"), fingerprint="0" * 64)
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        await appender.submit(command(records=(invalid,)))
    assert store.durable_records() == ()
    await appender.close()


def test_unknown_store_version_writes_nothing(tmp_path: Path) -> None:
    path = tmp_path / "store.sqlite3"
    store = SQLiteMaterializer(path, record_contracts={})
    store.close()
    connection = sqlite3.connect(path)
    connection.execute("UPDATE store_metadata SET store_version = 999")
    connection.commit()
    connection.close()
    before = path.read_bytes()
    with pytest.raises(StoreAdmissionError, match="version mismatch"):
        SQLiteMaterializer(path, record_contracts={})
    assert path.read_bytes() == before


def test_unknown_physical_table_rejects_startup(tmp_path: Path) -> None:
    path = tmp_path / "store.sqlite3"
    store = SQLiteMaterializer(path, record_contracts={})
    store.close()
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE unregistered_surface(value TEXT)")
    connection.commit()
    connection.close()
    with pytest.raises(StoreAdmissionError, match="table set mismatch"):
        SQLiteMaterializer(path, record_contracts={})


def test_weakened_primary_key_and_not_null_schema_rejects_startup(tmp_path: Path) -> None:
    path = tmp_path / "store.sqlite3"
    store = SQLiteMaterializer(path, record_contracts={})
    store.close()
    connection = sqlite3.connect(path)
    connection.execute("ALTER TABLE records RENAME TO records_original")
    connection.execute(
        """CREATE TABLE records(
               tenant_id TEXT, record_id TEXT, owner TEXT, schema_id TEXT,
               canonical_bytes BLOB, commit_sequence INTEGER
           )"""
    )
    connection.execute("DROP TABLE records_original")
    connection.commit()
    connection.close()
    with pytest.raises(StoreAdmissionError, match="canonical schema mismatch"):
        SQLiteMaterializer(path, record_contracts={})


def test_forged_writer_token_cannot_mutate(tmp_path: Path) -> None:
    store = SQLiteMaterializer(tmp_path / "store.sqlite3", record_contracts={})
    forged = sqlite_platform._WriterToken()
    with pytest.raises(RuntimeError, match="requires EventAppender ownership"):
        store._install_fence(forged, "tenant-1", "forged", 0)
    store.close()


async def test_second_appender_for_one_materializer_is_rejected(tmp_path: Path) -> None:
    store = SQLiteMaterializer(tmp_path / "store.sqlite3", record_contracts={})
    first = EventAppender(store, capacity=1)
    with pytest.raises(RuntimeError, match="already has an EventAppender owner"):
        EventAppender(store, capacity=1)
    await first.close()


@pytest.mark.parametrize(
    ("owner", "schema"), [("unknown", "schema.v1"), ("fixture-owner", "unknown")]
)
async def test_restart_rejects_persisted_record_without_registered_decoder(
    tmp_path: Path, owner: str, schema: str
) -> None:
    path = tmp_path / "store.sqlite3"
    _, appender = await opened(path)
    assert (await appender.submit(command())).disposition == "COMMITTED"
    await appender.close()
    connection = sqlite3.connect(path)
    connection.execute("UPDATE records SET owner = ?, schema_id = ?", (owner, schema))
    connection.commit()
    connection.close()
    with pytest.raises(StoreAdmissionError, match="no decoder"):
        SQLiteMaterializer(path, record_contracts={"fixture-owner": "schema.v1"})


@pytest.mark.parametrize("winner_first", [True, False])
async def test_single_writer_exercises_both_cas_serial_orders(
    tmp_path: Path, winner_first: bool
) -> None:
    store, appender = await opened(tmp_path / "store.sqlite3")
    left = command(key="left", records=(record("left", b"left"),))
    right = command(key="right", records=(record("right", b"right"),))
    ordered = (left, right) if winner_first else (right, left)
    results = await asyncio.gather(*(appender.submit(item) for item in ordered))
    assert [result.disposition for result in results] == ["COMMITTED", "STALE"]
    assert [row[1] for row in store.durable_records()] == [ordered[0].records[0].record_id]
    await appender.close()


async def test_cancelled_close_does_not_cancel_drain(tmp_path: Path) -> None:
    _, appender = await opened(tmp_path / "store.sqlite3")
    assert (await appender.submit(command())).disposition == "COMMITTED"
    closing = asyncio.create_task(appender.close())
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing
    await appender.close()
    with pytest.raises(RuntimeError, match="admission is closed"):
        await appender.submit(command(key="later", expected_head=1))


async def test_ordinary_lane_applies_bounded_backpressure(tmp_path: Path) -> None:
    _, appender = await opened(tmp_path / "store.sqlite3", capacity=1)
    results = await asyncio.gather(
        asyncio.create_task(appender.submit(command())),
        asyncio.create_task(appender.submit(command(key="second"))),
        return_exceptions=True,
    )
    assert sum(isinstance(result, asyncio.QueueFull) for result in results) == 1
    assert (
        sum(
            isinstance(result, PublicationResult) and result.disposition == "COMMITTED"
            for result in results
        )
        == 1
    )
    await appender.close()
