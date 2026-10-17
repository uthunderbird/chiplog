"""Real SQLite interval/commitment checks; resume authority validator is isolated."""

import hashlib
import json
from types import SimpleNamespace as NS

import pytest

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.composition import r14_execution_resume as resume
from chiplog.composition import r14_loop_semantic_reduction_publication as reductions
from chiplog.composition.r14_original_call_resolution_source import (
    _authenticate_append_evidence_suffix,
)
from chiplog.platform._sqlite import PhysicalPublicationCommand, PhysicalRecord, SQLiteMaterializer
from chiplog.platform.authority_gate import AuthorityGate
from chiplog.platform.authority_reads import capture_authority_snapshot_commitment
from chiplog.platform.workspace_snapshot import workspace_snapshot
from tests.capabilities.agent_loop.test_execution_recovery_preparation import _cut
from tests.capabilities.agent_loop.test_original_call_resolution_preparation import (
    _closed_r16,
    _source,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage",
    [
        None,
        "empty",
        "foreign-run",
        "foreign-intent",
        "invalid-resume",
        "unknown",
        "orphan",
    ],
)
async def test_evidence_interval_accepts_only_validated_same_obligation_resume(
    tmp_path,
    monkeypatch,
    damage,
):
    _, _, closed = _closed_r16()
    base_witness = _source(
        closed, record_id=closed.record.head, owner="effects", schema=closed.schema_id
    )
    run = (await _cut()).complete_ordered_run_lineage[-1]
    tenant = run.tenant
    path = tmp_path / "interval.sqlite"
    store = SQLiteMaterializer(path, record_contracts={"agent_loop": run.schema_id})
    with workspace_snapshot(path) as snapshot:
        predecessor = capture_authority_snapshot_commitment(snapshot.connection, tenant)
    raw = run.canonical_bytes()
    operation = "unknown.loop.v1" if damage == "unknown" else resume.OPERATION
    batch = PhysicalPublicationCommand(
        tenant_id=tenant,
        operation_kind=operation,
        idempotency_key="resume",
        request_fingerprint="a" * 64,
        expected_head=0,
        fence_generation="r6",
        expected_fence_frontier=0,
        minimum_fence_frontier=0,
        records=(
            PhysicalRecord(
                run.head, "agent_loop", run.schema_id, raw, hashlib.sha256(raw).hexdigest()
            ),
        ),
    )
    store._connection.execute("INSERT INTO tenant_heads VALUES (?,?)", (tenant, 1))
    store._connection.execute(
        "INSERT INTO records VALUES (?,?,?,?,?,?)",
        (
            tenant,
            run.head,
            "agent_loop",
            run.schema_id,
            raw,
            1,
        ),
    )
    if damage == "orphan":
        store._connection.execute(
            "INSERT INTO records VALUES (?,?,?,?,?,?)",
            (
                tenant,
                "orphan",
                "agent_loop",
                run.schema_id,
                b"orphan",
                1,
            ),
        )
    store._connection.execute(
        "INSERT INTO publications VALUES (?,?,?,?,?,?)",
        (
            tenant,
            operation,
            batch.idempotency_key,
            batch.request_fingerprint,
            1,
            run.head,
        ),
    )
    store._connection.commit()
    with workspace_snapshot(path) as snapshot:
        actual = capture_authority_snapshot_commitment(snapshot.connection, tenant)
    entry = dict(
        kind="DECIDED",
        operation_kind=operation,
        operation_id="resume",
        expected_head=0,
        predecessor=predecessor,
        resulting=actual,
    )
    entries = (("decision", None, json.dumps(entry).encode()),)
    owner_snapshot = NS(tenant_id=tenant, decisions=(), materialized_command_ids=())
    gate = AuthorityGate.for_database(path)
    runtime = NS(
        _database=path,
        _tenant_id=tenant,
        _authority_gate=lambda: gate,
        _check_database_identity=lambda: None,
        _pending=lambda: (),
        _commitment_journal=NS(load=lambda _: actual),
        _owner_decisions=lambda: NS(snapshot=lambda: owner_snapshot),
        _loop_decisions=lambda: NS(entries=lambda: entries),
        _publication=lambda _: batch,
    )
    original = NS(
        original_run_id="foreign" if damage == "foreign-run" else run.run_id,
        evidence_stream_id="foreign"
        if damage == "foreign-intent"
        else closed.snapshot.intent.intent_id,
    )
    evidence = NS(
        request=NS(
            run=run,
            original_suspension=NS(
                baseline=NS(
                    original_obligations=() if damage == "empty" else (original,),
                )
            ),
        )
    )
    calls = []

    def validate(actual_runtime, actual_entry, command, *, check_history):
        assert actual_runtime is runtime and actual_entry == entry and command is batch
        assert check_history is False
        calls.append("validated")
        if damage == "invalid-resume":
            raise ValueError("invalid selected resume")
        return evidence

    monkeypatch.setattr(resume, "_validate_selected", validate)
    monkeypatch.setattr(reductions, "validate_selected_witness_at_cut", lambda *a, **kw: None)
    with gate.hold():
        if damage is None:
            _authenticate_append_evidence_suffix(
                runtime,
                base_sequence=0,
                base_commitment=predecessor,
                end_sequence=1,
                end_commitment=actual,
                base_witness=base_witness,
            )
            assert calls == ["validated"]
        else:
            reason = {
                "empty": "resume changes its original cause",
                "foreign-run": "resume changes its original cause",
                "foreign-intent": "resume changes its original cause",
                "invalid-resume": "invalid selected resume",
                "unknown": "unregistered R14 record",
                "orphan": "differs from physical publication",
            }[damage]
            with pytest.raises((LoopRejected, ValueError), match=reason):
                _authenticate_append_evidence_suffix(
                    runtime,
                    base_sequence=0,
                    base_commitment=predecessor,
                    end_sequence=1,
                    end_commitment=actual,
                    base_witness=base_witness,
                )
            assert calls == ([] if damage in ("unknown", "orphan") else ["validated"])
    store._connection.close()


