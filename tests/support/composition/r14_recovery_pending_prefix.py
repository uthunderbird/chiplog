"""Shared builders extracted without changing their behavior."""

import sqlite3
import time
from types import SimpleNamespace

from tests.support.capabilities.agent_loop.execution_recovery_preparation import _cut

from chiplog.capabilities.agent_loop.call_acceptance_preparation import call_record_reference
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
    command,
)
from chiplog.platform._sqlite import SCHEMA_SQL
from chiplog.platform.authority_gate import AuthorityGate
from chiplog.platform.authority_reads import capture_authority_snapshot_commitment
from chiplog.platform.broker import BrokerSession, CallBudget, PublicPortCall, PublicPortSuccess


def _hash(database):
    with sqlite3.connect(database) as connection:
        connection.execute("BEGIN")
        return capture_authority_snapshot_commitment(connection, "tenant")


def _insert(connection, batch) -> None:
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


async def _fixture(tmp_path, complete, *, with_fence=False, prepare_prefix=None):
    database = tmp_path / "prefix.sqlite"
    with sqlite3.connect(database) as connection:
        connection.executescript(SCHEMA_SQL)
        connection.execute("INSERT INTO store_metadata VALUES(1,1)")
        connection.execute("INSERT INTO tenant_heads VALUES(?,?)", ("tenant", 1))
        if with_fence:
            connection.execute("INSERT INTO deletion_fences VALUES(?,?,?)", ("tenant", "r6", 0))
    if prepare_prefix is not None:
        prepare_prefix(database)
    predecessor = _hash(database)
    with sqlite3.connect(database) as connection:
        expected_head = connection.execute(
            "SELECT head FROM tenant_heads WHERE tenant_id='tenant'"
        ).fetchone()[0]
    original_cut = await _cut()
    frontier = original_cut.frontier.model_copy(update={"tenant_commit_sequence": expected_head})
    frontier_head = call_record_reference("recovery-frontier:" + frontier.run_id, frontier)
    cut = original_cut.model_copy(
        update={
            "materialization_commitment": predecessor,
            "database_id": str(database),
            "tenant_commit_sequence": expected_head,
            "frontier": frontier,
            "complete_ordered_responses": tuple(
                view.model_copy(update={"frontier_head": frontier_head})
                for view in original_cut.complete_ordered_responses
            ),
        }
    )
    identity = "execution-accounting-readiness:" + cut.digest()
    a = PrepareExecutionAccounting(command_id=identity, cut=cut)
    ar = prepare_execution_accounting(a)
    assert hasattr(ar, "complete_accounting"), ar
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
            for record in batch.records:
                connection.execute("DELETE FROM records WHERE record_id=?", (record.record_id,))
            connection.execute(
                "DELETE FROM publications WHERE idempotency_key=?", (batch.idempotency_key,)
            )
            connection.execute("UPDATE tenant_heads SET head=?", (batch.expected_head,))
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
