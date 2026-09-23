"""Real writer exclusion against a separately opened authority-state invalidator."""

import asyncio
import hashlib
import json
import sqlite3
import sys
from multiprocessing import get_context
from pathlib import Path
from subprocess import Popen, TimeoutExpired
from typing import Literal

import pytest

from chiplog.platform._sqlite import (
    EventAppender,
    FenceAdvanceCommand,
    PhysicalPublicationCommand,
    PhysicalRecord,
    SQLiteMaterializer,
)
from chiplog.platform.authority_gate import AuthorityGate
from chiplog.platform.read_ledger import BrokerReadLedger, BrokerReadState

_WORKER = """
import sys
from pathlib import Path
from multiprocessing.connection import Connection
from chiplog.platform.authority_gate import AuthorityGate
from chiplog.platform.read_ledger import BrokerReadLedger
pipe = Connection(int(sys.argv[3]))
gate = AuthorityGate.for_database(Path(sys.argv[1]))
ledger = BrokerReadLedger(Path(sys.argv[2]), authority_gate=gate)
state = ledger.current_state('tenant')
pipe.send('ready')
assert pipe.poll(10) and pipe.recv() == 'invalidate'
pipe.send('attempting')
ledger.invalidate_credential_session('tenant', state.fingerprint(), 'new-credential')
pipe.send('changed')
pipe.close()
"""


def _state() -> BrokerReadState:
    return BrokerReadState(
        tenant_id="tenant",
        broker_epoch=1,
        credential_session_head="old-credential",
        endpoint_channel_head="endpoint",
        file_wal_observation="file",
        journal_head="journal",
        materialization_commitment="commitment",
        owner_generation="generation",
        owner_draining=False,
        principal_contour_head="principal",
        storage_mutation_generation=0,
        trust_transition_head="trust",
        authority_surface_digest="surface",
        amr_fingerprint="amr",
    )


@pytest.mark.parametrize("outcome", ["commit", "refuse", "exception", "decision_exception"])
def test_writer_excludes_process_invalidation_through_transaction_cleanup(
    tmp_path: Path,
    outcome: str,
) -> None:
    database = tmp_path / "main.sqlite3"
    ledger_path = tmp_path / "reads.sqlite3"
    gate = AuthorityGate.for_database(database)
    ledger = BrokerReadLedger(ledger_path, authority_gate=gate)
    initial = _state()
    ledger.publish_initial_state(initial)
    parent, child = get_context("spawn").Pipe()
    process = Popen(
        [sys.executable, "-c", _WORKER, str(database), str(ledger_path), str(child.fileno())],
        pass_fds=(child.fileno(),),
    )
    child.close()
    callbacks: list[str] = []

    def assert_unchanged() -> None:
        gate.require_held()
        assert not parent.poll(0.15), (
            "independent invalidator committed inside writer critical section"
        )
        # Direct independent SQLite observation avoids merely testing reentrant ledger reads.
        with sqlite3.connect(ledger_path) as connection:
            raw = connection.execute("SELECT canonical_state FROM authority_read_state").fetchone()[
                0
            ]
        assert json.loads(raw)["credential_session_head"] == "old-credential"

    def guard() -> Literal["DENIED"] | None:
        callbacks.append("guard")
        gate.require_held()
        parent.send("invalidate")
        assert parent.poll(5) and parent.recv() == "attempting"
        assert_unchanged()
        if outcome == "exception":
            raise RuntimeError("guard failure")
        return "DENIED" if outcome == "refuse" else None

    def decision(commitment: str) -> None:
        callbacks.append("decision")
        assert commitment
        assert_unchanged()
        if outcome == "decision_exception":
            raise RuntimeError("decision failure")

    async def publish() -> None:
        with SQLiteMaterializer(
            database, record_contracts={"owner": "schema"}, authority_gate=gate
        ) as store:
            async with EventAppender(store, capacity=2) as appender:
                await appender.advance_fence(FenceAdvanceCommand("tenant", "fence", 0))
                command = PhysicalPublicationCommand(
                    tenant_id="tenant",
                    operation_kind="test",
                    idempotency_key="command",
                    request_fingerprint="fingerprint",
                    expected_head=0,
                    fence_generation="fence",
                    expected_fence_frontier=0,
                    minimum_fence_frontier=0,
                    records=(
                        PhysicalRecord(
                            "record", "owner", "schema", b"{}", hashlib.sha256(b"{}").hexdigest()
                        ),
                    ),
                    admission_guard=guard,
                    decision_guard=decision,
                )
                if "exception" in outcome:
                    with pytest.raises(RuntimeError, match="failure"):
                        await appender.submit(command)
                else:
                    result = await appender.submit(command)
                    assert result.disposition == ("COMMITTED" if outcome == "commit" else "DENIED")

    try:
        assert parent.poll(5) and parent.recv() == "ready"
        asyncio.run(publish())
        assert parent.poll(5) and parent.recv() == "changed"
        process.wait(5)
        assert process.returncode == 0
        assert ledger.current_state("tenant").credential_session_head == "new-credential"
        with sqlite3.connect(database) as connection:
            expected = 1 if outcome == "commit" else 0
            assert connection.execute("SELECT COUNT(*) FROM records").fetchone()[0] == expected
            assert connection.execute("SELECT COUNT(*) FROM publications").fetchone()[0] == expected
        assert callbacks == (
            ["guard", "decision"] if outcome in {"commit", "decision_exception"} else ["guard"]
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


def test_bound_read_ledger_rejects_identical_copied_file(tmp_path: Path) -> None:
    from chiplog.platform.authority_gate import AuthorityGateError

    path = tmp_path / "reads.sqlite3"
    ledger = BrokerReadLedger(
        path, authority_gate=AuthorityGate.for_database(tmp_path / "main.sqlite3")
    )
    state = _state()
    ledger.publish_initial_state(state)
    replacement = tmp_path / "replacement.sqlite3"
    replacement.write_bytes(path.read_bytes())
    replacement.replace(path)
    with pytest.raises(AuthorityGateError, match="replaced or aliased"):
        ledger.invalidate_credential_session("tenant", state.fingerprint(), "new")
