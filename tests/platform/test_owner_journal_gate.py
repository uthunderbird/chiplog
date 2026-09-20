"""Compound owner journal operations retain canonical authority exclusion."""

import sys
from multiprocessing import get_context
from pathlib import Path
from subprocess import Popen, TimeoutExpired

import pytest

from chiplog.adapters.driven.deployment_trust import IndependentTenantDecisionJournal
from chiplog.platform.authority_gate import AuthorityGate
from chiplog.platform.owner_decision_journal import IndependentOwnerDecisionJournal
from chiplog.platform.owner_publications import PreparedOwnerPublication
from tests.platform.test_owner_publications import digest, request

_WORKER = """
import sys
from pathlib import Path
from multiprocessing.connection import Connection
from chiplog.platform.authority_gate import AuthorityGate
root = Path(sys.argv[1])
pipe = Connection(int(sys.argv[2]))
gate = AuthorityGate.for_database(root / 'main.sqlite3')
pipe.send('ready')
assert pipe.poll(10) and pipe.recv() == 'run'
pipe.send('attempting')
with gate.hold():
    pipe.send('acquired')
pipe.close()
"""


def _prepared() -> PreparedOwnerPublication:
    before = digest(b"before")
    return PreparedOwnerPublication(request(before), "issued", "fence", 0, before)


def _open(
    root: Path,
) -> tuple[AuthorityGate, IndependentTenantDecisionJournal, IndependentOwnerDecisionJournal]:
    gate = AuthorityGate.for_database(root / "main.sqlite3")
    raw = IndependentTenantDecisionJournal.for_authority_bundle(
        root / "owner.journal", authority_gate=gate
    )
    return gate, raw, IndependentOwnerDecisionJournal(raw, "tenant")


@pytest.mark.parametrize("operation", ["select", "materialized"])
@pytest.mark.parametrize("boundary", ["before_append", "after_append"])
def test_compound_journal_retains_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str, boundary: str
) -> None:
    _, raw, journal = _open(tmp_path)
    prepared = _prepared()
    decision = journal.select(prepared, digest(b"after")) if operation == "materialized" else None
    parent, child = get_context("spawn").Pipe()
    process = Popen(
        [sys.executable, "-c", _WORKER, str(tmp_path), str(child.fileno())],
        pass_fds=(child.fileno(),),
    )
    child.close()
    append = raw.append
    visited = False

    def check_exclusion() -> None:
        nonlocal visited
        visited = True
        parent.send("run")
        assert parent.poll(5) and parent.recv() == "attempting"
        assert not parent.poll(0.2), "compound journal operation released authority gate"

    def checked_append(payload: bytes, predecessor: str | None) -> str:
        if boundary == "before_append":
            check_exclusion()
        result = append(payload, predecessor)
        if boundary == "after_append":
            check_exclusion()
        return result

    try:
        assert parent.poll(10) and parent.recv() == "ready"
        monkeypatch.setattr(raw, "append", checked_append)
        if decision is None:
            journal.select(prepared, digest(b"after"))
        else:
            journal.materialized(decision)
        assert visited
        assert parent.poll(10) and parent.recv() == "acquired"
        process.wait(5)
        assert process.returncode == 0
        snapshot = journal.snapshot()
        assert len(snapshot.decisions) == 1
        assert snapshot.materialized_command_ids == (
            frozenset({"command"}) if operation == "materialized" else frozenset()
        )
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(5)
        except TimeoutExpired:
            process.kill()
            process.wait(5)
        parent.close()


def test_nested_gate_and_historical_replay_do_not_append(tmp_path: Path) -> None:
    gate, raw, journal = _open(tmp_path)
    assert journal.authority_gate is gate
    prepared = _prepared()
    with gate.hold():
        decision = journal.select(prepared, digest(b"after"))
        journal.materialized(decision)
        before = raw.entries()
        assert journal.select(prepared, digest(b"after")) == decision
        journal.materialized(decision)
        assert raw.entries() == before
        assert journal.lookup("tenant", "command") == decision
        assert journal.snapshot().materialized_command_ids == frozenset({"command"})
    restarted = IndependentOwnerDecisionJournal(raw, "tenant")
    assert restarted.select(prepared, digest(b"after")) == decision
    restarted.materialized(decision)
    assert raw.entries() == before


def test_legacy_journal_remains_unbound(tmp_path: Path) -> None:
    raw = IndependentTenantDecisionJournal(tmp_path / "owner.journal")
    journal = IndependentOwnerDecisionJournal(raw, "tenant")
    assert journal.authority_gate is None
    decision = journal.select(_prepared(), digest(b"after"))
    journal.materialized(decision)
    assert journal.snapshot().materialized_command_ids == frozenset({"command"})
