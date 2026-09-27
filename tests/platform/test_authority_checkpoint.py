"""Full authority checkpoint preimages are exact, durable, and fail closed."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

import pytest

from chiplog.architecture.r7_storage_surface import (
    AUTHORITY_STORAGE_MEMBERS,
    AUTHORITY_STORAGE_SURFACE_DIGEST,
)
from chiplog.platform._sqlite import SQLiteMaterializer
from chiplog.platform.authority_checkpoint import (
    MAX_AUTHORITY_PREIMAGE_BYTES,
    AuthorityCheckpointRefV1,
    AuthorityCheckpointStore,
    AuthorityCheckpointVerificationError,
    _sqlite_row_sort_key,
)
from chiplog.platform.authority_reads import (
    AuthoritySnapshotIntegrityError,
    _authority_commitment,
    capture_authority_snapshot_bytes,
)


def _snapshot_with_every_member(path: Path) -> bytes:
    with (
        SQLiteMaterializer(path, record_contracts={"fixture": "fixture.v1"}),
        closing(sqlite3.connect(path)) as connection,
    ):
        connection.execute("INSERT INTO tenant_heads VALUES (?, ?)", ("tenant-a", 7))
        connection.execute("INSERT INTO tenant_heads VALUES (?, ?)", ("tenant-b", 2))
        connection.execute("INSERT INTO deletion_fences VALUES (?, ?, ?)", ("tenant-a", "fence", 3))
        connection.execute(
            "INSERT INTO evidence_inbox VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "tenant-b",
                "source",
                "evidence",
                "fp",
                b"\x00\xff",
                "POLL",
                "STATE",
                None,
                None,
                None,
            ),
        )
        connection.execute(
            "INSERT INTO publications VALUES (?, ?, ?, ?, ?, ?)",
            ("tenant-a", "operation", "key", "request", 7, "record"),
        )
        connection.execute(
            "INSERT INTO records VALUES (?, ?, ?, ?, ?, ?)",
            ("tenant-a", "record", "fixture", "fixture.v1", b"\x00\xff", 7),
        )
        connection.execute(
            "INSERT INTO derivatives VALUES (?, ?, ?, ?, ?, ?)",
            ("tenant-a", "sink", "derived", "record", 7, "provenance"),
        )
        connection.commit()
        connection.execute("BEGIN")
        return capture_authority_snapshot_bytes(connection, "tenant-a")


def _ref(raw: bytes) -> AuthorityCheckpointRefV1:
    return AuthorityCheckpointRefV1(
        format="authority-preimage-v1",
        commitment_algorithm="authority-json-v1",
        authority_surface_digest=AUTHORITY_STORAGE_SURFACE_DIGEST,
        blob_sha256=hashlib.sha256(raw).hexdigest(),
        byte_length=len(raw),
    )


def test_checkpoint_bytes_preserve_existing_full_commitment_and_exclude_derivatives(
    tmp_path: Path,
) -> None:
    raw = _snapshot_with_every_member(tmp_path / "authority.sqlite")
    expected_tables = tuple(item for item in AUTHORITY_STORAGE_MEMBERS if item.authority_bearing)
    decoded = json.loads(raw)

    assert hashlib.sha256(raw).hexdigest() == _commitment_for(tmp_path / "authority.sqlite")
    assert [(item["table"], tuple(item["columns"])) for item in decoded] == [
        (item.table, item.columns) for item in expected_tables
    ]
    heads = {row[0] for item in decoded for row in item["rows"] if item["table"] == "tenant_heads"}
    assert heads == {
        "tenant-a",
        "tenant-b",
    }
    assert "derivatives" not in {item["table"] for item in decoded}

    with closing(sqlite3.connect(tmp_path / "authority.sqlite")) as connection:
        connection.execute("BEGIN")
        connection.execute("ALTER TABLE derivatives ADD COLUMN unexpected TEXT")
        with pytest.raises(AuthoritySnapshotIntegrityError):
            capture_authority_snapshot_bytes(connection, "tenant-a")


def _commitment_for(path: Path) -> str:
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("BEGIN")
        return _authority_commitment(connection)


def test_stage_is_immutable_content_addressed_and_durable(tmp_path: Path) -> None:
    raw = _snapshot_with_every_member(tmp_path / "authority.sqlite")
    store = AuthorityCheckpointStore(tmp_path / "checkpoints")

    ref = store.stage_verified(raw)
    assert ref == _ref(raw)
    assert store.stage_verified(raw) == ref
    assert (
        store.resolve_verified(
            ref,
            expected_resulting=hashlib.sha256(raw).hexdigest(),
            expected_surface_digest=AUTHORITY_STORAGE_SURFACE_DIGEST,
        ).preimage
        == raw
    )


def test_stage_file_fsync_failure_leaves_no_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = _snapshot_with_every_member(tmp_path / "authority.sqlite")
    store = AuthorityCheckpointStore(tmp_path / "checkpoints")
    original = os.fsync

    def fail_file(descriptor: int) -> None:
        if stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError("injected blob fsync failure")
        original(descriptor)

    monkeypatch.setattr(os, "fsync", fail_file)
    with pytest.raises(OSError, match="injected blob fsync failure"):
        store.stage_verified(raw)
    assert not store._blob_path(hashlib.sha256(raw).hexdigest()).exists()


def test_stage_refuses_corrupt_preexisting_object_without_replacement(tmp_path: Path) -> None:
    raw = _snapshot_with_every_member(tmp_path / "authority.sqlite")
    store = AuthorityCheckpointStore(tmp_path / "checkpoints")
    path = store._blob_path(hashlib.sha256(raw).hexdigest())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"corrupt")

    with pytest.raises(AuthorityCheckpointVerificationError, match="collision or corruption"):
        store.stage_verified(raw)
    assert path.read_bytes() == b"corrupt"


def test_concurrent_stage_returns_one_immutable_reference(tmp_path: Path) -> None:
    raw = _snapshot_with_every_member(tmp_path / "authority.sqlite")
    store = AuthorityCheckpointStore(tmp_path / "checkpoints")
    with ThreadPoolExecutor(max_workers=4) as pool:
        refs = tuple(pool.map(store.stage_verified, (raw,) * 4))
    assert refs == (_ref(raw),) * 4


def test_sqlite_numeric_order_preserves_exact_64_bit_integers(tmp_path: Path) -> None:
    values = (-(2**63), -(2**53 + 1), -(2**53), 2**53, 2**53 + 1, 2**63 - 1)
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.execute("CREATE TABLE values_to_sort (value INTEGER)")
        connection.executemany(
            "INSERT INTO values_to_sort VALUES (?)", ((value,) for value in reversed(values))
        )
        sqlite_order = tuple(
            row[0] for row in connection.execute("SELECT value FROM values_to_sort ORDER BY 1")
        )

    assert (
        tuple(sorted(reversed(values), key=lambda value: _sqlite_row_sort_key([value])))
        == sqlite_order
    )
    assert _sqlite_row_sort_key([2**53]) < _sqlite_row_sort_key([2**53 + 1])


def test_store_rejects_existing_symlink_ancestor(tmp_path: Path) -> None:
    physical = tmp_path / "physical" / "checkpoints"
    physical.mkdir(parents=True)
    alias = tmp_path / "alias"
    alias.symlink_to(physical.parent, target_is_directory=True)

    with pytest.raises(AuthorityCheckpointVerificationError, match="unsafe"):
        AuthorityCheckpointStore(alias / "checkpoints")
    assert not (physical / "objects").exists()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda raw: raw.replace(b'"table":"records"', b'"table":"records","table":"records"'),
        lambda raw: raw.replace(b'"table":"records"', b'"table":"missing"'),
        lambda raw: raw.replace(
            b'"columns":["tenant_id","record_id"', b'"columns":["record_id","tenant_id"'
        ),
        lambda raw: raw.replace(b'"rows":[[', b'"rows":[["changed",', 1),
    ],
    ids=["duplicate", "foreign", "reordered", "changed_scalar"],
)
def test_stage_rejects_noncanonical_or_invalid_preimages(
    tmp_path: Path, mutate: Callable[[bytes], bytes]
) -> None:
    raw = _snapshot_with_every_member(tmp_path / "authority.sqlite")
    with pytest.raises(AuthorityCheckpointVerificationError):
        AuthorityCheckpointStore(tmp_path / "checkpoints").stage_verified(mutate(raw))


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "corrupt",
        "foreign",
        "noncanonical",
        "duplicate",
        "duplicate_row",
        "reordered",
        "reordered_table",
        "reordered_row",
        "incomplete",
        "oversize",
        "symlink",
    ],
)
def test_resolve_verified_rejects_untrusted_or_invalid_blob(tmp_path: Path, case: str) -> None:
    raw = _snapshot_with_every_member(tmp_path / "authority.sqlite")
    store = AuthorityCheckpointStore(tmp_path / "checkpoints")
    ref = store.stage_verified(raw)
    expected = hashlib.sha256(raw).hexdigest()
    path = store._blob_path(ref.blob_sha256)
    if case == "missing":
        path.unlink()
    elif case == "corrupt":
        path.write_bytes(b"corrupt")
    elif case == "foreign":
        ref = _ref(raw).model_copy(update={"authority_surface_digest": "0" * 64})
    elif case == "noncanonical":
        _install_untrusted(store, raw + b" ")
        ref = _ref(raw + b" ")
        expected = ref.blob_sha256
    elif case == "duplicate":
        altered = raw.replace(b'"table":"records"', b'"table":"records","table":"records"')
        _install_untrusted(store, altered)
        ref, expected = _ref(altered), hashlib.sha256(altered).hexdigest()
    elif case == "duplicate_row":
        altered = _rewrite(raw, lambda decoded: _duplicate_head(decoded))
        _install_untrusted(store, altered)
        ref, expected = _ref(altered), hashlib.sha256(altered).hexdigest()
    elif case == "reordered":
        altered = raw.replace(
            b'"columns":["tenant_id","record_id"', b'"columns":["record_id","tenant_id"'
        )
        _install_untrusted(store, altered)
        ref, expected = _ref(altered), hashlib.sha256(altered).hexdigest()
    elif case == "reordered_table":
        altered = _rewrite(raw, lambda decoded: list(reversed(decoded)))
        _install_untrusted(store, altered)
        ref, expected = _ref(altered), hashlib.sha256(altered).hexdigest()
    elif case == "reordered_row":
        altered = _rewrite(raw, lambda decoded: _reverse_heads(decoded))
        _install_untrusted(store, altered)
        ref, expected = _ref(altered), hashlib.sha256(altered).hexdigest()
    elif case == "incomplete":
        altered = raw.replace(
            b',{"columns":["singleton","store_version"],"rows":[[1,1]],"table":"store_metadata"}',
            b"",
        )
        _install_untrusted(store, altered)
        ref, expected = _ref(altered), hashlib.sha256(altered).hexdigest()
    elif case == "oversize":
        ref = ref.model_copy(update={"byte_length": MAX_AUTHORITY_PREIMAGE_BYTES + 1})
    else:
        victim = tmp_path / "victim"
        victim.write_bytes(raw)
        path.unlink()
        path.symlink_to(victim)

    with pytest.raises(AuthorityCheckpointVerificationError):
        store.resolve_verified(
            ref,
            expected_resulting=expected,
            expected_surface_digest=AUTHORITY_STORAGE_SURFACE_DIGEST,
        )


def _install_untrusted(store: AuthorityCheckpointStore, raw: bytes) -> None:
    path = store._blob_path(hashlib.sha256(raw).hexdigest())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    os.chmod(path, 0o600)


def _rewrite(raw: bytes, transform: Callable[[list[object]], list[object]]) -> bytes:
    decoded = json.loads(raw)
    assert isinstance(decoded, list)
    return json.dumps(transform(decoded), sort_keys=True, separators=(",", ":")).encode()


def _head_table(decoded: list[object]) -> dict[str, object]:
    table = next(
        item for item in decoded if isinstance(item, dict) and item["table"] == "tenant_heads"
    )
    assert isinstance(table, dict)
    return table


def _duplicate_head(decoded: list[object]) -> list[object]:
    heads = _head_table(decoded)
    rows = heads["rows"]
    assert isinstance(rows, list)
    rows.append(rows[0])
    return decoded


def _reverse_heads(decoded: list[object]) -> list[object]:
    heads = _head_table(decoded)
    rows = heads["rows"]
    assert isinstance(rows, list)
    rows.reverse()
    return decoded
