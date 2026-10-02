"""Exact SQLite pending delta; synthetic wire never grants complete-cut authority."""

import sqlite3
import time
from types import SimpleNamespace

import pytest

from chiplog.capabilities.agent_loop.execution_recovery_contracts import (
    PrepareExecutionAccounting,
    PrepareExecutionContinuation,
)
from chiplog.capabilities.agent_loop.execution_recovery_preparation import (
    prepare_execution_accounting,
    prepare_execution_continuation,
)
from chiplog.capabilities.agent_loop.recovery_wire_contracts import (
    EXECUTION_RECOVERY_REQUEST_SCHEMA,
    EXECUTION_RECOVERY_RESULT_SCHEMA,
)
from chiplog.composition.r14_recovery_publication import (
    RetainedRecoveryPublication,
    authenticate_selected_recovery_prefix_held,
    command,
    source,
)
from chiplog.platform._sqlite import SCHEMA_SQL
from chiplog.platform.authority_gate import AuthorityGate
from chiplog.platform.authority_reads import capture_authority_snapshot_commitment
from chiplog.platform.broker import BrokerSession, CallBudget, PublicPortCall, PublicPortSuccess
from tests.capabilities.agent_loop.test_execution_recovery_preparation import _cut


def _hash(database):
    with sqlite3.connect(database) as connection:
        connection.execute("BEGIN")
        return capture_authority_snapshot_commitment(connection, "tenant")


def _insert(connection, batch):
    sequence = batch.expected_head + 1
    for row in batch.records:
        connection.execute(
            "INSERT INTO records VALUES(?,?,?,?,?,?)",
            ("tenant", row.record_id, row.owner, row.schema_id, row.canonical_bytes, sequence),
        )
    connection.execute(
        "INSERT INTO publications VALUES(?,?,?,?,?,?)",
        (
            "tenant",
            batch.operation_kind,
            batch.idempotency_key,
            batch.request_fingerprint,
            sequence,
            "\n".join(row.record_id for row in batch.records),
        ),
    )
    connection.execute("UPDATE tenant_heads SET head=? WHERE tenant_id=?", (sequence, "tenant"))


async def _fixture(tmp_path, complete):
    database = tmp_path / "prefix.sqlite"
    with sqlite3.connect(database) as connection:
        connection.executescript(SCHEMA_SQL)
        connection.execute("INSERT INTO store_metadata VALUES(1,1)")
        connection.execute("INSERT INTO tenant_heads VALUES(?,?)", ("tenant", 1))
    predecessor = _hash(database)
    cut = (await _cut()).model_copy(
        update={"materialization_commitment": predecessor, "database_id": str(database)}
    )
    identity = "execution-accounting-readiness:" + cut.digest()
    a = PrepareExecutionAccounting(command_id=identity, cut=cut)
    ar = prepare_execution_accounting(a)
    c = PrepareExecutionContinuation(
        command_id=identity, cut=cut, complete_accounting=ar.complete_accounting
    )
    cr = prepare_execution_continuation(c)
    callee = BrokerSession(
        tenant_id="tenant",
        broker_epoch=1,
        generation_id="generation",
        owner_id="agent_loop",
        session_id="owner",
    )
    caller = callee.model_copy(update={"owner_id": "broker", "session_id": "broker"})

    def exchange(operation, request, result):
        sent = PublicPortCall(
            operation_id=operation,
            request_id=operation,
            caller=caller,
            callee=callee,
            schema_id=EXECUTION_RECOVERY_REQUEST_SCHEMA,
            canonical_payload=request.canonical_bytes(),
            budget=CallBudget(
                remaining_calls=1,
                remaining_depth=1,
                policy_version=1,
                absolute_deadline_ns=time.monotonic_ns() + 1_000_000_000,
            ),
        )
        returned = PublicPortSuccess(
            request_id=operation,
            responder=callee,
            schema_id=EXECUTION_RECOVERY_RESULT_SCHEMA,
            canonical_payload=result.canonical_bytes(),
        )
        return sent, returned

    ac, ap = exchange("agent_loop.prepare_execution_accounting.v1", a, ar)
    cc, cp = exchange("agent_loop.prepare_execution_continuation.v1", c, cr)
    evidence = RetainedRecoveryPublication(
        accounting_request=a,
        accounting_result=ar,
        accounting_call=ac,
        accounting_reply=ap,
        continuation_request=c,
        continuation_result=cr,
        continuation_call=cc,
        continuation_reply=cp,
    )
    batch = command(evidence)
    with sqlite3.connect(database) as connection:
        _insert(connection, batch)
    resulting = _hash(database)
    if not complete:
        with sqlite3.connect(database) as connection:
            connection.execute("DELETE FROM records")
            connection.execute("DELETE FROM publications")
            connection.execute("UPDATE tenant_heads SET head=1")
    entry = {
        "operation_kind": batch.operation_kind,
        "predecessor": predecessor,
        "resulting": resulting,
        "execution_recovery_evidence": evidence.canonical_bytes().decode(),
    }
    runtime = SimpleNamespace(
        _database=database, _database_identity=(str(database), 0, 0), _tenant_id="tenant"
    )
    gate = AuthorityGate(database)
    runtime._authority_gate = lambda: gate
    runtime._check_database_identity = lambda: None
    runtime._pending = lambda: (entry,)
    runtime._pending_owners = lambda: ()
    runtime._pending_gate_publications = lambda: ()
    runtime._publication = lambda selected: batch
    runtime._commitment_journal = SimpleNamespace(load=lambda tenant: predecessor)
    return runtime, entry, batch, predecessor, resulting


