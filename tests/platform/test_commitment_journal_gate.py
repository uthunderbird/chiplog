"""AMR gate exclusion, strict anchor reads and durable atomic replacement."""

import hashlib
import hmac
import json
import os
import stat
import sys
from multiprocessing import get_context
from pathlib import Path
from subprocess import Popen, TimeoutExpired

import pytest

from chiplog.platform.authority_gate import AuthorityGate
from chiplog.platform.authority_reads import (
    AuthorityCommitmentIndeterminateError,
    AuthorityCommitmentJournal,
)

_WORKER = """
import sys
from pathlib import Path
from multiprocessing.connection import Connection
from chiplog.platform.authority_gate import AuthorityGate
from chiplog.platform.authority_reads import AuthorityCommitmentJournal
pipe = Connection(int(sys.argv[2]))
database = Path(sys.argv[1])
journal = AuthorityCommitmentJournal(database, b'secret',
    authority_gate=AuthorityGate.for_database(database))
pipe.send('ready')
assert pipe.poll(5) and pipe.recv() == 'commit'
pipe.send('attempting')
journal.commit('tenant', 'new')
pipe.send('done')
"""


def _path(database: Path) -> Path:
    return database.with_suffix(database.suffix + ".r7-authority-commitment.json")


def test_separate_process_commit_waits_for_gate(tmp_path: Path) -> None:
    database = tmp_path / "main.sqlite3"
    gate = AuthorityGate.for_database(database)
    journal = AuthorityCommitmentJournal(database, b"secret", authority_gate=gate)
    journal.commit("tenant", "old")
    parent, child = get_context("spawn").Pipe()
    process = Popen(
        [sys.executable, "-c", _WORKER, str(database), str(child.fileno())],
        pass_fds=(child.fileno(),),
    )
    child.close()
    try:
        assert parent.poll(5) and parent.recv() == "ready"
        with gate.hold():
            parent.send("commit")
            assert parent.poll(5) and parent.recv() == "attempting"
            assert not parent.poll(0.2)
            assert journal.load("tenant") == "old"
        assert parent.poll(5) and parent.recv() == "done"
        process.wait(5)
        assert process.returncode == 0
        assert journal.load("tenant") == "new"
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(5)
        except TimeoutExpired:
            process.kill()
            process.wait(5)
        parent.close()


def test_binding_and_legitimate_replacement(tmp_path: Path) -> None:
    database = tmp_path / "main.sqlite3"
    gate = AuthorityGate.for_database(database)
    journal = AuthorityCommitmentJournal(database, b"secret", authority_gate=gate)
    assert journal.authority_gate is gate
    assert journal.load("tenant") is None
    journal.commit("tenant", "first")
    first_inode = _path(database).stat().st_ino
    with gate.hold():
        journal.commit("tenant", "second")
        assert _path(database).stat().st_ino != first_inode
        assert journal.load("tenant") == "second"
    legacy = AuthorityCommitmentJournal(database, b"secret")
    assert legacy.authority_gate is None
    assert legacy.load("tenant") == "second"
    with pytest.raises(ValueError, match="differs"):
        AuthorityCommitmentJournal(tmp_path / "other.sqlite3", b"secret", authority_gate=gate)


@pytest.mark.parametrize("kind", ["symlink", "dangling", "hardlink", "directory", "fifo"])
def test_invalid_target_refused_on_load_and_first_commit(tmp_path: Path, kind: str) -> None:
    database = tmp_path / "main.sqlite3"
    target = _path(database)
    victim = tmp_path / "victim"
    victim.write_bytes(b"untouched")
    if kind == "symlink":
        target.symlink_to(victim)
    elif kind == "dangling":
        target.symlink_to(tmp_path / "missing")
    elif kind == "hardlink":
        os.link(victim, target)
    elif kind == "directory":
        target.mkdir()
    else:
        os.mkfifo(target)
    journal = AuthorityCommitmentJournal(
        database, b"secret", authority_gate=AuthorityGate.for_database(database)
    )
    with pytest.raises(RuntimeError, match="regular"):
        journal.load("tenant")
    with pytest.raises(RuntimeError, match="regular"):
        journal.commit("tenant", "new")
    assert victim.read_bytes() == b"untouched"


@pytest.mark.parametrize(
    "value",
    [
        [],
        {},
        {"payload": {}, "mac": "wrong"},
        {"payload": {"tenant_id": "tenant", "commitment": 5}, "mac": "sign"},
        {"payload": {"tenant_id": 5, "commitment": "value"}, "mac": "sign"},
        {"payload": {"tenant_id": "tenant", "commitment": "value", "extra": 1}, "mac": "sign"},
        {"payload": {"tenant_id": "tenant", "commitment": "value"}, "mac": "sign", "extra": 1},
        {"payload": {"tenant_id": "tenant", "commitment": "value"}, "mac": 1},
        {"payload": {"tenant_id": "tenant", "commitment": "value"}, "mac": "wrong"},
        {"payload": {"tenant_id": "other", "commitment": "value"}, "mac": "sign"},
    ],
)
def test_malformed_authenticated_envelope_rejected(tmp_path: Path, value: object) -> None:
    database = tmp_path / "main.sqlite3"
    if isinstance(value, dict) and value.get("mac") == "sign":
        payload = json.dumps(value["payload"], sort_keys=True, separators=(",", ":")).encode()
        value["mac"] = hmac.new(b"secret", payload, hashlib.sha256).hexdigest()
    raw = json.dumps(value).encode()
    _path(database).write_bytes(raw)
    journal = AuthorityCommitmentJournal(database, b"secret")
    with pytest.raises(RuntimeError, match="invalid"):
        journal.load("tenant")
    with pytest.raises(RuntimeError, match="invalid"):
        journal.commit("tenant", "new")
    assert _path(database).read_bytes() == raw


