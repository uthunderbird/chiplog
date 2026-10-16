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