@pytest.mark.parametrize("complete", (False, True))
async def test_exact_pending_delta_only_reopens_structural_prefix(tmp_path, complete):
    runtime, entry, _batch, predecessor, _resulting = await _fixture(tmp_path, complete)
    with runtime._authority_gate().hold():
        assert authenticate_selected_recovery_prefix_held(
            runtime, _hash(runtime._database), predecessor
        )
        assert not source(runtime).authenticate_selected_held(runtime, None, entry)
        assert not authenticate_selected_recovery_prefix_held(runtime, "0" * 64, predecessor)
        assert not authenticate_selected_recovery_prefix_held(
            runtime, _hash(runtime._database), "0" * 64
        )


@pytest.mark.parametrize(
    "mutation",
    (
        "extra_record",
        "extra_publication",
        "partial",
        "other_table",
        "competing_pending",
        "changed_result",
    ),
)
async def test_pending_prefix_rejects_every_extra_or_incomplete_delta(tmp_path, mutation):
    runtime, entry, batch, predecessor, _resulting = await _fixture(tmp_path, True)
    with sqlite3.connect(runtime._database) as connection:
        if mutation == "extra_record":
            connection.execute(
                "INSERT INTO records VALUES(?,?,?,?,?,?)",
                ("tenant", "extra", "agent_loop", "unknown", b"{}", 2),
            )
        elif mutation == "extra_publication":
            connection.execute(
                "INSERT INTO publications VALUES(?,?,?,?,?,?)",
                ("tenant", "extra", "extra", "0" * 64, 2, ""),
            )
        elif mutation == "partial":
            connection.execute(
                "DELETE FROM records WHERE record_id=?", (batch.records[1].record_id,)
            )
        elif mutation == "other_table":
            connection.execute("INSERT INTO deletion_fences VALUES(?,?,?)", ("tenant", "r6", 1))
        elif mutation == "competing_pending":
            runtime._pending_owners = lambda: ("rival",)
        else:
            entry["resulting"] = "0" * 64
    actual = _hash(runtime._database)
    # Matching actual to a fabricated result is insufficient: reconstruct C too.
    if mutation in ("extra_record", "extra_publication", "other_table"):
        entry["resulting"] = actual
    with runtime._authority_gate().hold():
        assert not authenticate_selected_recovery_prefix_held(runtime, actual, predecessor)


async def test_expiry_during_final_complete_scan_prevents_decision(tmp_path, monkeypatch):
    """Synthetic port remains untrusted; exercise refusal before any selection."""
    from chiplog.capabilities.agent_loop.contracts import LoopRejected
    from chiplog.composition import r14_recovery_publication as publication

    runtime, entry, _batch, _predecessor, _result = await _fixture(tmp_path, False)
    original_hash = _hash(runtime._database)
    evidence = publication.retained(entry)
    cut = evidence.accounting_request.cut
    clock = {"now": 1, "scans": 0}
    deadline = 1 + publication.RECOVERY_ACTOR_BUDGET_NS
    actor = SimpleNamespace(
        request=SimpleNamespace(budget=SimpleNamespace(absolute_deadline_ns=deadline))
    )

    async def actor_read(peer):
        return actor

    def check_actor(observed):
        if clock["now"] >= observed.request.budget.absolute_deadline_ns:
            raise LoopRejected("execution caller authentication unavailable or stale")

    async def issue(runtime, peer):
        return actor, None

    async def exchange(runtime, request, operation, result_type):
        if operation == "agent_loop.prepare_execution_accounting.v1":
            return evidence.accounting_result, evidence.accounting_call, evidence.accounting_reply
        return evidence.continuation_result, evidence.continuation_call, evidence.continuation_reply

    class UnauthenticatedSyntheticPort:
        def collect_held(self, runtime):
            clock["scans"] += 1
            if clock["scans"] == 3:
                clock["now"] = deadline
            return cut

        def authenticate_selected_held(self, *args):
            return False

    decisions = []
    runtime._full_recovery_cut_source = UnauthenticatedSyntheticPort()
    runtime._execution_actor = actor_read
    runtime._check_execution_actor = check_actor
    runtime._same_execution_actor_identity = lambda a, b: True
    runtime._pending = lambda: ()
    runtime._loop_decisions = lambda: SimpleNamespace(entries=lambda: ())
    runtime.current_worker = lambda: "worker"
    runtime._supervisor = SimpleNamespace(
        runtime=lambda: SimpleNamespace(session=lambda owner: "session")
    )
    runtime._require_no_pending = lambda: None
    runtime._append_decision = decisions.append

    async def submit(batch):
        assert batch.admission_guard() is None
        batch.decision_guard("0" * 64)
        pytest.fail("expired final scan selected a decision")

    runtime._appender = SimpleNamespace(submit=submit)
    monkeypatch.setattr(publication, "issue_execution_recovery_actor_v1", issue)
    monkeypatch.setattr(publication, "_exchange", exchange)
    with pytest.raises(LoopRejected, match="source changed at final decision"):
        await publication.publish_execution_accounting_readiness(runtime)
    assert clock["scans"] == 3
    assert decisions == []
    assert _hash(runtime._database) == original_hash
