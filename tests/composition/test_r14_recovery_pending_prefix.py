"""Exact SQLite pending delta; synthetic wire never grants complete-cut authority."""

import json
import sqlite3
from types import SimpleNamespace
from typing import TypedDict

import pytest

from chiplog.composition.r14_recovery_publication import (
    authenticate_selected_recovery_prefix_held,
    source,
)
from tests.support.composition.r14_recovery_pending_prefix import (
    _fixture,
    _hash,
)


@pytest.mark.parametrize("complete", (False, True))
async def test_exact_pending_delta_only_reopens_structural_prefix(tmp_path, complete) -> None:
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


@pytest.mark.parametrize("complete", [False, True])
@pytest.mark.parametrize("damaged_result", [False, True])
async def test_exact_history_checks_pending_readiness_prefix_before_selected_authority(
    tmp_path, monkeypatch, complete, damaged_result
) -> None:
    """Real pending SQL delta; full selected-authority validation is a sentinel."""
    from chiplog.adapters.driven.loop_sqlite import LoopIntegrityError
    from chiplog.composition import r14_loop_history as history
    from chiplog.composition import r14_native_recovery_historical as historical
    from chiplog.composition import r14_recovery_publication as publication

    class ReachedSelectedAuthority(BaseException):
        pass

    runtime, entry, batch, _, _ = await _fixture(tmp_path, complete, with_fence=True)
    entry.update(
        kind="DECIDED",
        operation_id=batch.idempotency_key,
        expected_head=batch.expected_head,
        fingerprint=batch.request_fingerprint,
    )
    if damaged_result:
        entry["resulting"] = "0" * 64
    runtime._loop_decisions = lambda: SimpleNamespace(
        entries=lambda: (
            (batch.idempotency_key, None, json.dumps(entry, sort_keys=True, separators=(",", ":"))),
        )
    )
    runtime._owner_decisions = lambda: SimpleNamespace(
        snapshot=lambda: SimpleNamespace(decisions=())
    )
    monkeypatch.setattr(historical, "_make_authenticated_loop_decision_cut", lambda *a: a[1])
    selected_calls = []

    def selected_authority(actual_runtime, selected_entry, selected_batch, **kwargs) -> None:
        runtime._authority_gate().require_held()
        assert actual_runtime is runtime and selected_entry == entry and selected_batch == batch
        selected_calls.append(selected_entry)
        raise ReachedSelectedAuthority

    monkeypatch.setattr(publication, "validate_selected", selected_authority)
    if damaged_result:
        with pytest.raises(LoopIntegrityError, match="unverified pending write"):
            history._read_call_history(runtime, selected_only=True, exact_historical_sources=True)
        assert selected_calls == []
    else:
        with pytest.raises(ReachedSelectedAuthority):
            history._read_call_history(runtime, selected_only=True, exact_historical_sources=True)
        assert selected_calls == [entry]


@pytest.mark.parametrize("damaged_result", [False, True])
@pytest.mark.parametrize("historical_locator", [False, True])
@pytest.mark.parametrize("route", ["readiness", "registered", "unknown", "multiple"])
async def test_accepted_outcome_callback_rechecks_exact_pending_readiness(
    tmp_path, monkeypatch, damaged_result, historical_locator, route
):
    """Real SQL prefix; accepted facts/effects semantics are explicit controls."""
    from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
    from chiplog.capabilities.agent_loop.recovery_contracts import Present
    from chiplog.capabilities.agent_loop.recovery_frontier_contracts import (
        ConsequentialAcceptedCall,
    )
    from chiplog.composition import r14_historical_suffix as suffix
    from chiplog.composition import r14_original_call_resolution_source as resolution
    from chiplog.composition import r16_effects
    from chiplog.composition.r14_native_selected_accounting_source import (
        R14NativeSelectedAccountingSource,
    )

    class ReachedPrefixVerdict(BaseException):
        pass

    runtime, entry, _, predecessor, actual = await _fixture(tmp_path, True)
    if route in {"registered", "unknown"}:
        entry["operation_kind"] = (
            "agent_loop.original-resolver-invocation.v1"
            if route == "registered"
            else "agent_loop.unknown.v1"
        )
    elif route == "multiple":
        runtime._pending = lambda: (entry, entry)
    journal = SimpleNamespace(decisions=())
    runtime._owner_decisions = lambda: SimpleNamespace(snapshot=lambda: journal)
    reference = Present(head="effect", fingerprint="1" * 64)
    acceptance = ConsequentialAcceptedCall(
        initialized=reference,
        accepted=reference,
        execution_intent=reference,
        external_effect_intent=reference,
        complete_acceptance_manifest=(reference, reference),
    )
    seal = SimpleNamespace(initialized_records=(SimpleNamespace(original_call_id="call"),))
    accepted = SimpleNamespace(
        original_call_id="call",
        selected_seal=seal,
        source=SimpleNamespace(
            selected_native_seals=(seal,),
            target_inventory_rows=(
                SimpleNamespace(original_call_id="call", acceptance=acceptance),
            ),
        ),
    )
    source_reader = object.__new__(R14NativeSelectedAccountingSource)
    source_reader._runtime = runtime
    fallback_calls = []

    def broader_success(*args, **kwargs):
        fallback_calls.append(True)
        return True

    monkeypatch.setattr(resolution, "authenticate_selected_resolution_prefix_held", broader_success)
    monkeypatch.setattr(suffix, "authenticate_selected_open_prefix_held", broader_success)
    monkeypatch.setattr(suffix, "authenticate_selected_pending_r14_suffix_held", broader_success)
    monkeypatch.setattr(
        suffix, "authenticate_selected_r14_native_terminal_suffix_held", broader_success
    )
    verdicts = []

    def read_effects(actual_runtime, owners, *, authenticate_later_suffix, **kwargs) -> None:
        # Repeated uses must re-read evidence; no cached success after corruption.
        for attempt in range(3):
            if damaged_result or attempt == 2:
                entry["resulting"] = "0" * 64
            verdict = authenticate_later_suffix(actual_runtime, actual, predecessor)
            verdicts.append(verdict)
            assert verdict is (route != "readiness" or (not damaged_result and attempt < 2))
        raise ReachedPrefixVerdict

    monkeypatch.setattr(r16_effects, "_read_materialized_effects_with_history", read_effects)

    class HistoricalOutcomeLocator(TypedDict, total=False):
        expected_outcome_record_id: str
        expected_selected_decision: CallSubjectHead
        expected_tenant_commit_sequence: int

    locator: HistoricalOutcomeLocator = (
        dict(
            expected_outcome_record_id="outcome",
            expected_selected_decision=CallSubjectHead(subject_id="decision", revision=reference),
            expected_tenant_commit_sequence=1,
        )
        if historical_locator
        else {}
    )
    with pytest.raises(ReachedPrefixVerdict):
        source_reader._collect_one_accepted_outcome(
            current_obligation_state="OPEN",
            accepted_facts=accepted,
            allow_authenticated_later_suffix=not historical_locator,
            **locator,
        )
    expected = [False] * 3 if damaged_result else [True, True, False]
    assert verdicts == (expected if route == "readiness" else [True] * 3)
    assert len(fallback_calls) == (0 if route == "readiness" else 3)


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
async def test_pending_prefix_rejects_every_extra_or_incomplete_delta(tmp_path, mutation) -> None:
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
    from tests.support.composition.r14_resume_artifacts import _evidence as resume_evidence

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

    def check_actor(observed) -> None:
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

    async def submit(batch) -> None:
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
