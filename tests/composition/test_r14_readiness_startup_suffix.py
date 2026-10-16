"""Pending readiness reads never replace complete selected-cut authority."""

import hashlib
import json
import sqlite3
from types import SimpleNamespace

import pytest

from chiplog.platform._sqlite import PhysicalPublicationCommand, PhysicalRecord
from tests.composition.test_r14_recovery_pending_prefix import _fixture, _hash, _insert


def _entry(batch, predecessor, resulting, **evidence):
    return dict(
        kind="DECIDED", operation_kind=batch.operation_kind,
        operation_id=batch.idempotency_key, expected_head=batch.expected_head,
        fingerprint=batch.request_fingerprint, predecessor=predecessor,
        resulting=resulting, **evidence,
    )


async def _semantic_prefix(tmp_path, monkeypatch, *, complete, bridge):
    """Real reduction/readiness producers and SQL; resolution/witness/resume auth controlled."""
    from chiplog.capabilities.agent_loop.original_semantic_reduction_preparation import (
        prepare_loop_semantic_reduction,
    )
    from chiplog.capabilities.effects.dispatch_outcome_contracts import DispatchOutcomeRecordV2
    from chiplog.composition import r14_execution_resume as resume
    from chiplog.composition import r14_loop_semantic_reduction_publication as reduction
    from chiplog.composition import r14_original_call_resolution_publication as resolution
    from tests.capabilities.agent_loop.test_execution_recovery_preparation import _cut
    from tests.composition.test_r14_loop_semantic_reduction_publication import retained
    from tests.composition.test_r14_original_call_resolution_publication import publication_evidence

    resolved = publication_evidence()
    resolution_batch = resolution.command(resolved)
    template = retained()
    run = (await _cut()).complete_ordered_run_lineage[-1]
    entries, batches = [], {}
    resume_evidence = None

    def add(database, batch, **evidence):
        predecessor = _hash(database)
        with sqlite3.connect(database) as connection:
            _insert(connection, batch)
        selected = _entry(batch, predecessor, _hash(database), **evidence)
        entries.append(selected)
        batches[batch.idempotency_key] = batch
        return selected

    def prefix(database):
        nonlocal resume_evidence
        # The initial committed prefix is an explicit control, not native authority.
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE tenant_heads SET head=?", (resolution_batch.expected_head,))
        add(database, resolution_batch)
        source = template.request.model_copy(update={
            "database_id": str(database), "materialization_commitment": _hash(database),
        })
        prepared = prepare_loop_semantic_reduction(source)
        evidence = template.model_copy(update={
            "database_id": str(database), "request": source, "prepared": prepared,
            "predecessor": source.materialization_commitment,
            "owner_call": template.owner_call.model_copy(
                update={"canonical_payload": source.canonical_bytes()}
            ),
            "owner_reply": template.owner_reply.model_copy(
                update={"canonical_payload": prepared.canonical_bytes()}
            ),
        })
        batch = reduction.command(evidence)
        add(database, batch, **{reduction.EVIDENCE_FIELD: evidence.canonical_bytes().decode()})
        if bridge:
            raw = run.canonical_bytes()
            batch = PhysicalPublicationCommand(
                tenant_id="tenant", operation_kind=resume.OPERATION,
                idempotency_key="controlled-resume", request_fingerprint="a" * 64,
                expected_head=batch.expected_head + 1, fence_generation="r6",
                expected_fence_frontier=0, minimum_fence_frontier=0,
                records=(PhysicalRecord(
                    run.head, "agent_loop", run.schema_id, raw, hashlib.sha256(raw).hexdigest(),
                ),),
            )
            add(database, batch)
            witness = DispatchOutcomeRecordV2.model_validate_json(
                source.complete_anchor_sources[-1].canonical_record_bytes
            )
            original = SimpleNamespace(
                original_run_id=run.run_id,
                evidence_stream_id=witness.snapshot.intent.intent_id,
            )
            resume_evidence = SimpleNamespace(request=SimpleNamespace(
                run=run, original_suspension=SimpleNamespace(
                    baseline=SimpleNamespace(original_obligations=(original,))
                ),
            ))

    runtime, pending, ready_batch, predecessor, _ = await _fixture(
        tmp_path, complete, prepare_prefix=prefix
    )
    pending.update(_entry(ready_batch, pending["predecessor"], pending["resulting"]))
    entries.append(pending)
    batches[ready_batch.idempotency_key] = ready_batch
    runtime._loop_decisions = lambda: SimpleNamespace(entries=lambda: tuple(
        (entry["operation_id"], None, json.dumps(entry, sort_keys=True, separators=(",", ":")))
        for entry in entries
    ))
    owner_snapshot = SimpleNamespace(tenant_id="tenant", decisions=(), materialized_command_ids=())
    runtime._owner_decisions = lambda: SimpleNamespace(snapshot=lambda: owner_snapshot)
    runtime._publication = lambda entry: batches[entry["operation_id"]]
    runtime._loop_decision_materialized = lambda identity, entry: entry is not pending
    # Only these unrelated authority acquisitions are controlled. Reduction command,
    # interval chain, exact readiness prefix, physical catalog and SQL hashes are real.
    monkeypatch.setattr(resolution, "_selected_entries", lambda _: (
        ("resolution", "{}", entries[0], resolution_batch),
    ))
    monkeypatch.setattr(resolution, "validate_selected_command", lambda *a: resolved)
    monkeypatch.setattr(reduction, "validate_selected_witness_at_cut", lambda *a, **kw: None)

    def validate_resume(actual_runtime, entry, command, *, check_history):
        assert actual_runtime is runtime and command is batches["controlled-resume"]
        assert entry["operation_kind"] == resume.OPERATION and check_history is False
        return resume_evidence

    monkeypatch.setattr(resume, "_validate_selected", validate_resume)
    return runtime, pending, predecessor


