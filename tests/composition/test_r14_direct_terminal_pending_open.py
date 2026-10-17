"""A complete earlier terminal survives only an authenticated pending prefix."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from chiplog.composition import r14_accepted_direct_terminal_frontier as frontier
from chiplog.composition import r14_historical_suffix as suffix


@pytest.mark.parametrize(
    "state,physical,physical_anchor,anchor,prefix,accepted,proof_calls",
    [
        ("COMPLETE", "current", "anchor", "anchor", True, True, 1),
        ("COMPLETE", "current", "anchor", "anchor", False, False, 1),
        ("ABSENT", "current", "anchor", "anchor", True, False, 0),
        ("CONFLICT", "current", "anchor", "anchor", True, False, 0),
        ("COMPLETE", "changed", "anchor", "anchor", True, False, 0),
        ("COMPLETE", "current", "changed", "anchor", True, False, 0),
        ("COMPLETE", "current", "current", "current", False, True, 0),
    ],
)
def test_complete_terminal_requires_exact_target_and_authenticated_pending_prefix(
    monkeypatch, state, physical, physical_anchor, anchor, prefix, accepted, proof_calls
):
    from chiplog.platform import authority_reads, workspace_snapshot

    held = []
    calls = []

    class Gate:
        def require_held(self) -> None:
            assert held

    gate = Gate()

    @contextmanager
    def connection(_path):
        gate.require_held()
        yield object()

    command = object()

    def selected(actual_command):
        gate.require_held()
        assert actual_command is command
        return state, physical, physical_anchor

    runtime = SimpleNamespace(
        _authority_gate=lambda: gate,
        _check_database_identity=lambda: gate.require_held(),
        _database="database",
        _tenant_id="tenant",
        _commitment_journal=SimpleNamespace(load=lambda tenant: anchor),
        _selected_physical_state=selected,
    )

    def authenticate(actual_runtime, actual, anchored):
        gate.require_held()
        assert actual_runtime is runtime
        assert (actual, anchored) == ("current", "anchor")
        calls.append(True)
        return prefix

    monkeypatch.setattr(workspace_snapshot, "read_connection", connection)
    monkeypatch.setattr(
        authority_reads, "capture_authority_snapshot_commitment", lambda *_: "current"
    )
    monkeypatch.setattr(suffix, "authenticate_selected_open_prefix_held", authenticate)
    held.append(True)
    if accepted:
        frontier.require_complete_direct_terminal_publication_held(runtime, command)
    else:
        with pytest.raises(ValueError, match="not completely materialized"):
            frontier.require_complete_direct_terminal_publication_held(runtime, command)
    assert len(calls) == proof_calls


@pytest.mark.parametrize(
    "snapshot_present,source_stamp,source_sequence,prefix,closed_commitment,accepted",
    [
        (True, "historical", 20, True, "current", True),
        (True, None, 20, True, "current", False),
        (True, "historical", None, True, "current", False),
        (True, "historical", 20, False, "current", False),
        (True, "historical", 20, True, "changed", False),
        (False, "historical", 20, True, "current", False),
    ],
)
def test_historical_builder_keeps_currentness_and_requires_pending_prefix_proof(
    monkeypatch,
    snapshot_present,
    source_stamp,
    source_sequence,
    prefix,
    closed_commitment,
    accepted,
):
    """Isolate final admission; mounted evidence supplies the real semantic DTOs."""
    from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
    from chiplog.capabilities.agent_loop.recovery_contracts import Present
    from chiplog.capabilities.agent_loop.recovery_frontier_contracts import (
        ConsequentialAcceptedCall,
    )
    from chiplog.composition import r14_native_direct_terminal_cut as builder

    ns = SimpleNamespace
    revision = Present(head="head", fingerprint="a" * 64)
    ref = CallSubjectHead(subject_id="run", revision=revision)
    run = ns(run_id="run", head="head", digest=lambda: "a" * 64, canonical_bytes=lambda: b"run")
    branch = ConsequentialAcceptedCall(
        initialized=revision,
        accepted=revision,
        execution_intent=revision,
        external_effect_intent=revision,
        complete_acceptance_manifest=(revision, revision),
    )
    outcome = ns(
        snapshot=ns(obligation=ns(state="CLOSED"), state="CONFIRMED"),
        canonical_bytes=lambda: b"outcome",
    )
    member = ns(
        record_bytes=b"outcome",
        publication_ordinal=0,
        selected_decision=ns(subject_id="decision", head="head", fingerprint="a" * 64),
        semantics=object(),
        canonical_bytes=lambda: b"member",
    )
    proposal = ns(
        accepted=ns(accepted_id="accepted", canonical_bytes=lambda: b"accepted"),
        execution_intent=ns(
            execution_intent_id="intent", external_intent=ref, canonical_bytes=lambda: b"intent"
        ),
    )
    closed = ns(
        database_identity=("database", 1, 2),
        tenant_id="tenant",
        materialization_commitment=closed_commitment,
        tenant_commit_sequence=35,
        accepted=ns(
            source=ns(
                target_run_lineage=(ref,),
                target_inventory_rows=(ns(original_call_id="call", acceptance=branch),),
            ),
            acceptance=ns(loop_proposal=proposal),
            original_call_id="call",
            selected_seal=ns(response_seal=ref),
            initialized=ref,
        ),
        latest_selected_member=member,
        latest_outcome=outcome,
        latest_outcome_record_id="outcome",
        latest_batch_record_ids=("outcome",),
        latest_publication_ordinal=0,
        latest_commit_sequence=18,
    )

    @contextmanager
    def connection(_path):
        yield object()

    runtime = ns(
        _authority_gate=lambda: ns(hold=lambda: connection(None), require_held=lambda: None),
        _check_database_identity=lambda: None,
        _database="database",
        _tenant_id="tenant",
        _database_identity=closed.database_identity,
        _commitment_journal=ns(load=lambda tenant: "anchor"),
    )
    monkeypatch.setattr(builder, "CommonCliExecutionRuntime", SimpleNamespace)
    monkeypatch.setattr(builder, "ExecutionRunRecord", SimpleNamespace)
    monkeypatch.setattr(builder, "read_connection", connection)
    monkeypatch.setattr(builder, "capture_authority_snapshot_commitment", lambda *_: "current")
    built = ns(model_copy=lambda **kw: "built-cut")
    monkeypatch.setattr(builder, "AcceptedDirectTerminalCutV1", lambda **kw: built)
    monkeypatch.setattr(builder, "_fingerprint", lambda cut: "a" * 64)
    monkeypatch.setattr(suffix, "authenticate_selected_open_prefix_held", lambda *_: prefix)
    result = builder.build_accepted_direct_terminal_cut(
        runtime,
        closed,
        source_materialization_commitment=source_stamp,
        source_tenant_commit_sequence=source_sequence,
        authenticated_snapshot=ns(records=(run,)) if snapshot_present else None,
    )
    assert result == ("built-cut" if accepted else None)
