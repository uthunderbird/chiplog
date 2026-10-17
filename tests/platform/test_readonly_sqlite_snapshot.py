"""Existing authority inspection must not invoke SQLite on source files."""

import os
import shutil
import sqlite3
import stat
from contextlib import closing
from pathlib import Path

import pytest

from chiplog.platform._sqlite import SCHEMA_SQL
from chiplog.platform.authority_gate import AuthorityGate
from chiplog.platform.authority_reads import capture_authority_snapshot_bytes


def _files(directory: Path):
    return {
        path.name: (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
        for path in directory.iterdir()
    }


@pytest.mark.parametrize("role", ["MAIN", "TRUST"])
def test_existing_only_copy_reads_committed_wal_without_source_sqlite_or_sidecar_writes(
    tmp_path, monkeypatch, role
):
    producer = tmp_path / "producer.sqlite"
    source_directory = tmp_path / "source"
    source_directory.mkdir()
    main = source_directory / "authority.sqlite"
    database = main if role == "MAIN" else main.with_suffix(main.suffix + ".trust.sqlite3")
    with closing(sqlite3.connect(producer, isolation_level=None)) as writer:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        writer.executescript(SCHEMA_SQL)
        writer.execute("INSERT INTO store_metadata VALUES (1, 1)")
        writer.execute("BEGIN")
        expected = capture_authority_snapshot_bytes(writer, "tenant")
        writer.execute("ROLLBACK")
        shutil.copyfile(producer, database)
        shutil.copyfile(Path(str(producer) + "-wal"), Path(str(database) + "-wal"))

    # Prove this fixture rejects a main-only implementation before testing the API.
    control = tmp_path / "main-only.sqlite"
    shutil.copyfile(database, control)
    with closing(sqlite3.connect(control)) as connection:
        assert connection.execute("SELECT name FROM sqlite_master").fetchall() == []

    gate = AuthorityGate.for_database(main)
    with gate.hold():
        pass  # Fixture provisioning ends before the observed existing-only operation.
    before = _files(source_directory)
    assert database.name + "-shm" not in before
    original_connect = sqlite3.connect

    def private_connect(target, *args, **kwargs):
        assert str(source_directory) not in str(target), "SQLite opened source namespace"
        return original_connect(target, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", private_connect)
    from chiplog.platform.readonly_sqlite_snapshot import readonly_authority_sql_snapshot

    with (
        gate.hold_existing(),
        readonly_authority_sql_snapshot(database, authority_gate=gate, role=role) as connection,
    ):
        assert connection.execute("SELECT * FROM store_metadata").fetchall() == [(1, 1)]
        assert connection.in_transaction
        assert capture_authority_snapshot_bytes(connection, "tenant") == expected
        private_path = Path(connection.execute("PRAGMA database_list").fetchone()[2])
        assert private_path.exists()
    assert not private_path.parent.exists()
    assert _files(source_directory) == before


def _clean_source(tmp_path):
    database = tmp_path / "source" / "authority.sqlite"
    database.parent.mkdir()
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("CREATE TABLE sentinel(value)")
    gate = AuthorityGate.for_database(database)
    with gate.hold():
        pass
    return database, gate


def test_missing_or_mismatched_gate_refuses_without_provisioning(tmp_path: Path) -> None:
    from chiplog.platform.readonly_sqlite_snapshot import (
        AuthoritySQLSnapshotIntegrityError,
        readonly_authority_sql_snapshot,
    )

    database, gate = _clean_source(tmp_path)
    before = _files(database.parent)
    with (
        pytest.raises(AuthoritySQLSnapshotIntegrityError),
        readonly_authority_sql_snapshot(database, authority_gate=gate),
    ):
        pytest.fail("unheld gate yielded")
    with (
        gate.hold_existing(),
        pytest.raises(AuthoritySQLSnapshotIntegrityError),
        readonly_authority_sql_snapshot(database.parent / "other", authority_gate=gate),
    ):
        pytest.fail("mismatched gate yielded")
    assert _files(database.parent) == before


@pytest.mark.parametrize("companion", ["-journal", "-wal"])
def test_companion_appearance_during_consumer_refuses_and_cleans(
    tmp_path: Path, companion: str
) -> None:
    from chiplog.platform.readonly_sqlite_snapshot import (
        AuthoritySQLSnapshotIntegrityError,
        readonly_authority_sql_snapshot,
    )

    database, gate = _clean_source(tmp_path)
    with (
        gate.hold_existing(),
        pytest.raises(AuthoritySQLSnapshotIntegrityError),
        readonly_authority_sql_snapshot(database, authority_gate=gate) as connection,
    ):
        private = Path(connection.execute("PRAGMA database_list").fetchone()[2])
        Path(str(database) + companion).write_bytes(b"external mutation")
    assert not private.parent.exists()
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")


def test_replaced_source_during_copy_refuses_before_yield(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import chiplog.platform.readonly_sqlite_snapshot as snapshot

    database, gate = _clean_source(tmp_path)
    original = snapshot._copy_source

    def replace_after_copy(stack, source, destination):
        result = original(stack, source, destination)
        replacement = source.with_suffix(".replacement")
        replacement.write_bytes(source.read_bytes())
        replacement.replace(source)
        return result

    monkeypatch.setattr(snapshot, "_copy_source", replace_after_copy)
    with (
        gate.hold_existing(),
        pytest.raises(snapshot.AuthoritySQLSnapshotIntegrityError),
        snapshot.readonly_authority_sql_snapshot(database, authority_gate=gate),
    ):
        pytest.fail("replaced source yielded")


def test_consumer_exception_closes_private_connection_and_directory(tmp_path: Path) -> None:
    from chiplog.platform.readonly_sqlite_snapshot import readonly_authority_sql_snapshot

    database, gate = _clean_source(tmp_path)
    before = _files(database.parent)
    with (
        gate.hold_existing(),
        pytest.raises(ValueError, match="consumer"),
        readonly_authority_sql_snapshot(database, authority_gate=gate) as connection,
    ):
        private = Path(connection.execute("PRAGMA database_list").fetchone()[2])
        raise ValueError("consumer")
    assert not private.parent.exists()
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")
    assert _files(database.parent) == before


def test_existing_nonhot_journal_is_unsupported(tmp_path: Path) -> None:
    from chiplog.platform.readonly_sqlite_snapshot import (
        AuthoritySQLSnapshotIntegrityError,
        readonly_authority_sql_snapshot,
    )

    database, gate = _clean_source(tmp_path)
    Path(str(database) + "-journal").write_bytes(b"")
    before = _files(database.parent)
    with (
        gate.hold_existing(),
        pytest.raises(AuthoritySQLSnapshotIntegrityError, match="journal unsupported"),
        readonly_authority_sql_snapshot(database, authority_gate=gate),
    ):
        pytest.fail("journal yielded")
    assert _files(database.parent) == before


@pytest.mark.parametrize("tail", ["uncommitted", "reset"])
def test_native_recovery_preserves_legitimate_wal_tails(tmp_path: Path, tail: str) -> None:
    from chiplog.platform.readonly_sqlite_snapshot import readonly_authority_sql_snapshot

    producer = tmp_path / "producer.sqlite"
    directory = tmp_path / "source"
    directory.mkdir()
    database = directory / "authority.sqlite"
    with closing(sqlite3.connect(producer, isolation_level=None)) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE sentinel(value)")
        writer.execute("INSERT INTO sentinel VALUES ('committed')")
        if tail == "uncommitted":
            writer.execute("PRAGMA cache_size=1")
            writer.execute("BEGIN")
            size_before = Path(str(producer) + "-wal").stat().st_size
            writer.executemany("INSERT INTO sentinel VALUES (?)", [("x" * 4000,)] * 30)
            assert Path(str(producer) + "-wal").stat().st_size > size_before
        else:
            # Checkpoint without truncating; new generation leaves old frames behind.
            writer.executemany("INSERT INTO sentinel VALUES (?)", [("x" * 4000,)] * 30)
            writer.execute("DELETE FROM sentinel WHERE value != 'committed'")
            writer.execute("PRAGMA wal_checkpoint(RESTART)")
            size_before = Path(str(producer) + "-wal").stat().st_size
            writer.execute("UPDATE sentinel SET value='committed'")
            assert Path(str(producer) + "-wal").stat().st_size == size_before
        shutil.copyfile(producer, database)
        shutil.copyfile(Path(str(producer) + "-wal"), Path(str(database) + "-wal"))
    gate = AuthorityGate.for_database(database)
    with gate.hold():
        pass
    before = _files(directory)
    with (
        gate.hold_existing(),
        readonly_authority_sql_snapshot(database, authority_gate=gate) as connection,
    ):
        assert connection.execute("SELECT value FROM sentinel").fetchall() == [("committed",)]
    assert _files(directory) == before


def test_private_sqlite_failure_is_typed_and_disposes_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import chiplog.platform.readonly_sqlite_snapshot as snapshot

    database, gate = _clean_source(tmp_path)
    database.write_bytes(b"invalid sqlite image" * 100)
    before = _files(database.parent)
    directories = []
    original = snapshot.TemporaryDirectory

    def track_directory(*args, **kwargs):
        directory = original(*args, **kwargs)
        directories.append(Path(directory.name))
        return directory

    monkeypatch.setattr(snapshot, "TemporaryDirectory", track_directory)
    with (
        gate.hold_existing(),
        pytest.raises(snapshot.AuthoritySQLSnapshotIntegrityError) as error,
        snapshot.readonly_authority_sql_snapshot(database, authority_gate=gate),
    ):
        pytest.fail("invalid database yielded")
    assert isinstance(error.value.__cause__, sqlite3.Error)
    assert directories and all(not directory.exists() for directory in directories)
    assert _files(database.parent) == before


@pytest.mark.parametrize("alias", ["symlink", "hardlink", "fifo"])
def test_aliased_or_nonregular_wal_refuses(tmp_path: Path, alias: str) -> None:
    from chiplog.platform.readonly_sqlite_snapshot import (
        AuthoritySQLSnapshotIntegrityError,
        readonly_authority_sql_snapshot,
    )

    database, gate = _clean_source(tmp_path)
    wal = Path(str(database) + "-wal")
    if alias == "symlink":
        wal.symlink_to(database)
    elif alias == "hardlink":
        other = tmp_path / "other"
        other.write_bytes(b"wal")
        os.link(other, wal)
    else:
        os.mkfifo(wal)
    with (
        gate.hold_existing(),
        pytest.raises(AuthoritySQLSnapshotIntegrityError),
        readonly_authority_sql_snapshot(database, authority_gate=gate),
    ):
        pytest.fail("aliased/nonregular WAL yielded")


def test_same_inode_content_mutation_during_copy_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import chiplog.platform.readonly_sqlite_snapshot as snapshot

    database, gate = _clean_source(tmp_path)
    original = snapshot._copy_source

    def mutate_after_copy(stack, source, destination):
        result = original(stack, source, destination)
        with source.open("r+b") as target:
            target.seek(-1, 2)
            target.write(b"x")
        return result

    monkeypatch.setattr(snapshot, "_copy_source", mutate_after_copy)
    with (
        gate.hold_existing(),
        pytest.raises(snapshot.AuthoritySQLSnapshotIntegrityError),
        snapshot.readonly_authority_sql_snapshot(database, authority_gate=gate),
    ):
        pytest.fail("mutated source yielded")


def test_trust_role_is_limited_to_exact_companion_of_matching_gate(tmp_path: Path) -> None:
    from chiplog.platform.readonly_sqlite_snapshot import (
        AuthoritySQLSnapshotIntegrityError,
        readonly_authority_sql_snapshot,
    )

    database, gate = _clean_source(tmp_path)
    trust = database.with_suffix(database.suffix + ".trust.sqlite3")
    with closing(sqlite3.connect(trust)) as connection:
        connection.execute("CREATE TABLE trust_sentinel(value)")
    before = _files(database.parent)
    with gate.hold_existing():
        with readonly_authority_sql_snapshot(trust, authority_gate=gate, role="TRUST") as private:
            assert private.execute("SELECT * FROM trust_sentinel").fetchall() == []
        for wrong in (database, database.parent / "other.trust.sqlite3"):
            with (
                pytest.raises(AuthoritySQLSnapshotIntegrityError),
                readonly_authority_sql_snapshot(wrong, authority_gate=gate, role="TRUST"),
            ):
                pytest.fail("wrong trust path yielded")
        with (
            pytest.raises(AuthoritySQLSnapshotIntegrityError),
            readonly_authority_sql_snapshot(trust, authority_gate=gate),
        ):
            pytest.fail("TRUST accepted as MAIN")
    other = AuthorityGate.for_database(database.parent / "other.sqlite")
    with (
        other.hold(),
        pytest.raises(AuthoritySQLSnapshotIntegrityError),
        readonly_authority_sql_snapshot(trust, authority_gate=other, role="TRUST"),
    ):
        pytest.fail("wrong gate yielded")
    other.path.unlink()
    assert _files(database.parent) == before
