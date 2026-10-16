"""Disposable SQL observation of an existing, gate-held authority cut.

SQLite recovers only the private raw main/WAL copy. Its recovered prefix is not
history authentication or admission evidence. Any rollback journal is unsupported,
including a nonhot journal. Source SHM is neither opened nor copied.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from collections.abc import Iterator
from contextlib import ExitStack, closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import BinaryIO, Literal

from chiplog.platform.authority_gate import (
    AuthorityGate,
    AuthorityGateError,
    checked_file_identity,
)


class AuthoritySQLSnapshotIntegrityError(RuntimeError):
    """The source cut or disposable SQLite inspection could not be established."""


def _metadata(descriptor: int) -> tuple[int, ...]:
    value = os.fstat(descriptor)
    return (
        value.st_dev,
        value.st_ino,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _digest(stream: BinaryIO) -> bytes:
    stream.seek(0)
    digest = hashlib.sha256()
    while block := stream.read(1024 * 1024):
        digest.update(block)
    return digest.digest()


@dataclass(frozen=True)
class _Source:
    path: Path
    stream: BinaryIO
    metadata: tuple[int, ...]
    digest: bytes

    def recheck(self) -> None:
        checked_file_identity(self.path, (str(self.path), self.metadata[0], self.metadata[1]))
        if _metadata(self.stream.fileno()) != self.metadata or _digest(self.stream) != self.digest:
            raise AuthorityGateError(f"source changed: {self.path}")
        # Detect changes during hashing as well as before it.
        if _metadata(self.stream.fileno()) != self.metadata:
            raise AuthorityGateError(f"source changed while hashing: {self.path}")


def _copy_source(stack: ExitStack, source: Path, destination: Path) -> _Source:
    identity = checked_file_identity(source)
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    stream = stack.enter_context(os.fdopen(os.open(source, flags), "rb"))
    metadata = _metadata(stream.fileno())
    if identity != (str(source), *metadata[:2]) or metadata[2] != 1:
        raise AuthorityGateError(f"opened source identity differs: {source}")
    checked_file_identity(source, identity)
    digest = hashlib.sha256()
    with destination.open("xb") as target:
        while block := stream.read(1024 * 1024):
            digest.update(block)
            target.write(block)
    return _Source(source, stream, metadata, digest.digest())


def _present(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


@contextmanager
def readonly_authority_sql_snapshot(
    database: Path, *, authority_gate: AuthorityGate, role: Literal["MAIN", "TRUST"] = "MAIN"
) -> Iterator[sqlite3.Connection]:
    """Yield a private active read transaction; refuse changes at both cuts.

    Caller must keep its matching gate held synchronously throughout the scope.
    Rechecks detect observed mutation, not hostile change-and-restore. Native
    SQLite recovery accepts valid uncommitted/reset tails and may recover a
    prefix of damaged WAL; authenticated complete-history checks remain external.
    """
    database = Path(database).absolute()
    wal = Path(str(database) + "-wal")
    journal = Path(str(database) + "-journal")
    with ExitStack() as stack:
        try:
            canonical = authority_gate.database
            if role == "TRUST":
                canonical = canonical.with_suffix(canonical.suffix + ".trust.sqlite3")
            elif role != "MAIN":
                raise AuthorityGateError("unsupported canonical SQL role")
            if database != canonical:
                raise AuthorityGateError("database does not match canonical gate binding")
            authority_gate.require_held()
            if _present(journal):
                raise AuthorityGateError(f"rollback journal unsupported: {journal}")
            has_wal = _present(wal)
            directory = Path(stack.enter_context(TemporaryDirectory(prefix="chiplog-sql-")))
            private = directory / "authority.sqlite"
            sources = [_copy_source(stack, database, private)]
            if has_wal:
                sources.append(_copy_source(stack, wal, Path(str(private) + "-wal")))

            def recheck() -> None:
                authority_gate.require_held()
                if _present(journal) or _present(wal) != has_wal:
                    raise AuthorityGateError("authority companion namespace changed")
                for source in sources:
                    source.recheck()

            connection = stack.enter_context(
                closing(sqlite3.connect(private, isolation_level=None))
            )
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            connection.execute("SELECT name FROM sqlite_master").fetchall()
            recheck()
        except (OSError, AuthorityGateError, sqlite3.Error) as error:
            raise AuthoritySQLSnapshotIntegrityError(
                f"operation=readonly_authority_sql_snapshot path={database}: {error}"
            ) from error
        yield connection
        try:
            recheck()
        except (OSError, AuthorityGateError) as error:
            raise AuthoritySQLSnapshotIntegrityError(
                f"operation=readonly_authority_sql_snapshot path={database}: {error}"
            ) from error
