"""One physical read transaction shared by the registered workspace readers."""

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ReadSnapshot:
    path: Path
    connection: sqlite3.Connection
    identity: tuple[int, int]


_CURRENT: ContextVar[ReadSnapshot | None] = ContextVar("workspace_snapshot", default=None)


@contextmanager
def workspace_snapshot(path: Path) -> Iterator[ReadSnapshot]:
    if _CURRENT.get() is not None:
        raise ValueError("nested workspace snapshot")
    path = path.resolve(strict=True)
    stat = path.stat()
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")
        snapshot = ReadSnapshot(path, connection, (stat.st_dev, stat.st_ino))
        token = _CURRENT.set(snapshot)
        try:
            yield snapshot
        finally:
            _CURRENT.reset(token)


@contextmanager
def read_connection(path: Path) -> Iterator[sqlite3.Connection]:
    """Join the broker's active cut; historical standalone callers get a local cut."""
    current = _CURRENT.get()
    if current is not None:
        stat = path.stat()
        if path.resolve() != current.path or (stat.st_dev, stat.st_ino) != current.identity:
            raise ValueError("workspace reader attempted another physical database")
        if not current.connection.in_transaction:
            raise ValueError("workspace read transaction ended early")
        yield current.connection
    else:
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("BEGIN")
            yield connection


__all__ = ["ReadSnapshot", "read_connection", "workspace_snapshot"]