@pytest.mark.parametrize(
    "damage",
    [
        None,
        "adjacent",
        "at-pending",
        "anchored",
        "gap",
        "duplicate",
        "fork",
        "forged-empty",
        "foreign-tenant",
        "unknown",
        "changed-record",
        "orphan",
        "rival",
        "pending-result",
        "pending-predecessor",
        "pending-head",
        "multiple",
        "owner-bridge",
        "foreign-owner",
    ],
)
def test_historical_interval_with_later_exact_pending_readiness_control(
    tmp_path, monkeypatch, damage
):
    """Real physical/commitment boundary; semantic authority is not fabricated."""
    from dataclasses import replace

    from chiplog.platform.publication_readback import inspect_publication

    _, _, closed = _closed_r16()
    witness = _source(
        closed, record_id=closed.record.head, owner="effects", schema=closed.schema_id
    )
    tenant = closed.snapshot.intent.mandate.tenant_id
    path = tmp_path / "historical-pending.sqlite"
    schema = "chiplog.test.interval-control.v1"
    store = SQLiteMaterializer(path, record_contracts={"agent_loop": schema})

    def commitment():
        with workspace_snapshot(path) as snapshot:
            return capture_authority_snapshot_commitment(snapshot.connection, tenant)

    batches, entries, commitments = {}, [], {}
    operations = (
        "agent_loop.original-call-resolution.v1",
        "agent_loop.loop-semantic-reduction.v1",
        resume.OPERATION,
        "agent_loop.execution-accounting-readiness.v1",
    )
    try:
        for sequence, operation in enumerate(operations, 1):
            predecessor = commitment()
            raw = json.dumps({"control_sequence": sequence}).encode()
            record_id = f"control-{sequence}"
            batch = PhysicalPublicationCommand(
                tenant_id=tenant,
                operation_kind=operation,
                idempotency_key=f"selected-{sequence}",
                request_fingerprint=str(sequence) * 64,
                expected_head=sequence - 1,
                fence_generation="r6",
                expected_fence_frontier=0,
                minimum_fence_frontier=0,
                records=(
                    PhysicalRecord(
                        record_id, "agent_loop", schema, raw, hashlib.sha256(raw).hexdigest()
                    ),
                ),
            )
            store._connection.execute(
                "INSERT OR REPLACE INTO tenant_heads VALUES (?,?)", (tenant, sequence)
            )
            store._connection.execute(
                "INSERT INTO records VALUES (?,?,?,?,?,?)",
                (tenant, record_id, "agent_loop", schema, raw, sequence),
            )
            store._connection.execute(
                "INSERT INTO publications VALUES (?,?,?,?,?,?)",
                (
                    tenant,
                    operation,
                    batch.idempotency_key,
                    batch.request_fingerprint,
                    sequence,
                    record_id,
                ),
            )
            store._connection.commit()
            resulting = commitment()
            commitments[sequence] = resulting
            batches[batch.idempotency_key] = batch
            entries.append(
                dict(
                    kind="DECIDED",
                    operation_kind=operation,
                    operation_id=batch.idempotency_key,
                    expected_head=sequence - 1,
                    predecessor=predecessor,
                    resulting=resulting,
                )
            )
        pending = entries[-1]
        pending_batch = batches[pending["operation_id"]]
        assert pending["predecessor"] == commitments[3]
        assert pending["resulting"] == commitments[4]
        assert commitments[3] != commitments[4]
        with workspace_snapshot(path) as snapshot:
            assert inspect_publication(snapshot.connection, pending_batch, 4) == "COMPLETE"
        frontier = 3 if damage == "adjacent" else 4 if damage == "at-pending" else 1
        selected_commitment = commitments[frontier]
        anchored = commitments[4] if damage == "anchored" else commitments[3]
        if damage == "gap":
            entries.remove(entries[1])
        elif damage == "duplicate":
            entries.append(dict(entries[1]))
        elif damage == "fork":
            entries[2]["predecessor"] = "foreign-commitment"
        elif damage == "forged-empty":
            selected_commitment = "foreign-equal-base-and-end"
        elif damage == "foreign-tenant":
            batches["selected-2"] = replace(batches["selected-2"], tenant_id="foreign")
        elif damage == "unknown":
            batches["selected-2"] = replace(batches["selected-2"], operation_kind="unknown")
            entries[1]["operation_kind"] = "unknown"
        elif damage in {"changed-record", "orphan", "rival"}:
            if damage == "changed-record":
                store._connection.execute(
                    "UPDATE records SET canonical_bytes=? WHERE record_id=?",
                    (b"changed", "control-2"),
                )
            elif damage == "orphan":
                store._connection.execute(
                    "INSERT INTO records VALUES (?,?,?,?,?,?)",
                    (tenant, "orphan", "agent_loop", schema, b"orphan", 2),
                )
            else:
                store._connection.execute(
                    "INSERT INTO publications VALUES (?,?,?,?,?,?)",
                    (tenant, "rival", "rival", "b" * 64, 2, "control-2"),
                )
            store._connection.commit()
            # Control-only journal rebind isolates bridge physical readback from
            # the earlier whole-current-state hash check. No native HMAC claim.
            pending["resulting"] = commitment()
        elif damage == "pending-result":
            pending["resulting"] = "foreign-result"
        elif damage == "pending-predecessor":
            pending["predecessor"] = "foreign-predecessor"
        elif damage == "pending-head":
            batches["selected-4"] = replace(pending_batch, expected_head=2)
        if damage in {"owner-bridge", "foreign-owner"}:
            selected = entries.pop(1)
            owner = NS(
                tenant_id=tenant,
                decisions=(
                    NS(
                        tenant_commit_sequence=2,
                        prepared=NS(
                            request=NS(identity=NS(command_id="selected-2")),
                            predecessor_commitment=selected["predecessor"],
                        ),
                        resulting_commitment=selected["resulting"],
                    ),
                ),
                materialized_command_ids=("selected-2",),
            )
            if damage == "foreign-owner":
                batches["selected-2"] = replace(batches["selected-2"], tenant_id="foreign")
        else:
            owner = NS(tenant_id=tenant, decisions=(), materialized_command_ids=())
        captured = tuple((str(i), None, json.dumps(e).encode()) for i, e in enumerate(entries))
        gate = AuthorityGate.for_database(path)
        runtime = NS(
            _database=path,
            _tenant_id=tenant,
            _authority_gate=lambda: gate,
            _check_database_identity=lambda: None,
            _pending=lambda: (pending, pending) if damage == "multiple" else (pending,),
            _commitment_journal=NS(load=lambda _: anchored),
            _owner_decisions=lambda: NS(snapshot=lambda: owner),
            _loop_decisions=lambda: NS(entries=lambda: captured),
            _publication=lambda entry: batches[entry["operation_id"]],
            _owner_command=lambda decision: batches[decision.prepared.request.identity.command_id],
        )
        monkeypatch.setattr(reductions, "validate_selected_witness_at_cut", lambda *a, **kw: None)
        with gate.hold():

            def verify() -> None:
                _authenticate_append_evidence_suffix(
                    runtime,
                    base_sequence=frontier,
                    base_commitment=selected_commitment,
                    end_sequence=frontier,
                    end_commitment=selected_commitment,
                    base_witness=witness,
                )

            if damage in {None, "adjacent", "at-pending", "anchored", "owner-bridge"}:
                verify()
            else:
                reason = {
                    "gap": "sequence gap",
                    "duplicate": "competing selected",
                    "fork": "commitment chain forks",
                    "forged-empty": "commitment chain forks",
                    "foreign-tenant": "selected header differs",
                    "unknown": "unregistered loop",
                    "changed-record": "bridge differs from physical",
                    "orphan": "bridge differs from physical",
                    "rival": "bridge differs from physical",
                    "pending-result": "pending publication is not exact",
                    "pending-predecessor": "pending publication is not exact",
                    "pending-head": "pending publication is not exact",
                    "multiple": "unauthenticated current suffix",
                    "foreign-owner": "selected header differs",
                }[damage]
                with pytest.raises(LoopRejected, match=reason):
                    verify()
    finally:
        store._connection.close()
