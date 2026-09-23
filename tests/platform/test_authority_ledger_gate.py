"""Authority ledger uses the canonical gate before its independent SQLite locks."""

import hashlib
import sys
from multiprocessing import get_context
from pathlib import Path
from subprocess import Popen, TimeoutExpired

import pytest

from chiplog.platform.authority_gate import AuthorityGate, AuthorityGateError
from chiplog.platform.authority_ledger import BrokerAuthorityLedger, OperationToken

_WORKER = """
import sys
from pathlib import Path
from multiprocessing.connection import Connection
from chiplog.platform.authority_gate import AuthorityGate
from chiplog.platform.authority_ledger import BrokerAuthorityLedger
pipe = Connection(int(sys.argv[3]))
gate = AuthorityGate.for_database(Path(sys.argv[1]))
ledger = BrokerAuthorityLedger(Path(sys.argv[2]), authority_gate=gate)
pipe.send('ready')
assert pipe.poll(5) and pipe.recv() == 'allocate'
pipe.send('attempting')
pipe.send(ledger.allocate_epoch('tenant', 'endpoint2', 'key2'))
"""


def test_separate_process_epoch_waits_for_bundle_gate(tmp_path: Path) -> None:
    database = tmp_path / "main.sqlite3"
    path = tmp_path / "broker.sqlite3"
    gate = AuthorityGate.for_database(database)
    ledger = BrokerAuthorityLedger(path, authority_gate=gate)
    assert ledger.allocate_epoch("tenant", "endpoint", "key") == 1
    parent, child = get_context("spawn").Pipe()
    process = Popen(
        [sys.executable, "-c", _WORKER, str(database), str(path), str(child.fileno())],
        pass_fds=(child.fileno(),),
    )
    child.close()
    try:
        assert parent.poll(5) and parent.recv() == "ready"
        with gate.hold():
            parent.send("allocate")
            assert parent.poll(5) and parent.recv() == "attempting"
            assert not parent.poll(0.2)
            assert ledger.current_epoch("tenant") == 1
        assert parent.poll(5) and parent.recv() == 2
        process.wait(5)
        assert process.returncode == 0
        assert ledger.current_epoch("tenant") == 2
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(5)
        except TimeoutExpired:
            process.kill()
            process.wait(5)
        parent.close()


def test_nested_admission_finish_and_reconciliation_preserve_semantics(tmp_path: Path) -> None:
    gate = AuthorityGate.for_database(tmp_path / "main.sqlite3")
    ledger = BrokerAuthorityLedger(tmp_path / "broker.sqlite3", authority_gate=gate)
    assert ledger.authority_gate == gate
    with gate.hold():
        epoch = ledger.allocate_epoch("tenant", "endpoint", "key")
        token = OperationToken(
            mode="IDEMPOTENT_EXACT",
            tenant_id="tenant",
            broker_epoch=epoch,
            owner_id="owner",
            generation_id="generation",
            session_id="session",
            target_id="target",
            capability_id="capability",
            operation_kind="test",
            payload_fingerprint="payload",
            nonce="nonce",
            expiry_ns=100,
        )
        issued, novel = ledger.admit_with_novelty("command", "slot", token, 1)
        assert novel
        assert ledger.admit("command", "slot", token, 1) == issued
        assert ledger.pending("tenant") == (issued,)
        finished = issued.model_copy(
            update={
                "state": "DEFINITE",
                "result_bytes": b"done",
                "result_digest": hashlib.sha256(b"done").hexdigest(),
            }
        )
        assert ledger.finish(finished) == finished
        assert ledger.lookup("tenant", "command") == finished
        ledger.admit("pending", "slot2", token.model_copy(update={"nonce": "nonce2"}), 1)
        assert ledger.reconcile_pending("tenant", epoch + 1) == ("pending",)
        assert ledger.pending("tenant") == ()
        reconciled = ledger.lookup("tenant", "pending")
        assert reconciled is not None and reconciled.state == "OUTCOME_UNKNOWN"
        gate.require_held()


def test_bound_ledger_rejects_identical_sidecar_replacement(tmp_path: Path) -> None:
    path = tmp_path / "broker.sqlite3"
    ledger = BrokerAuthorityLedger(
        path, authority_gate=AuthorityGate.for_database(tmp_path / "main.sqlite3")
    )
    ledger.allocate_epoch("tenant", "endpoint", "key")
    replacement = tmp_path / "copy.sqlite3"
    replacement.write_bytes(path.read_bytes())
    replacement.replace(path)
    with pytest.raises(AuthorityGateError, match="replaced or aliased"):
        ledger.current_epoch("tenant")
    with pytest.raises(AuthorityGateError, match="replaced or aliased"):
        ledger.allocate_epoch("tenant", "endpoint2", "key2")