@pytest.mark.parametrize("complete", [False, True])
@pytest.mark.parametrize("bridge", [False, True])
async def test_semantic_startup_proves_only_through_pending_readiness_predecessor(
    tmp_path, monkeypatch, complete, bridge
):
    from chiplog.composition import r14_loop_semantic_reduction_publication as reduction
    from chiplog.composition import r14_original_call_resolution_source as intervals
    from chiplog.composition import r14_recovery_publication as recovery

    runtime, pending, predecessor = await _semantic_prefix(
        tmp_path, monkeypatch, complete=complete, bridge=bridge
    )
    real_interval = intervals._authenticate_append_evidence_suffix
    calls = []

    def interval(actual_runtime, **kwargs):
        calls.append(kwargs)
        return real_interval(actual_runtime, **kwargs)

    monkeypatch.setattr(intervals, "_authenticate_append_evidence_suffix", interval)
    with runtime._authority_gate().hold():
        before = (runtime._database.read_bytes(), runtime._loop_decisions().entries(), predecessor)
        reduction.validate_loop_semantic_reduction_startup(runtime)
        assert calls[-1]["end_sequence"] == runtime._publication(pending).expected_head
        assert calls[-1]["end_commitment"] == predecessor
        # Synthetic native sources cannot borrow structural prefix permission.
        assert recovery.authenticate_selected_recovery_prefix_held(
            runtime, _hash(runtime._database), predecessor
        )
        with pytest.raises(ValueError, match="unauthenticated"):
            recovery.validate_selected(runtime, pending, runtime._publication(pending))
        assert (runtime._database.read_bytes(), runtime._loop_decisions().entries(),
                runtime._commitment_journal.load("tenant")) == before


@pytest.mark.parametrize("damage", [
    "old-row", "readiness-row", "anchor", "predecessor", "result", "multiple", "owner", "gate",
])
async def test_semantic_readiness_rejection_preserves_sql_journal_and_anchor(
    tmp_path, monkeypatch, damage
):
    from chiplog.capabilities.agent_loop.contracts import LoopRejected
    from chiplog.composition import r14_loop_semantic_reduction_publication as reduction

    runtime, pending, _ = await _semantic_prefix(tmp_path, monkeypatch, complete=True, bridge=True)
    if damage in {"old-row", "readiness-row"}:
        batch = (
            reduction._selected_entries(runtime)[0][3]
            if damage == "old-row" else runtime._publication(pending)
        )
        with sqlite3.connect(runtime._database) as connection:
            connection.execute("UPDATE records SET canonical_bytes=? WHERE record_id=?",
                               (b"changed", batch.records[0].record_id))
    elif damage == "anchor":
        runtime._commitment_journal = SimpleNamespace(load=lambda _: "0" * 64)
    elif damage in {"predecessor", "result"}:
        pending["predecessor" if damage == "predecessor" else "resulting"] = "0" * 64
    elif damage == "multiple":
        runtime._pending = lambda: (pending, pending)
    elif damage == "owner":
        runtime._pending_owners = lambda: (object(),)
    else:
        runtime._pending_gate_publications = lambda: (object(),)
    proposed_recovery = []
    with runtime._authority_gate().hold():
        before = (runtime._database.read_bytes(), runtime._loop_decisions().entries(),
                  runtime._commitment_journal.load("tenant"))
        with pytest.raises((ValueError, LoopRejected)):
            reduction.validate_loop_semantic_reduction_startup(runtime)
            proposed_recovery.append(True)
        assert proposed_recovery == []
        assert (runtime._database.read_bytes(), runtime._loop_decisions().entries(),
                runtime._commitment_journal.load("tenant")) == before


