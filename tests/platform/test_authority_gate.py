"""Actual process/thread exclusion; the gate itself grants no publication authority."""

import sys
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import FrozenInstanceError
from multiprocessing import get_context
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from pathlib import Path
from queue import Queue
from subprocess import Popen, TimeoutExpired
from threading import Thread

import pytest

from chiplog.platform.authority_gate import AuthorityGate, AuthorityGateError

TIMEOUT = 5.0


def _receive(connection: Connection) -> str:
    assert connection.poll(TIMEOUT), "child did not reach the expected synchronization point"
    value = connection.recv()
    assert isinstance(value, str)
    return value


def _finish(process: BaseProcess) -> None:
    process.join(TIMEOUT)
    if process.is_alive():
        process.terminate()
        process.join(TIMEOUT)
    if process.is_alive():
        process.kill()
        process.join(TIMEOUT)
    assert not process.is_alive(), "child process survived cleanup"


_WORKER = """
import sys
from pathlib import Path
from multiprocessing.connection import Connection
from chiplog.platform.authority_gate import AuthorityGate
connection = Connection(int(sys.argv[2]))
try:
    gate = AuthorityGate.for_database(Path(sys.argv[1]))
    connection.send('ready')
    with gate.hold():
        gate.require_held()
        connection.send('entered')
        if sys.argv[3] == 'retain':
            assert connection.poll(5.0)
            assert connection.recv() == 'release'
    connection.send('released')
finally:
    connection.close()
"""


@contextmanager
def _spawned(database: Path, *, retain: bool = False) -> Iterator[tuple[Popen[bytes], Connection]]:
    context = get_context("spawn")
    parent, child = context.Pipe()
    process = Popen(
        [
            sys.executable,
            "-c",
            _WORKER,
            str(database),
            str(child.fileno()),
            "retain" if retain else "release",
        ],
        pass_fds=(child.fileno(),),
    )
    child.close()
    try:
        yield process, parent
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(TIMEOUT)
        except TimeoutExpired:
            process.kill()
            process.wait(TIMEOUT)
        assert process.poll() is not None
        parent.close()


def _normal_exit(process: BaseProcess | Popen[bytes], connection: Connection) -> None:
    assert _receive(connection) == "released"
    if isinstance(process, BaseProcess):
        process.join(TIMEOUT)
        assert not process.is_alive()
        assert process.exitcode == 0
    else:
        assert process.wait(TIMEOUT) == 0


def test_hold_requires_actual_scope_and_has_immutable_canonical_identity(tmp_path: Path) -> None:
    database = tmp_path / "tenant.sqlite"
    gate = AuthorityGate.for_database(database)
    assert gate.database == database.resolve()
    assert gate.path == Path(str(database.resolve()) + ".authority.lock")
    with pytest.raises(AuthorityGateError):
        gate.require_held()
    for attribute in ("path", "database"):
        with pytest.raises((FrozenInstanceError, AttributeError)):
            setattr(gate, attribute, tmp_path / "other.lock")
    with gate.hold():
        gate.require_held()
        with pytest.raises(AuthorityGateError):
            AuthorityGate.for_database(tmp_path / "different.sqlite").require_held()
        assert gate.path.stat().st_mode & 0o777 == 0o600
        assert gate.path.stat().st_nlink == 1
    with pytest.raises(AuthorityGateError):
        gate.require_held()


def test_separate_handles_share_same_thread_reentrant_ownership(tmp_path: Path) -> None:
    database = tmp_path / "tenant.sqlite"
    outer = AuthorityGate.for_database(database)
    inner = AuthorityGate.for_database(database)
    with outer.hold():
        with inner.hold():
            inner.require_held()
            outer.require_held()
        outer.require_held()
        inner.require_held()
    with pytest.raises(AuthorityGateError):
        inner.require_held()


def test_independent_process_enters_only_after_outermost_release(tmp_path: Path) -> None:
    database = tmp_path / "tenant.sqlite"
    gate = AuthorityGate.for_database(database)
    scope = gate.hold()
    scope.__enter__()
    released = False
    try:
        with _spawned(database) as (process, connection):
            assert _receive(connection) == "ready"
            with AuthorityGate.for_database(database).hold():
                assert not connection.poll(0.1), "rival entered a held nested scope"
            assert not connection.poll(0.1), "nested exit released the outer OS lock"
            scope.__exit__(None, None, None)
            released = True
            assert _receive(connection) == "entered"
            _normal_exit(process, connection)
    finally:
        if not released:
            scope.__exit__(None, None, None)


