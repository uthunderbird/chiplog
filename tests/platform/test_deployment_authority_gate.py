"""Deployment cache transactions share the canonical authority exclusion scope."""

from __future__ import annotations

import hmac
import shutil
import sqlite3
import sys
from multiprocessing import get_context
from pathlib import Path
from subprocess import Popen, TimeoutExpired

import pytest

from chiplog.adapters.driven.deployment_trust import IndependentTenantDecisionJournal
from chiplog.platform.authority_gate import AuthorityGate
from chiplog.platform.r8_gate import (
    BrokerDeploymentGate,
    GateDecisionIndeterminate,
    GateIntegrityError,
)
from tests.support.deployment_gate import KEY, PAYLOAD, entitlement, request, signature

_WORKER = """
import sys
from pathlib import Path
from multiprocessing.connection import Connection
from chiplog.adapters.driven.deployment_trust import IndependentTenantDecisionJournal
from chiplog.platform.authority_gate import AuthorityGate
from chiplog.platform.r8_gate import BrokerDeploymentGate
root = Path(sys.argv[1])
pipe = Connection(int(sys.argv[2]))
authority = AuthorityGate.for_database(root / 'main.sqlite3')
journal = IndependentTenantDecisionJournal.for_authority_bundle(
    root / 'gate.journal', authority_gate=authority)
def construct():
    return BrokerDeploymentGate(root / 'gate.sqlite3', tenant_id='t', surfaces=(),
        journal=journal, authority_gate=authority, clock=lambda: 1)
mode = sys.argv[3]
gate = construct() if mode == 'observe' else None
pipe.send('ready')
assert pipe.poll(10) and pipe.recv() == 'run'
pipe.send('attempting')
if gate is None:
    construct()
else:
    assert gate.observe() is None
pipe.send('finished')
pipe.close()
"""


def _open(root: Path) -> tuple[BrokerDeploymentGate, IndependentTenantDecisionJournal]:
    authority = AuthorityGate.for_database(root / "main.sqlite3")
    journal = IndependentTenantDecisionJournal.for_authority_bundle(
        root / "gate.journal", authority_gate=authority
    )
    return BrokerDeploymentGate(
        root / "gate.sqlite3",
        tenant_id="t",
        surfaces=(("synthetic.send", "synthetic"),),
        authenticate=lambda payload, proof: hmac.compare_digest(
            hmac.digest(KEY, payload, "sha256"), proof
        ),
        journal=journal,
        authority_gate=authority,
        clock=lambda: 1,
    ), journal


@pytest.mark.parametrize("mode", ["observe", "construct"])
def test_deployment_waits_for_authority_before_sqlite(tmp_path: Path, mode: str) -> None:
    authority = AuthorityGate.for_database(tmp_path / "main.sqlite3")
    parent, child = get_context("spawn").Pipe()
    process = Popen(
        [sys.executable, "-c", _WORKER, str(tmp_path), str(child.fileno()), mode],
        pass_fds=(child.fileno(),),
    )
    child.close()
    try:
        assert parent.poll(10) and parent.recv() == "ready"
        with authority.hold():
            parent.send("run")
            assert parent.poll(5) and parent.recv() == "attempting"
            assert not parent.poll(0.2), "deployment operation escaped authority exclusion"
            database = tmp_path / "gate.sqlite3"
            if mode == "construct":
                assert not database.exists(), (
                    "constructor created SQLite before acquiring authority"
                )
            else:
                # If the contender acquired SQLite before blocking on authority,
                # this independent writer would fail with 'database is locked'.
                with sqlite3.connect(database, timeout=0.1) as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    assert connection.execute("SELECT COUNT(*) FROM gate_current").fetchone() == (
                        0,
                    )
                    connection.rollback()
        assert parent.poll(10) and parent.recv() == "finished"
        process.wait(5)
        assert process.returncode == 0
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(5)
        except TimeoutExpired:
            process.kill()
            process.wait(5)
        parent.close()


