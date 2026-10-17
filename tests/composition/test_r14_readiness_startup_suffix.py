"""Pending readiness reads never replace complete selected-cut authority."""

import hashlib
import json
import sqlite3
from types import MethodType, SimpleNamespace

import pytest

from chiplog.platform._sqlite import PhysicalPublicationCommand, PhysicalRecord
from tests.support.composition.r14_recovery_pending_prefix import _fixture, _hash, _insert


def _entry(batch, predecessor, resulting, **evidence):
    return dict(
        version=1,
        kind="DECIDED",
        operation_kind=batch.operation_kind,
        operation_id=batch.idempotency_key,
        expected_head=batch.expected_head,
        fingerprint=batch.request_fingerprint,
        predecessor=predecessor,
        resulting=resulting,
        **evidence,
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
    from tests.support.capabilities.agent_loop.execution_recovery_preparation import _cut
    from tests.support.composition.r14_loop_semantic_reduction_publication import retained
    from tests.support.composition.r14_original_call_resolution_publication import (
        publication_evidence,
    )

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

    def prefix(database) -> None:
        nonlocal resume_evidence
        # The initial committed prefix is an explicit control, not native authority.
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE tenant_heads SET head=?", (resolution_batch.expected_head,))
        add(database, resolution_batch)
        source = template.request.model_copy(
            update={
                "database_id": str(database),
                "materialization_commitment": _hash(database),
            }
        )
        prepared = prepare_loop_semantic_reduction(source)
        evidence = template.model_copy(
            update={
                "database_id": str(database),
                "request": source,
                "prepared": prepared,
                "predecessor": source.materialization_commitment,
                "owner_call": template.owner_call.model_copy(
                    update={"canonical_payload": source.canonical_bytes()}
                ),
                "owner_reply": template.owner_reply.model_copy(
                    update={"canonical_payload": prepared.canonical_bytes()}
                ),
            }
        )
        batch = reduction.command(evidence)
        add(database, batch, **{reduction.EVIDENCE_FIELD: evidence.canonical_bytes().decode()})
        if bridge:
            raw = run.canonical_bytes()
            batch = PhysicalPublicationCommand(
                tenant_id="tenant",
                operation_kind=resume.OPERATION,
                idempotency_key="controlled-resume",
                request_fingerprint="a" * 64,
                expected_head=batch.expected_head + 1,
                fence_generation="r6",
                expected_fence_frontier=0,
                minimum_fence_frontier=0,
                records=(
                    PhysicalRecord(
                        run.head,
                        "agent_loop",
                        run.schema_id,
                        raw,
                        hashlib.sha256(raw).hexdigest(),
                    ),
                ),
            )
            add(database, batch)
            witness = DispatchOutcomeRecordV2.model_validate_json(
                source.complete_anchor_sources[-1].canonical_record_bytes
            )
            original = SimpleNamespace(
                original_run_id=run.run_id,
                evidence_stream_id=witness.snapshot.intent.intent_id,
            )
            resume_evidence = SimpleNamespace(
                request=SimpleNamespace(
                    run=run,
                    original_suspension=SimpleNamespace(
                        baseline=SimpleNamespace(original_obligations=(original,))
                    ),
                )
            )

    runtime, pending, ready_batch, predecessor, _ = await _fixture(
        tmp_path, complete, prepare_prefix=prefix
    )
    pending.update(_entry(ready_batch, pending["predecessor"], pending["resulting"]))
    entries.append(pending)
    batches[ready_batch.idempotency_key] = ready_batch

    def journal_entries():
        # Persistence/authentication is controlled; marker sequencing is real.
        values = []
        for entry in entries:
            values.append(entry)
            if entry is not pending:
                values.append(
                    dict(version=1, kind="MATERIALIZED", operation_id=entry["operation_id"])
                )
        return tuple(
            (str(index), None, json.dumps(value, sort_keys=True, separators=(",", ":")))
            for index, value in enumerate(values)
        )

    runtime._loop_decisions = lambda: SimpleNamespace(entries=journal_entries)
    owner_snapshot = SimpleNamespace(tenant_id="tenant", decisions=(), materialized_command_ids=())
    runtime._owner_decisions = lambda: SimpleNamespace(snapshot=lambda: owner_snapshot)
    runtime._publication = lambda entry: batches[entry["operation_id"]]
    from chiplog.composition.r13_runtime import R13Runtime
    from chiplog.composition.r14_runtime import R14PlanningRuntime

    for name in ("_pending", "_loop_decision_record", "_validate_h1_v2_durable_decision"):
        setattr(runtime, name, MethodType(getattr(R13Runtime, name), runtime))
    for name in ("_loop_decision_materialized", "_selected_physical_state"):
        setattr(runtime, name, MethodType(getattr(R14PlanningRuntime, name), runtime))
    # Only these unrelated authority acquisitions are controlled. Reduction command,
    # interval chain, exact readiness prefix, physical catalog and SQL hashes are real.
    monkeypatch.setattr(
        resolution,
        "_selected_entries",
        lambda _: (("resolution", "{}", entries[0], resolution_batch),),
    )
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
        if complete:
            from chiplog.platform.owner_decision_journal import OwnerJournalIntegrityError

            selected = reduction._selected_entries(runtime)[0]
            # Recovery/writer lookup keeps its stricter anchored-cut contract.
            with pytest.raises(OwnerJournalIntegrityError, match="recover_history_physical"):
                runtime._loop_decision_materialized(selected[3].idempotency_key, selected[2])
        reduction.validate_loop_semantic_reduction_startup(runtime)
        assert calls[-1]["end_sequence"] == runtime._publication(pending).expected_head
        assert calls[-1]["end_commitment"] == predecessor
        # Synthetic native sources cannot borrow structural prefix permission.
        assert recovery.authenticate_selected_recovery_prefix_held(
            runtime, _hash(runtime._database), predecessor
        )
        with pytest.raises(ValueError, match="unauthenticated"):
            recovery.validate_selected(runtime, pending, runtime._publication(pending))
        assert (
            runtime._database.read_bytes(),
            runtime._loop_decisions().entries(),
            runtime._commitment_journal.load("tenant"),
        ) == before


@pytest.mark.parametrize("physical", ["ABSENT", "COMPLETE"])
@pytest.mark.parametrize("materialized", [False, True])
async def test_semantic_startup_keeps_own_publication_crash_windows(
    tmp_path, monkeypatch, physical: str, materialized
) -> None:
    from chiplog.composition import r14_loop_semantic_reduction_publication as reduction

    runtime, pending, _ = await _semantic_prefix(
        tmp_path, monkeypatch, complete=False, bridge=False
    )
    selected = reduction._selected_entries(runtime)[0]
    entry, batch = selected[2:]
    values = [json.loads(raw) for _, _, raw in runtime._loop_decisions().entries()]
    values = [value for value in values if value["operation_id"] != pending["operation_id"]]
    if not materialized:
        values = [
            value
            for value in values
            if not (
                value["operation_id"] == batch.idempotency_key and value["kind"] == "MATERIALIZED"
            )
        ]
    runtime._loop_decisions = lambda: SimpleNamespace(
        entries=lambda: tuple(
            (str(index), None, json.dumps(value, sort_keys=True, separators=(",", ":")))
            for index, value in enumerate(values)
        )
    )
    if physical == "ABSENT":
        with sqlite3.connect(runtime._database) as connection:
            for record in batch.records:
                connection.execute("DELETE FROM records WHERE record_id=?", (record.record_id,))
            connection.execute(
                "DELETE FROM publications WHERE idempotency_key=?", (batch.idempotency_key,)
            )
            connection.execute("UPDATE tenant_heads SET head=?", (batch.expected_head,))
    anchor = entry["resulting"] if materialized else entry["predecessor"]
    runtime._commitment_journal = SimpleNamespace(load=lambda _: anchor)
    with runtime._authority_gate().hold():
        before = (runtime._database.read_bytes(), runtime._loop_decisions().entries(), anchor)
        if physical == "ABSENT" and materialized:
            with pytest.raises(ValueError, match="unauthenticated current suffix"):
                reduction.validate_loop_semantic_reduction_startup(runtime)
        else:
            reduction.validate_loop_semantic_reduction_startup(runtime)
        assert (
            runtime._database.read_bytes(),
            runtime._loop_decisions().entries(),
            runtime._commitment_journal.load("tenant"),
        ) == before


@pytest.mark.parametrize("damage", ["orphan", "duplicate", "unknown-kind", "changed-exact"])
async def test_semantic_startup_keeps_exact_marker_history(tmp_path, monkeypatch, damage: str):
    from chiplog.capabilities.agent_loop.contracts import LoopRejected
    from chiplog.composition import r14_loop_semantic_reduction_publication as reduction

    runtime, _, _ = await _semantic_prefix(tmp_path, monkeypatch, complete=True, bridge=True)
    values = [json.loads(raw) for _, _, raw in runtime._loop_decisions().entries()]
    marker = next(value for value in values if value["kind"] == "MATERIALIZED")
    if damage == "orphan":
        marker["operation_id"] = "missing-decision"
    elif damage == "duplicate":
        values.insert(values.index(marker) + 1, marker.copy())
    elif damage == "unknown-kind":
        marker["kind"] = "UNKNOWN"
    else:
        exact_lookup = runtime._loop_decision_record

        def changed_lookup(identity, expected):
            # Mutate only the journal after selection; expected remains the old entry.
            selected = next(
                value
                for value in values
                if value["kind"] == "DECIDED" and value["operation_id"] == identity
            )
            selected["fingerprint"] = "0" * 64
            return exact_lookup(identity, expected)

        runtime._loop_decision_record = changed_lookup
    runtime._loop_decisions = lambda: SimpleNamespace(
        entries=lambda: tuple(
            (str(index), None, json.dumps(value, sort_keys=True, separators=(",", ":")))
            for index, value in enumerate(values)
        )
    )
    with runtime._authority_gate().hold():
        sql_before = runtime._database.read_bytes()
        anchor = runtime._commitment_journal.load("tenant")
        with pytest.raises(
            LoopRejected,
            match=(
                "missing or changed exact"
                if damage == "changed-exact"
                else "orphaned loop materialization"
                if damage in {"orphan", "duplicate"}
                else "unknown loop publication disposition"
            ),
        ):
            reduction.validate_loop_semantic_reduction_startup(runtime)
        assert runtime._database.read_bytes() == sql_before
        assert runtime._commitment_journal.load("tenant") == anchor


@pytest.mark.parametrize(
    "damage",
    [
        "old-row",
        "readiness-row",
        "anchor",
        "predecessor",
        "result",
        "multiple",
        "owner",
        "gate",
    ],
)
async def test_semantic_readiness_rejection_preserves_sql_journal_and_anchor(
    tmp_path, monkeypatch, damage: str
) -> None:
    from chiplog.capabilities.agent_loop.contracts import LoopRejected
    from chiplog.composition import r14_loop_semantic_reduction_publication as reduction

    runtime, pending, _ = await _semantic_prefix(tmp_path, monkeypatch, complete=True, bridge=True)
    if damage in {"old-row", "readiness-row"}:
        batch = (
            reduction._selected_entries(runtime)[0][3]
            if damage == "old-row"
            else runtime._publication(pending)
        )
        with sqlite3.connect(runtime._database) as connection:
            connection.execute(
                "UPDATE records SET canonical_bytes=? WHERE record_id=?",
                (b"changed", batch.records[0].record_id),
            )
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
        before = (
            runtime._database.read_bytes(),
            runtime._loop_decisions().entries(),
            runtime._commitment_journal.load("tenant"),
        )
        with pytest.raises((ValueError, LoopRejected)):
            reduction.validate_loop_semantic_reduction_startup(runtime)
            proposed_recovery.append(True)
        assert proposed_recovery == []
        assert (
            runtime._database.read_bytes(),
            runtime._loop_decisions().entries(),
            runtime._commitment_journal.load("tenant"),
        ) == before


async def test_real_startup_rejects_unauthenticated_readiness_before_recovery(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real startup/semantic/recovery validators; other family authority is controlled."""
    from importlib import import_module

    from chiplog.composition.r14_runtime import R14PlanningRuntime

    runtime, _, _ = await _semantic_prefix(tmp_path, monkeypatch, complete=True, bridge=True)
    controls = {
        "r14_loop_history": (
            "validate_selected_h1_completions",
            "validate_selected_delivery_work_genesis",
            "validate_selected_cancellations",
            "validate_selected_executions",
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

    async def recover(*args, **kwargs) -> None:
        recovery_calls.append(True)

    runtime._recover_exact = recover
    runtime._recover_pending = recover
    runtime._finish_decision = lambda *a, **kw: recovery_calls.append(True)
    with runtime._authority_gate().hold():
        before = (
            runtime._database.read_bytes(),
            runtime._loop_decisions().entries(),
            runtime._commitment_journal.load("tenant"),
        )
    with pytest.raises(ValueError, match="unauthenticated"):
        await R14PlanningRuntime._prepare_startup(runtime)
    assert recovery_calls == []
    assert (
        runtime._database.read_bytes(),
        runtime._loop_decisions().entries(),
        runtime._commitment_journal.load("tenant"),
    ) == before


@pytest.mark.parametrize("family", ["policy", "invocation", "resolution"])
@pytest.mark.parametrize(
    "route",
    [
        "readiness",
        "other",
        "unknown",
        "multiple",
        "owner",
        "gate",
        "bad-prefix",
        "bad-generic",
        "anchored",
    ],
)
async def test_old_startup_complete_requires_readiness_before_unanchored_suffix_proof(
    tmp_path, monkeypatch, family: str, route: str
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
    batch = PhysicalPublicationCommand(
        tenant_id=runtime._tenant_id,
        operation_kind=module.OPERATION,
        idempotency_key="old-selected",
        request_fingerprint="0" * 64,
        expected_head=0,
        fence_generation="r6",
        expected_fence_frontier=0,
        minimum_fence_frontier=0,
        records=(),
    )
    runtime._loop_decisions = lambda: SimpleNamespace(
        entries=lambda: (
            (
                "materialized",
                None,
                json.dumps({"kind": "MATERIALIZED", "operation_id": "old-selected"}),
            ),
        )
    )
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