def test_another_thread_cannot_borrow_owner_thread_reentrancy(tmp_path: Path) -> None:
    database = tmp_path / "tenant.sqlite"
    gate = AuthorityGate.for_database(database)
    messages: Queue[str] = Queue()

    def worker() -> None:
        try:
            other = AuthorityGate.for_database(database)
            try:
                other.require_held()
            except AuthorityGateError:
                messages.put("not-owner")
            else:
                messages.put("borrowed-owner")
            with other.hold():
                other.require_held()
                messages.put("entered")
        except BaseException as error:
            messages.put(repr(error))

    thread = Thread(target=worker, daemon=True)
    try:
        with gate.hold():
            thread.start()
            assert messages.get(timeout=TIMEOUT) == "not-owner"
            thread.join(0.1)
            assert thread.is_alive(), "rival thread did not block on held gate"
            assert messages.empty(), "different thread entered the owner's scope"
        assert messages.get(timeout=TIMEOUT) == "entered"
    finally:
        thread.join(TIMEOUT)
    assert not thread.is_alive()


def test_exception_releases_and_distinct_bundles_do_not_block(tmp_path: Path) -> None:
    first = AuthorityGate.for_database(tmp_path / "first.sqlite")
    second = tmp_path / "second.sqlite"
    with pytest.raises(RuntimeError, match="scope failure"), first.hold():
        with _spawned(second) as (process, connection):
            assert _receive(connection) == "ready"
            assert _receive(connection) == "entered"
            _normal_exit(process, connection)
        raise RuntimeError("scope failure")
    with _spawned(first.database) as (process, connection):
        assert _receive(connection) == "ready"
        assert _receive(connection) == "entered"
        _normal_exit(process, connection)


def test_killed_holder_releases_kernel_lock_to_a_waiting_process(tmp_path: Path) -> None:
    database = tmp_path / "tenant.sqlite"
    with _spawned(database, retain=True) as (holder, held):
        assert _receive(held) == "ready"
        assert _receive(held) == "entered"
        with _spawned(database) as (waiter, waiting):
            assert _receive(waiting) == "ready"
            assert not waiting.poll(0.1)
            holder.kill()
            assert holder.wait(TIMEOUT) < 0
            assert holder.poll() is not None
            assert _receive(waiting) == "entered"
            _normal_exit(waiter, waiting)


def test_symlink_database_alias_resolves_to_same_gate(tmp_path: Path) -> None:
    database = tmp_path / "tenant.sqlite"
    database.write_bytes(b"")
    alias = tmp_path / "alias.sqlite"
    alias.symlink_to(database)
    first, second = AuthorityGate.for_database(database), AuthorityGate.for_database(alias)
    assert first.database == second.database
    assert first.path == second.path
    with first.hold(), second.hold():
        first.require_held()
        second.require_held()


def test_hard_linked_database_cannot_create_split_bundle_locks(tmp_path: Path) -> None:
    database = tmp_path / "tenant.sqlite"
    database.write_bytes(b"")
    alias = tmp_path / "alias.sqlite"
    alias.hardlink_to(database)
    for path in (database, alias):
        with pytest.raises(AuthorityGateError):
            AuthorityGate.for_database(path)


@pytest.mark.parametrize("mutation", ["mode", "symlink", "hardlink", "directory"])
def test_invalid_existing_gate_file_rejects_before_protected_action(
    tmp_path: Path, mutation: str
) -> None:
    database = tmp_path / "tenant.sqlite"
    lock = Path(str(database) + ".authority.lock")
    target = tmp_path / "other"
    if mutation == "directory":
        lock.mkdir()
    elif mutation in {"symlink", "hardlink"}:
        target.write_bytes(b"")
        target.chmod(0o600)
        if mutation == "symlink":
            lock.symlink_to(target)
        else:
            lock.hardlink_to(target)
    else:
        lock.write_bytes(b"")
        lock.chmod(0o644)
    entered = False
    with pytest.raises(AuthorityGateError), AuthorityGate.for_database(database).hold():
        entered = True
    assert not entered