@pytest.mark.parametrize(
    "mismatch", ["missing_gate", "other_gate", "unbound_journal", "no_journal"]
)
def test_deployment_rejects_mismatched_authority_binding(tmp_path: Path, mismatch: str) -> None:
    bound_authority = AuthorityGate.for_database(tmp_path / "main.sqlite3")
    authority: AuthorityGate | None = bound_authority
    journal: IndependentTenantDecisionJournal | None = (
        IndependentTenantDecisionJournal.for_authority_bundle(
            tmp_path / "gate.journal", authority_gate=bound_authority
        )
    )
    if mismatch == "missing_gate":
        authority = None
    elif mismatch == "other_gate":
        authority = AuthorityGate.for_database(tmp_path / "other.sqlite3")
    elif mismatch == "unbound_journal":
        journal = IndependentTenantDecisionJournal(tmp_path / "unbound.journal")
    else:
        journal = None
    with pytest.raises(GateIntegrityError):
        BrokerDeploymentGate(
            tmp_path / "gate.sqlite3",
            tenant_id="t",
            surfaces=(),
            journal=journal,
            authority_gate=authority,
            clock=lambda: 1,
        )
    assert not (tmp_path / "gate.sqlite3").exists()


def _replace(database: Path) -> None:
    replacement = database.with_suffix(".copy")
    shutil.copyfile(database, replacement)
    replacement.replace(database)


def test_deployment_rejects_identical_database_replacement(tmp_path: Path) -> None:
    gate, _ = _open(tmp_path)
    value = entitlement()
    assert gate.import_current(value, signature(value), expected=None)
    _replace(tmp_path / "gate.sqlite3")
    assert gate.check(request(value)).disposition == "HOLD"
    assert gate.commit_handoff(request(value), PAYLOAD).disposition == "HOLD"
    with pytest.raises(GateIntegrityError):
        gate.observe()


def test_bound_handoff_materialization_survives_restart(tmp_path: Path) -> None:
    gate, _ = _open(tmp_path)
    value = entitlement()
    assert gate.import_current(value, signature(value), expected=None)
    assert gate.commit_handoff(request(value), PAYLOAD, execution=b"exact").disposition == (
        "PERMIT_EXACT_EVALUATION"
    )
    restarted, _ = _open(tmp_path)
    assert restarted.observe() == value
    assert restarted.pending() == (("op", b"exact"),)
    crossed = restarted.crossed("op")
    assert crossed is not None and crossed[1] == PAYLOAD
    restarted.materialized("op")
    final, _ = _open(tmp_path)
    assert final.pending() == ()
    assert final.check(request(value, "second")).disposition == "HOLD"


def test_replacement_after_durable_handoff_is_indeterminate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate, journal = _open(tmp_path)
    value = entitlement()
    assert gate.import_current(value, signature(value), expected=None)
    append = journal.append

    def append_then_replace(decision: bytes, predecessor: str | None) -> str:
        result = append(decision, predecessor)
        _replace(tmp_path / "gate.sqlite3")
        return result

    monkeypatch.setattr(journal, "append", append_then_replace)
    with pytest.raises(GateDecisionIndeterminate):
        gate.commit_handoff(request(value), PAYLOAD, execution=b"exact")
    restored, _ = _open(tmp_path)
    assert restored.pending() == (("op", b"exact"),)


def test_replaced_authority_lock_after_lost_ack_is_indeterminate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate, journal = _open(tmp_path)
    value = entitlement()
    assert gate.import_current(value, signature(value), expected=None)
    authority = gate.authority_gate
    assert authority is not None
    append = journal.append

    def append_then_lose_ack(decision: bytes, predecessor: str | None) -> str:
        append(decision, predecessor)
        _replace(authority.path)
        raise OSError("durable handoff acknowledgement lost")

    monkeypatch.setattr(journal, "append", append_then_lose_ack)
    try:
        with pytest.raises(GateDecisionIndeterminate):
            result = gate.commit_handoff(request(value), PAYLOAD, execution=b"exact")
            pytest.fail(f"durable handoff was classified as {result.disposition}")
    finally:
        # Read without the deliberately broken authority lock to establish what
        # actually became durable, independently of the adapter's disposition.
        independent = IndependentTenantDecisionJournal(tmp_path / "gate.journal")
        entries = independent.entries()
        assert len(entries) == 2
        assert b'"kind":"HANDOFF"' in entries[-1][2]
        assert f'"payload":"{PAYLOAD.hex()}"'.encode() in entries[-1][2]