def test_duplicate_json_key_rejected(tmp_path: Path) -> None:
    database = tmp_path / "main.sqlite3"
    journal = AuthorityCommitmentJournal(database, b"secret")
    journal.commit("tenant", "value")
    raw = _path(database).read_bytes().replace(b'"tenant_id":', b'"tenant_id":"other","tenant_id":')
    _path(database).write_bytes(raw)
    with pytest.raises(RuntimeError, match="invalid"):
        journal.load("tenant")


def test_predictable_temporary_symlink_cannot_overwrite_victim(tmp_path: Path) -> None:
    database = tmp_path / "main.sqlite3"
    victim = tmp_path / "victim"
    victim.write_bytes(b"untouched")
    old_temporary = _path(database).with_suffix(".json.tmp")
    old_temporary.symlink_to(victim)
    journal = AuthorityCommitmentJournal(database, b"secret")
    journal.commit("tenant", "value")
    assert victim.read_bytes() == b"untouched"
    assert old_temporary.is_symlink()
    assert journal.load("tenant") == "value"


def test_directory_fsync_failure_is_indeterminate_with_exact_visible_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "main.sqlite3"
    journal = AuthorityCommitmentJournal(database, b"secret")
    journal.commit("tenant", "old")
    original = os.fsync

    def fail_directory(descriptor: int) -> None:
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError("injected directory durability failure")
        original(descriptor)

    monkeypatch.setattr(os, "fsync", fail_directory)
    with pytest.raises(AuthorityCommitmentIndeterminateError, match="replacement occurred"):
        journal.commit("tenant", "new")
    assert journal.load("tenant") == "new"
    assert json.loads(_path(database).read_bytes())["payload"] == {
        "tenant_id": "tenant",
        "commitment": "new",
    }


def test_file_fsync_failure_preserves_old_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "main.sqlite3"
    journal = AuthorityCommitmentJournal(database, b"secret")
    journal.commit("tenant", "old")

    def fail(descriptor: int) -> None:
        raise OSError("injected file durability failure")

    monkeypatch.setattr(os, "fsync", fail)
    with pytest.raises(OSError, match="injected file"):
        journal.commit("tenant", "new")
    assert journal.load("tenant") == "old"
    assert not list(tmp_path.glob("*.tmp"))


def test_replacement_during_read_is_detected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "main.sqlite3"
    journal = AuthorityCommitmentJournal(database, b"secret")
    journal.commit("tenant", "old")
    replacement = tmp_path / "replacement"
    replacement.write_bytes(_path(database).read_bytes())
    original = journal._check_descriptor
    calls = 0

    def substitute(descriptor: int) -> os.stat_result:
        nonlocal calls
        calls += 1
        if calls == 2:
            replacement.replace(_path(database))
        return original(descriptor)

    monkeypatch.setattr(journal, "_check_descriptor", substitute)
    with pytest.raises(RuntimeError, match="replaced or aliased"):
        journal.load("tenant")


def test_random_temporary_substitution_is_detected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "main.sqlite3"
    journal = AuthorityCommitmentJournal(database, b"secret")
    journal.commit("tenant", "old")
    victim = tmp_path / "victim"
    victim.write_bytes(b"untouched")
    original = os.fsync

    def substitute(descriptor: int) -> None:
        original(descriptor)
        (temporary,) = tmp_path.glob("*.tmp")
        temporary.unlink()
        temporary.symlink_to(victim)

    monkeypatch.setattr(os, "fsync", substitute)
    with pytest.raises(RuntimeError, match="temporary replaced or aliased"):
        journal.commit("tenant", "new")
    assert victim.read_bytes() == b"untouched"
    assert journal.load("tenant") == "old"
    assert not list(tmp_path.glob("*.tmp"))


def test_postreplace_identity_failure_is_indeterminate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "main.sqlite3"
    journal = AuthorityCommitmentJournal(database, b"secret")
    journal.commit("tenant", "old")
    original = os.replace

    def substitute(source: Path, destination: Path) -> None:
        original(source, destination)
        replacement = tmp_path / "replacement"
        replacement.write_bytes(destination.read_bytes())
        original(replacement, destination)

    monkeypatch.setattr(os, "replace", substitute)
    with pytest.raises(AuthorityCommitmentIndeterminateError, match="identity indeterminate"):
        journal.commit("tenant", "new")
    assert journal.load("tenant") == "new"