def test_replaced_gate_inode_is_not_silently_reopened(tmp_path: Path) -> None:
    gate = AuthorityGate.for_database(tmp_path / "tenant.sqlite")
    with gate.hold():
        original_inode = gate.path.stat().st_ino
    replacement = tmp_path / "replacement"
    replacement.write_bytes(b"")
    replacement.chmod(0o600)
    replacement.replace(gate.path)
    assert gate.path.stat().st_ino != original_inode
    with pytest.raises(AuthorityGateError), gate.hold():
        pytest.fail("replaced gate identity reached protected action")
    with pytest.raises(AuthorityGateError), AuthorityGate.for_database(gate.database).hold():
        pytest.fail("a second handle silently adopted the replaced gate identity")


def test_replacement_during_hold_rejects_before_held_authority_check(tmp_path: Path) -> None:
    gate = AuthorityGate.for_database(tmp_path / "tenant.sqlite")
    entered = False
    with pytest.raises(AuthorityGateError), gate.hold():
        replacement = tmp_path / "replacement"
        replacement.write_bytes(b"")
        replacement.chmod(0o600)
        replacement.replace(gate.path)
        gate.require_held()
        entered = True
    assert not entered


def test_failed_acquisition_does_not_leave_thread_lock_owned(tmp_path: Path) -> None:
    gate = AuthorityGate.for_database(tmp_path / "tenant.sqlite")
    gate.path.write_bytes(b"")
    gate.path.chmod(0o644)
    with pytest.raises(AuthorityGateError), gate.hold():
        pytest.fail("invalid file mode admitted")
    gate.path.chmod(0o600)
    messages: Queue[str] = Queue()

    def worker() -> None:
        try:
            with AuthorityGate.for_database(gate.database).hold():
                messages.put("entered")
        except BaseException as error:
            messages.put(repr(error))

    thread = Thread(target=worker, daemon=True)
    thread.start()
    try:
        assert messages.get(timeout=TIMEOUT) == "entered"
    finally:
        thread.join(TIMEOUT)
    assert not thread.is_alive()


def _fork_worker(
    gate: AuthorityGate, inherited: AbstractContextManager[None], connection: Connection
) -> None:
    try:
        try:
            inherited.__exit__(None, None, None)
        except AuthorityGateError:
            connection.send("inherited-exit-rejected")
        else:
            connection.send("inherited-exit-accepted")
        try:
            gate.require_held()
        except AuthorityGateError:
            connection.send("not-owner")
        else:
            connection.send("inherited-owner")
        with gate.hold():
            gate.require_held()
            connection.send("entered")
        connection.send("released")
    finally:
        connection.close()


def test_fork_resets_handle_state_without_unlocking_parent_scope(tmp_path: Path) -> None:
    gate = AuthorityGate.for_database(tmp_path / "tenant.sqlite")
    scope = gate.hold()
    scope.__enter__()
    released = False
    context = get_context("fork")
    parent, child = context.Pipe()
    process = context.Process(target=_fork_worker, args=(gate, scope, child))
    try:
        process.start()
        child.close()
        assert _receive(parent) == "inherited-exit-rejected"
        assert _receive(parent) == "not-owner"
        gate.require_held()
        assert not parent.poll(0.1), "fork child unlocked or borrowed parent ownership"
        scope.__exit__(None, None, None)
        released = True
        assert _receive(parent) == "entered"
        _normal_exit(process, parent)
    finally:
        if not released:
            scope.__exit__(None, None, None)
        if process.pid is not None:
            if process.is_alive():
                process.terminate()
            _finish(process)
        parent.close()
        child.close()


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("error_type", [PermissionError, OSError])
def test_caller_oserror_escapes_unchanged_and_releases_process_lock(
    tmp_path: Path,
    error_type: type[OSError],
    nested: bool,
) -> None:
    database = tmp_path / "caller-error.sqlite3"
    gate = AuthorityGate.for_database(database)
    original = error_type("caller operation failed")
    caught: BaseException | None = None
    try:
        with gate.hold():
            gate.require_held()
            if nested:
                with AuthorityGate.for_database(database).hold():
                    raise original
            raise original
    except BaseException as error:
        caught = error
    # Probe release before asserting identity, so the failing regression still
    # establishes whether the exception cleanup released cross-process ownership.
    with _spawned(database) as (process, connection):
        assert _receive(connection) == "ready"
        assert _receive(connection) == "entered"
        _normal_exit(process, connection)
    assert caught is original
