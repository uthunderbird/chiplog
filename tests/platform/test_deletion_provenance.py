from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from chiplog.platform._sqlite import (
    DerivativeRegistration,
    DerivativeRegistrationCommand,
    EventAppender,
    FenceAdvanceCommand,
    PhysicalPublicationCommand,
    PhysicalRecord,
    SQLiteMaterializer,
    StoreAdmissionError,
)


def store(path: Path) -> SQLiteMaterializer:
    return SQLiteMaterializer(
        path,
        record_contracts={"owner": "schema.v1"},
        derivative_contracts=("projection", "cache"),
        managed_derivative_sinks=("cache", "projection"),
    )


def command() -> PhysicalPublicationCommand:
    payload = b"payload"
    return PhysicalPublicationCommand(
        "tenant-1",
        "fixture",
        "key-1",
        "fingerprint-1",
        0,
        "fence-1",
        3,
        2,
        (
            PhysicalRecord(
                "record-1", "owner", "schema.v1", payload, hashlib.sha256(payload).hexdigest()
            ),
        ),
    )


def provenance_fingerprint(registration: DerivativeRegistration) -> str:
    canonical = json.dumps(
        {
            "tenant_id": "tenant-1",
            "sink": registration.sink,
            "derivative_id": registration.derivative_id,
            "source_record_ids": registration.source_record_ids,
            "source_epoch": registration.source_epoch,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


@pytest.mark.parametrize(
    ("mode", "match"),
    [
        ("absent", "absent"),
        ("lag", "lags"),
        ("generation", "generation mismatch"),
        ("frontier", "frontier mismatch"),
    ],
)
async def test_fence_absence_lag_or_mismatch_blocks_publication(
    tmp_path: Path, mode: str, match: str
) -> None:
    subject = store(tmp_path / f"{mode}.sqlite3")
    appender = EventAppender(subject, capacity=2)
    if mode != "absent":
        await appender.advance_fence(
            FenceAdvanceCommand("tenant-1", "fence-1", 1 if mode == "lag" else 3)
        )
    candidate = command()
    if mode == "generation":
        candidate = replace(candidate, fence_generation="wrong")
    if mode == "frontier":
        candidate = replace(candidate, expected_fence_frontier=2)
    with pytest.raises(ValueError, match=match):
        await appender.submit(candidate)
    assert subject.durable_records() == ()
    await appender.close()


@pytest.mark.parametrize("mode", ["absent", "lag", "mismatch"])
async def test_fence_failure_blocks_ordinary_read(tmp_path: Path, mode: str) -> None:
    subject = store(tmp_path / f"read-{mode}.sqlite3")
    appender = EventAppender(subject, capacity=1)
    if mode != "absent":
        await appender.advance_fence(
            FenceAdvanceCommand("tenant-1", "fence-1", 1 if mode == "lag" else 3)
        )
    with pytest.raises(ValueError, match="deletion fence"):
        subject.guarded_records("tenant-1", "wrong" if mode == "mismatch" else "fence-1", 2)
    await appender.close()


async def test_stale_fence_blocks_replay_after_fence_advance(tmp_path: Path) -> None:
    subject = store(tmp_path / "replay.sqlite3")
    appender = EventAppender(subject, capacity=2)
    await appender.advance_fence(FenceAdvanceCommand("tenant-1", "fence-1", 3))
    assert (await appender.submit(command())).disposition == "COMMITTED"
    await appender.advance_fence(FenceAdvanceCommand("tenant-1", "fence-2", 4))
    with pytest.raises(ValueError, match="generation mismatch"):
        await appender.submit(command())
    assert len(subject.durable_records()) == 1
    await appender.close()


async def test_exact_fence_install_is_idempotent_for_concurrent_bootstrap(tmp_path: Path) -> None:
    subject = store(tmp_path / "fence-replay.sqlite3")
    appender = EventAppender(subject, capacity=2)
    first, second = await asyncio.gather(
        appender.advance_fence(FenceAdvanceCommand("tenant-1", "r6", 0, True)),
        appender.advance_fence(FenceAdvanceCommand("tenant-1", "r6", 0, True)),
    )
    assert {first.disposition, second.disposition} == {"COMMITTED", "REPLAY"}
    subject.require_fence("tenant-1", "r6", 0, exact_frontier=0)
    await appender.close()


async def test_guarded_read_has_only_pre_or_post_fence_serial_orders(tmp_path: Path) -> None:
    subject = store(tmp_path / "read-orders.sqlite3")
    appender = EventAppender(subject, capacity=2)
    await appender.advance_fence(FenceAdvanceCommand("tenant-1", "fence-1", 3))
    assert (await appender.submit(command())).disposition == "COMMITTED"
    assert len(subject.guarded_records("tenant-1", "fence-1", 3)) == 1
    await appender.advance_fence(FenceAdvanceCommand("tenant-1", "fence-2", 4))
    with pytest.raises(ValueError, match="generation mismatch"):
        subject.guarded_records("tenant-1", "fence-1", 3)
    assert len(subject.guarded_records("tenant-1", "fence-2", 4)) == 1
    await appender.close()


@pytest.mark.parametrize(
    ("registered", "managed"),
    [
        ((), ("projection",)),
        (("projection",), ()),
        (("projection", "projection"), ("projection",)),
    ],
)
def test_missing_or_orphan_derivative_registry_rejects_admission(
    tmp_path: Path, registered: tuple[str, ...], managed: tuple[str, ...]
) -> None:
    with pytest.raises(StoreAdmissionError, match="exact-set mismatch"):
        SQLiteMaterializer(
            tmp_path / "store.sqlite3",
            record_contracts={},
            derivative_contracts=registered,
            managed_derivative_sinks=managed,
        )


@pytest.mark.parametrize("managed", [(), ("owner", "orphan"), ("owner", "owner")])
def test_missing_or_orphan_record_owner_registry_rejects_admission(
    tmp_path: Path, managed: tuple[str, ...]
) -> None:
    with pytest.raises(StoreAdmissionError, match="record owner registry exact-set mismatch"):
        SQLiteMaterializer(
            tmp_path / "store.sqlite3",
            record_contracts={"owner": "schema.v1"},
            managed_record_owners=managed,
        )


async def test_derivative_requires_exact_existing_unique_provenance(tmp_path: Path) -> None:
    subject = store(tmp_path / "store.sqlite3")
    appender = EventAppender(subject, capacity=3)
    await appender.advance_fence(FenceAdvanceCommand("tenant-1", "fence-1", 3))
    assert (await appender.submit(command())).disposition == "COMMITTED"
    registration = DerivativeRegistration("projection", "projection-1", ("record-1",), 3, "")
    registration = replace(
        registration, provenance_fingerprint=provenance_fingerprint(registration)
    )
    result = await appender.register_derivative(
        DerivativeRegistrationCommand("tenant-1", registration, "fence-1")
    )
    assert result.disposition == "COMMITTED"
    for invalid, match in [
        (
            replace(registration, derivative_id="missing", source_record_ids=("missing",)),
            "missing or foreign",
        ),
        (
            replace(
                registration, derivative_id="duplicate", source_record_ids=("record-1", "record-1")
            ),
            "unique",
        ),
        (
            replace(registration, derivative_id="bad-fingerprint", provenance_fingerprint="0" * 64),
            "fingerprint mismatch",
        ),
        (replace(registration, derivative_id="stale", source_epoch=2), "frontier mismatch"),
    ]:
        with pytest.raises(ValueError, match=match):
            await appender.register_derivative(
                DerivativeRegistrationCommand("tenant-1", invalid, "fence-1")
            )
    await appender.close()


async def test_derivative_exact_replay_survives_restart_but_not_changed_binding_or_fence(
    tmp_path: Path,
) -> None:
    path = tmp_path / "derivative-replay.sqlite3"
    publication = command()
    publication = replace(
        publication,
        records=(*publication.records, replace(publication.records[0], record_id="record-2")),
    )
    registration = DerivativeRegistration(
        "projection", "immutable-screen", ("record-1", "record-2"), 3, ""
    )
    registration = replace(
        registration, provenance_fingerprint=provenance_fingerprint(registration)
    )
    request = DerivativeRegistrationCommand("tenant-1", registration, "fence-1")
    async with EventAppender(store(path), capacity=2) as appender:
        await appender.advance_fence(FenceAdvanceCommand("tenant-1", "fence-1", 3))
        assert (await appender.submit(publication)).disposition == "COMMITTED"
        assert (await appender.register_derivative(request)).disposition == "COMMITTED"
        assert (await appender.register_derivative(request)).disposition == "REPLAY"
    async with EventAppender(store(path), capacity=2) as appender:
        assert (await appender.register_derivative(request)).disposition == "REPLAY"
        changed = replace(registration, source_record_ids=("record-2", "record-1"))
        changed = replace(changed, provenance_fingerprint=provenance_fingerprint(changed))
        with pytest.raises(ValueError, match="changed provenance"):
            await appender.register_derivative(replace(request, registration=changed))
        assert (await appender.register_derivative(request)).disposition == "REPLAY"
        await appender.advance_fence(FenceAdvanceCommand("tenant-1", "fence-2", 4))
        with pytest.raises(ValueError, match="generation mismatch"):
            await appender.register_derivative(request)