async def test_real_startup_rejects_unauthenticated_readiness_before_recovery(
    tmp_path, monkeypatch
):
    """Real startup/semantic/recovery validators; other family authority is controlled."""
    from importlib import import_module

    from chiplog.composition.r14_runtime import R14PlanningRuntime

    runtime, _, _ = await _semantic_prefix(tmp_path, monkeypatch, complete=True, bridge=True)
    controls = {
        "r14_loop_history": (
            "validate_selected_h1_completions", "validate_selected_delivery_work_genesis",
            "validate_selected_cancellations", "validate_selected_executions",
            "validate_selected_native_accounting",
        ),
        "r14_accepted_open_accounting_publication": (
            "validate_selected_accepted_open_accounting",
            "validate_selected_accepted_open_recovery",
        ),
        "r14_accepted_direct_terminal_publication": ("validate_selected_accepted_direct_terminal",),
        "r14_recovery_profile_publication": ("validate_profile_startup",),
        "r14_original_resolver_policy_publication": ("validate_policy_startup",),
        "r14_original_resolver_invocation_publication": ("validate_invocation_startup",),
        "r14_original_call_resolution_publication": ("validate_original_call_resolution_startup",),
        "r16_denial_history": ("validate_selected_denials",),
        "r17_ingress_history": ("validate_selected_ingress",),
    }
    for module_name, names in controls.items():
        module = import_module("chiplog.composition." + module_name)
        for name in names:
            monkeypatch.setattr(module, name, lambda *a, **kw: None)
    recovery_calls = []

    async def recover(*args, **kwargs):
        recovery_calls.append(True)

    runtime._recover_exact = recover
    runtime._recover_pending = recover
    runtime._finish_decision = lambda *a, **kw: recovery_calls.append(True)
    with runtime._authority_gate().hold():
        before = (runtime._database.read_bytes(), runtime._loop_decisions().entries(),
                  runtime._commitment_journal.load("tenant"))
    with pytest.raises(ValueError, match="unauthenticated"):
        await R14PlanningRuntime._prepare_startup(runtime)
    assert recovery_calls == []
    assert (runtime._database.read_bytes(), runtime._loop_decisions().entries(),
            runtime._commitment_journal.load("tenant")) == before


@pytest.mark.parametrize("family", ["policy", "invocation", "resolution"])
@pytest.mark.parametrize("route", [
    "readiness", "other", "unknown", "multiple", "owner", "gate",
    "bad-prefix", "bad-generic", "anchored",
])
async def test_old_startup_complete_requires_readiness_before_unanchored_suffix_proof(
    tmp_path, monkeypatch, family, route
):
    """Call-site routing only; old-family catalog/envelope authority is controlled."""
    from chiplog.composition import r14_historical_suffix as suffix
    from chiplog.composition import r14_original_call_resolution_publication as resolution
    from chiplog.composition import r14_original_resolver_invocation_publication as invocation
    from chiplog.composition import r14_original_resolver_policy_publication as policy

    runtime, pending, _, anchor, _ = await _fixture(tmp_path, True)
    if route in {"other", "unknown"}:
        pending["operation_kind"] = (
            "agent_loop.execution-next-turn.v1" if route == "other" else "agent_loop.unknown.v1"
        )
    elif route == "multiple":
        runtime._pending = lambda: (pending, pending)
    elif route == "owner":
        runtime._pending_owners = lambda: (object(),)
    elif route == "gate":
        runtime._pending_gate_publications = lambda: (object(),)
    elif route == "bad-prefix":
        pending["resulting"] = "0" * 64
    elif route == "anchored":
        anchor = _hash(runtime._database)
        runtime._commitment_journal = SimpleNamespace(load=lambda _: anchor)
        runtime._pending = lambda: pytest.fail("anchored route must not acquire pending readiness")
    module, validator = {
        "policy": (policy, policy._validate_startup_state),
        "invocation": (invocation, invocation._startup_state),
        "resolution": (resolution, resolution._startup_state),
    }[family]
    batch = SimpleNamespace(expected_head=0, idempotency_key="old-selected")
    runtime._loop_decisions = lambda: SimpleNamespace(entries=lambda: (
        ("materialized", None,
         json.dumps({"kind": "MATERIALIZED", "operation_id": "old-selected"})),
    ))
    monkeypatch.setattr(module, "inspect_publication", lambda *args: "COMPLETE")
    catalogs, generic_calls = [], []
    monkeypatch.setattr(module, "_validate_physical_catalog", lambda *a: catalogs.append(a))

    def generic(actual_runtime, actual, anchored, *, target_commitment):
        assert actual_runtime is runtime and actual == _hash(runtime._database)
        assert anchored == anchor and target_commitment == "1" * 64
        generic_calls.append(True)
        return route != "bad-generic"

    monkeypatch.setattr(suffix, "authenticate_selected_r14_suffix_held", generic)
    with runtime._authority_gate().hold():
        if route in {"readiness", "anchored"}:
            validator(runtime, {"predecessor": "old", "resulting": "1" * 64}, batch)
            assert len(catalogs) == len(generic_calls) == 1
        else:
            with pytest.raises(ValueError, match="physical state"):
                validator(runtime, {"predecessor": "old", "resulting": "1" * 64}, batch)
            assert catalogs == []
            assert len(generic_calls) == int(route == "bad-generic")
