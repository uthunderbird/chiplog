import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from chiplog.platform.workspace_snapshot import read_connection, workspace_snapshot


def test_readers_join_one_transaction_even_after_independent_writer_commits(tmp_path: Path) -> None:
    database = tmp_path / "db"
    with closing(sqlite3.connect(database)) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("CREATE TABLE records (value INTEGER)")
        writer.execute("INSERT INTO records VALUES (1)")
        writer.commit()
        with workspace_snapshot(database) as snapshot:
            with read_connection(database) as conversation:
                assert conversation.execute("SELECT value FROM records").fetchone() == (1,)
            writer.execute("UPDATE records SET value=2")
            writer.commit()
            with read_connection(database) as journal, read_connection(database) as policy:
                assert journal is policy is conversation is snapshot.connection
                assert journal.execute("SELECT value FROM records").fetchone() == (1,)
        with read_connection(database) as current:
            assert current.execute("SELECT value FROM records").fetchone() == (2,)


def test_another_database_or_closed_transaction_cannot_join_snapshot(tmp_path: Path) -> None:
    database, other = tmp_path / "db", tmp_path / "other"
    database.touch()
    other.touch()
    with workspace_snapshot(database) as snapshot:
        with pytest.raises(ValueError, match="another physical"), read_connection(other):
            pass
        snapshot.connection.rollback()
        with pytest.raises(ValueError, match="ended early"), read_connection(database):
            pass
