"""Private async ownership fence for enrolled H1 post-seal recovery work.

This is intentionally narrower than the coordinator lifecycle: it serializes
an installed recovery role across tasks and processes, while the coordinator
must still drain/revoke owner IPC before releasing its lease.
"""

from __future__ import annotations

import asyncio
import fcntl
import os
import threading
import weakref
from asyncio import Task
from contextlib import suppress
from typing import Any

from chiplog.composition.h1_launch_enrollment import EnrolledH1RecoveryMount

_fences: weakref.WeakSet[_H1RecoveryExecutionFence] = weakref.WeakSet()
_fork_lock = threading.Lock()


def _before_fork() -> None:
    _fork_lock.acquire()


def _after_fork_parent() -> None:
    _fork_lock.release()


def _after_fork_child() -> None:
    global _fences, _fork_lock
    for fence in _fences:
        fence._invalidate_after_fork()
    _fences = weakref.WeakSet()
    _fork_lock = threading.Lock()


os.register_at_fork(
    before=_before_fork, after_in_parent=_after_fork_parent, after_in_child=_after_fork_child
)


class _H1RecoveryExecutionFence:
    """One task-only lease over one separately enrolled, existing-only inode."""

    __slots__ = (
        "__weakref__",
        "_candidate_fds",
        "_closed",
        "_lease",
        "_mount",
        "_pid",
        "_poll_interval",
    )

    def __init__(self, mount: EnrolledH1RecoveryMount, *, poll_interval: float = 0.005) -> None:
        if type(mount) is not EnrolledH1RecoveryMount:
            raise TypeError("recovery execution fence requires an enrolled recovery mount")
        if type(poll_interval) not in (int, float) or poll_interval <= 0:
            raise ValueError("recovery execution fence poll interval must be positive")
        self._mount = mount
        self._poll_interval = float(poll_interval)
        self._pid = os.getpid()
        self._lease: _H1RecoveryExecutionLease | None = None
        self._candidate_fds: set[int] = set()
        self._closed = False
        with _fork_lock:
            _fences.add(self)

    async def acquire(self) -> _H1RecoveryExecutionLease:
        """Wait using nonblocking flock attempts; every attempt owns a fresh fd."""
        task = _current_task()
        self._require_current_process()
        if self._closed:
            raise RuntimeError("recovery execution fence is closed")
        if self._lease is not None and self._lease._task is task:
            raise RuntimeError("recovery execution fence is non-reentrant")
        while True:
            with _fork_lock:
                fd = self._mount._open_existing_execution_fence()
                self._candidate_fds.add(fd)
            locked = False
            try:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                except BlockingIOError:
                    pass
                if not locked:
                    with _fork_lock:
                        os.close(fd)
                        self._candidate_fds.discard(fd)
                    await asyncio.sleep(self._poll_interval)
                    continue
                # Make cancellation between the kernel acquisition and lease
                # publication observable, then clean the held fresh fd below.
                await asyncio.sleep(0)
                self._require_current_process()
                self._mount.assert_current()
                if self._closed:
                    raise RuntimeError("recovery execution fence is closed")
                if self._lease is not None:
                    # This cannot arise from normal task scheduling, but a
                    # fail-closed guard prevents a borrowed in-process lease.
                    raise RuntimeError("recovery execution fence ownership changed")
                with _fork_lock:
                    lease = _H1RecoveryExecutionLease(self, task, fd, self._pid)
                    self._lease = lease
                    self._candidate_fds.discard(fd)
                return lease
            except BaseException:
                with _fork_lock:
                    if locked:
                        with suppress(OSError):
                            fcntl.flock(fd, fcntl.LOCK_UN)
                    with suppress(OSError):
                        os.close(fd)
                    self._candidate_fds.discard(fd)
                raise

    def close(self) -> None:
        self._require_current_process()
        if self._lease is not None:
            raise RuntimeError("recovery execution fence has an active lease")
        self._closed = True

    def _release(self, lease: _H1RecoveryExecutionLease) -> None:
        self._require_current_process()
        with _fork_lock:
            if not lease._task_owns(self):
                raise RuntimeError("recovery execution fence lease is not owned by this task")
            error: BaseException | None = None
            try:
                self._mount.assert_current()
            except BaseException as exc:
                error = exc
            try:
                fcntl.flock(lease._fd, fcntl.LOCK_UN)
            finally:
                try:
                    os.close(lease._fd)
                finally:
                    self._lease = None
                    lease._released = True
        if error is not None:
            raise error

    def _invalidate_after_fork(self) -> None:
        """Close inherited descriptions without unlocking the parent's flock."""
        descriptors = set(self._candidate_fds)
        if self._lease is not None:
            descriptors.add(self._lease._fd)
        for fd in descriptors:
            if fd is not None:
                with suppress(OSError):
                    os.close(fd)
        self._candidate_fds.clear()
        if self._lease is not None:
            self._lease._released = True
        self._lease = None
        self._closed = True

    def _require_current_process(self) -> None:
        if os.getpid() != self._pid:
            raise RuntimeError("recovery execution fence cannot cross fork")


class _H1RecoveryExecutionLease:
    __slots__ = ("_fd", "_fence", "_pid", "_released", "_task")

    def __init__(
        self, fence: _H1RecoveryExecutionFence, task: Task[Any], fd: int, pid: int
    ) -> None:
        self._fence, self._task, self._fd, self._pid, self._released = fence, task, fd, pid, False

    async def __aenter__(self) -> _H1RecoveryExecutionLease:
        self.require_owned()
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        del exc_type, exc, traceback
        self._fence._release(self)
        return False

    def require_owned(self) -> None:
        if not self._task_owns(self._fence):
            raise RuntimeError("recovery execution fence lease is not owned by this task")
        self._fence._mount.assert_current()

    def _task_owns(self, fence: _H1RecoveryExecutionFence) -> bool:
        return (
            not self._released
            and os.getpid() == self._pid
            and fence._lease is self
            and _current_task() is self._task
        )


def _current_task() -> Task[Any]:
    task = asyncio.current_task()
    if task is None:
        raise RuntimeError("recovery execution fence requires an asyncio task")
    return task
