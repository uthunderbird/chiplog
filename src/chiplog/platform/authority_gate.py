"""Synchronous cross-process exclusion for one canonical authority bundle.

This is a lock, not an authorization credential. Canonical composition must bind
all admitted source mutations and the writer to it, without awaiting inside hold.
"""

from __future__ import annotations

import fcntl
import os
import stat
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path


class AuthorityGateError(RuntimeError):
    pass


FileIdentity = tuple[str, int, int]


def checked_file_identity(path: Path, expected: FileIdentity | None = None) -> FileIdentity:
    """Check a canonical source path against its opened object's identity."""
    try:
        metadata = path.lstat()
        identity = (str(path), metadata.st_dev, metadata.st_ino)
        if (
            path.resolve(strict=True) != path
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or (expected is not None and identity != expected)
        ):
            raise AuthorityGateError(
                f"operation=source_identity path={path}: source replaced or aliased"
            )
        return identity
    except OSError as error:
        raise AuthorityGateError(
            f"operation=source_identity path={path}: source unavailable"
        ) from error


@dataclass
class _GateState:
    thread_lock: threading.RLock = field(default_factory=threading.RLock)
    descriptor: int | None = None
    owner: int | None = None
    depth: int = 0
    identity: tuple[int, int] | None = None


_states: dict[Path, _GateState] = {}
_registry_lock = threading.Lock()


def _before_fork() -> None:
    # Descriptor creation/registration and close are indivisible with fork.
    _registry_lock.acquire()


def _after_fork_parent() -> None:
    _registry_lock.release()


def _after_fork_child() -> None:
    global _states, _registry_lock
    for state in _states.values():
        if state.descriptor is not None:
            # Do not LOCK_UN: flock ownership shares the inherited description.
            os.close(state.descriptor)
    _states = {}
    _registry_lock = threading.Lock()


os.register_at_fork(
    before=_before_fork, after_in_parent=_after_fork_parent, after_in_child=_after_fork_child
)


def _state(path: Path) -> _GateState:
    with _registry_lock:
        return _states.setdefault(path, _GateState())


@dataclass(frozen=True)
class AuthorityGate:
    database: Path
    path: Path = field(init=False)

    def __post_init__(self) -> None:
        canonical = self.database.resolve(strict=False)
        object.__setattr__(self, "database", canonical)
        object.__setattr__(
            self, "path", canonical.with_suffix(canonical.suffix + ".authority.lock")
        )
        self._check_database()

    @classmethod
    def for_database(cls, database: Path) -> AuthorityGate:
        return cls(database)

    def _error(self, reason: str) -> AuthorityGateError:
        return AuthorityGateError(f"operation=authority_gate path={self.path}: {reason}")

    def _check_database(self) -> None:
        if self.database.resolve(strict=False) != self.database:
            raise self._error("canonical database binding changed")
        try:
            metadata = self.database.lstat()
        except FileNotFoundError:
            return
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise self._error("database must be a regular file without hard-link aliases")

    def _check_descriptor(
        self, descriptor: int, expected: tuple[int, int] | None = None
    ) -> tuple[int, int]:
        actual = os.fstat(descriptor)
        named = self.path.lstat()
        if (
            not stat.S_ISREG(actual.st_mode)
            or not stat.S_ISREG(named.st_mode)
            or actual.st_nlink != 1
            or named.st_nlink != 1
            or (actual.st_dev, actual.st_ino) != (named.st_dev, named.st_ino)
            or stat.S_IMODE(actual.st_mode) != 0o600
            or actual.st_uid != os.geteuid()
            or (expected is not None and (actual.st_dev, actual.st_ino) != expected)
        ):
            raise self._error("lock file identity, ownership or permissions differ")
        return actual.st_dev, actual.st_ino

    @contextmanager
    def hold(self) -> Iterator[None]:
        entered_pid = os.getpid()
        state = _state(self.path)
        state.thread_lock.acquire()
        entered = False
        body_error: BaseException | None = None
        outermost = state.depth == 0
        try:
            self._check_database()
            if outermost:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with _registry_lock:
                    state.descriptor = os.open(
                        self.path,
                        os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC,
                        0o600,
                    )
                state.identity = self._check_descriptor(state.descriptor, state.identity)
                fcntl.flock(state.descriptor, fcntl.LOCK_EX)
                self._check_descriptor(state.descriptor, state.identity)
                self._check_database()
                state.owner = threading.get_ident()
            elif state.owner != threading.get_ident() or state.descriptor is None:
                raise self._error("inconsistent local lock ownership")
            assert state.descriptor is not None
            self._check_descriptor(state.descriptor, state.identity)
            state.depth += 1
            entered = True
            try:
                yield
            except BaseException as error:
                body_error = error
                raise
            finally:
                if os.getpid() != entered_pid:
                    raise self._error("a gate scope cannot cross fork")
                # Detect replacement even when no nested require_held was needed.
                self._check_descriptor(state.descriptor, state.identity)
        except OSError as error:
            if error is body_error:
                raise
            raise self._error(str(error)) from error
        finally:
            if os.getpid() != entered_pid:
                # Child hooks closed inherited descriptors; do not mutate or
                # unlock inherited thread state while unwinding a copied scope.
                raise self._error("a gate scope cannot cross fork")
            try:
                if entered:
                    state.depth -= 1
                if outermost and state.descriptor is not None:
                    with _registry_lock:
                        descriptor, state.descriptor = state.descriptor, None
                        state.owner = None
                        try:
                            fcntl.flock(descriptor, fcntl.LOCK_UN)
                        finally:
                            os.close(descriptor)
            finally:
                state.thread_lock.release()

    def require_held(self) -> None:
        state = _state(self.path)
        if not state.thread_lock.acquire(blocking=False):
            raise self._error("current thread does not own the authority gate")
        try:
            if state.owner != threading.get_ident() or state.depth == 0 or state.descriptor is None:
                raise self._error("current thread does not own the authority gate")
            self._check_database()
            try:
                self._check_descriptor(state.descriptor, state.identity)
            except OSError as error:
                raise self._error(str(error)) from error
        finally:
            state.thread_lock.release()


__all__ = ["AuthorityGate", "AuthorityGateError", "FileIdentity", "checked_file_identity"]
