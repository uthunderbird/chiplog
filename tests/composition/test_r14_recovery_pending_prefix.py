"""Exact SQLite pending delta; synthetic wire never grants complete-cut authority."""

import json
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


@pytest.mark.parametrize("damaged_result", [False, True])
@pytest.mark.parametrize("route", ["readiness", "registered", "unknown", "multiple"])
async def test_open_effects_callback_uses_exact_pending_readiness_prefix(
    tmp_path, monkeypatch, damaged_result, route
):
    """Real SQL prefix check; unrelated OPEN/effects authority is stubbed."""
    from chiplog.composition import r14_historical_suffix as suffix
    from chiplog.composition import r16_dispatch_history, r16_effects
    from chiplog.platform.workspace_snapshot import read_connection

    runtime, entry, batch, predecessor, _ = await _fixture(tmp_path, True)
    entry.update(
        kind="DECIDED",
        operation_id=batch.idempotency_key,
        expected_head=batch.expected_head,
        fingerprint=batch.request_fingerprint,
    )
    if damaged_result:
        entry["resulting"] = "0" * 64
    if route in {"registered", "unknown"}:
        entry["operation_kind"] = (
            "agent_loop.original-resolver-invocation.v1"
            if route == "registered"
            else "agent_loop.unknown.v1"
        )
    elif route == "multiple":
        runtime._pending = lambda: (entry, entry)
    runtime._loop_decisions = lambda: SimpleNamespace(
        entries=lambda: (("selected-readiness", None, json.dumps(entry).encode()),)
    )
    runtime._owner_decisions = lambda: "owner-journal-control"
    actual = _hash(runtime._database)
    original_pending = suffix.authenticate_selected_pending_r14_suffix_held
    fallback_calls = []

    def fallback(*args):
        fallback_calls.append(args)
        # A rejected exact prefix must not fall through to a broader success.
        if route == "readiness":
            return True if damaged_result else original_pending(*args)
        if route == "multiple":
            return original_pending(*args)
        return not damaged_result

    monkeypatch.setattr(suffix, "authenticate_selected_pending_r14_suffix_held", fallback)
    monkeypatch.setattr(
        suffix,
        "native_open_physical_execution_history_held",
        lambda *a, **kw: SimpleNamespace(records=()),
    )
    effects_reads = []

    def read_effects(actual_runtime, journal, *, authenticate_later_suffix, **kwargs):
        effects_reads.append(actual_runtime)
        expected = False if route == "multiple" else not damaged_result
        assert authenticate_later_suffix(actual_runtime, actual, predecessor) is expected
        return SimpleNamespace(materialization_commitment=actual), journal

    monkeypatch.setattr(r16_effects, "_read_materialized_effects_with_history", read_effects)
    monkeypatch.setattr(r16_dispatch_history, "verify_dispatch_history", lambda *a: object())
    with runtime._authority_gate().hold(), read_connection(runtime._database) as connection:
        suffix.validate_native_open_sources_held(
            runtime,
            connection,
            sources=(),
            snapshot=SimpleNamespace(tenant_head=2),
            selected=(),
            expected_ids=(),
            expected_publications=(),
            acceptances=(),
        )
    assert effects_reads == [runtime]
    assert len(fallback_calls) == (0 if route == "readiness" else 1)


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


@pytest.mark.parametrize("stale_source", ("expired_actor", "changed_cut"))
async def test_stale_final_complete_scan_prevents_decision(tmp_path, monkeypatch, stale_source):
    """Synthetic port remains untrusted; exercise refusal before any selection."""
    from chiplog.capabilities.agent_loop.contracts import LoopRejected
    from chiplog.composition import r14_recovery_publication as publication
    from tests.composition.test_r14_resume_artifacts import _evidence as resume_evidence

    runtime, entry, _batch, _predecessor, _result = await _fixture(tmp_path, False)
    original_hash = _hash(runtime._database)
    evidence = publication.retained(entry)
    closed_receipt = (await resume_evidence()).actor_budget_v1
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
        return actor, closed_receipt

    async def exchange(runtime, request, operation, result_type):
        if operation == "agent_loop.prepare_execution_accounting.v1":
            return evidence.accounting_result, evidence.accounting_call, evidence.accounting_reply
        return evidence.continuation_result, evidence.continuation_call, evidence.continuation_reply

    class UnauthenticatedSyntheticPort:
        def collect_held(self, runtime):
            clock["scans"] += 1
            if stale_source == "changed_cut" and clock["scans"] == 2:
                return cut.model_copy(update={"complete_inventory_fingerprint": "f" * 64})
            if stale_source == "expired_actor" and clock["scans"] == 2:
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
    assert clock["scans"] == 2
    assert decisions == []
    assert _hash(runtime._database) == original_hash
