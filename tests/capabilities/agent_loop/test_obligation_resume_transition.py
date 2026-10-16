"""Frontier-transition boundary; outer cut and suspension authentication are separate."""

import hashlib
from types import SimpleNamespace as NS

import pytest
from tests.capabilities.agent_loop.test_original_call_resolution_preparation import (
    _source,
)
from tests.capabilities.agent_loop.test_original_call_resolution_preparation import (
    request as resolution_request,
)
from tests.capabilities.agent_loop.test_original_semantic_reduction_preparation import (
    request as reduction_request,
)
from tests.capabilities.agent_loop.test_recovery_accounting import recovery_call

from chiplog.capabilities.agent_loop import execution_recovery_preparation as preparation
from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.execution_recovery_contracts import (
    AcceptedOpenTerminalDispositionV1,
)
from chiplog.capabilities.agent_loop.original_call_resolution_preparation import (
    prepare_original_call_resolution,
)
from chiplog.capabilities.agent_loop.original_semantic_reduction_preparation import (
    prepare_loop_semantic_reduction,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Absent, Present
from chiplog.capabilities.agent_loop.recovery_domain import RecoveryHold


def _ref(record, identity):
    digest = hashlib.sha256(record.canonical_bytes()).hexdigest()
    return CallSubjectHead(
        subject_id=identity, revision=Present(head="record:" + digest, fingerprint=digest)
    )


def _transition(monkeypatch, *, existing_evidence=False, damage=None):
    request = resolution_request()
    original = request.cut
    resolved = prepare_original_call_resolution(request)
    reduced = prepare_loop_semantic_reduction(reduction_request()).record
    terminal = AcceptedOpenTerminalDispositionV1.model_validate_json(
        original.call_terminal.canonical_record_bytes
    )
    outcome = _ref(resolved.recovered_outcome, resolved.recovered_outcome.outcome_id).revision
    closure_ref = _ref(resolved.closure, resolved.closure.closure_id).revision
    batch_ref = _ref(resolved.batch, resolved.batch.batch_id).revision
    reduction_ref = _ref(reduced, reduced.reduction_id).revision
    call = recovery_call()
    branch = call.acceptance.model_copy(
        update={"external_effect_intent": terminal.external_effect_intent.revision}
    )
    disposition = call.disposition.model_copy(update={
        "terminal": original.call_terminal.subject.revision,
        "obligation": original.original,
    })
    call = call.model_copy(update={
        "original_call_id": original.original.original_call_id,
        "acceptance": branch,
        "disposition": disposition,
    })
    current_call = call.model_copy(update={"disposition": disposition.model_copy(update={
        "recovered_outcome": disposition.recovered_outcome.model_copy(update={"outcome": outcome})
    })})
    closure = NS(
        original=original.original, terminal_disposition=disposition.terminal,
        recovered_outcome=outcome, closure=closure_ref, resolver_batch=batch_ref,
        reduction_id=reduced.reduction_id,
    )
    evidence = tuple(source.revision for source in reduced.complete_ordered_evidence)
    reduction = NS(
        reduction_id=reduced.reduction_id, obligation_closure=closure_ref,
        accepted_outcome=outcome, consumability="CONSUMABLE",
        ordered_consumed_evidence=evidence, current_reduction=reduction_ref,
    )
    batch_source = _source(
        resolved.batch, record_id=resolved.batch.batch_id, owner="agent_loop",
        schema="chiplog.loop.original-resolver-batch.v1",
    )
    resolver_sources = (
        original.resolver_policy, original.reducer_policy,
        original.authority.authenticated_invocation, batch_source,
    )
    authority = tuple(source.subject.revision for source in resolver_sources)
    scope = Present(head="native-scope", fingerprint="a" * 64)
    policy = Present(head="native-policy", fingerprint="b" * 64)
    semantic = Present(head="native-semantic", fingerprint="c" * 64)
    if damage == "external-intent":
        foreign_branch = branch.model_copy(update={"external_effect_intent": scope})
        call = call.model_copy(update={"acceptance": foreign_branch})
        current_call = current_call.model_copy(update={"acceptance": foreign_branch})
    before_heads = {
        "CALL": (disposition.terminal, Absent()),
        "EVIDENCE": evidence[:1] if existing_evidence else (Absent(),),
        "AUTHORITY": (scope,), "POLICY": (policy, semantic),
        "OBLIGATION": (disposition.terminal, original.current_obligation.subject.revision),
        "SEMANTIC_REDUCTION": (Absent(),), "PROMPT": (policy,),
    }
    after_heads = {
        **before_heads, "CALL": (disposition.terminal, outcome), "EVIDENCE": evidence,
        "AUTHORITY": (scope, *authority), "POLICY": (policy, semantic, *authority),
        "OBLIGATION": (disposition.terminal, outcome, closure_ref, batch_ref),
        "SEMANTIC_REDUCTION": (reduction_ref,),
    }
    if damage == "evidence-rewrite":
        before_heads["EVIDENCE"] = (scope,)
    elif damage == "evidence-mixed":
        before_heads["EVIDENCE"] = (Absent(), evidence[0])
    elif damage == "evidence-extra":
        after_heads["EVIDENCE"] = (*evidence, scope)
    elif damage == "reduction-existing":
        before_heads["SEMANTIC_REDUCTION"] = (scope,)
    elif damage == "reduction-extra":
        after_heads["SEMANTIC_REDUCTION"] = (reduction_ref, scope)
    elif damage == "authority-missing-inherited":
        after_heads["AUTHORITY"] = authority
    elif damage == "authority-extra":
        after_heads["AUTHORITY"] = (scope, *authority, policy)
    elif damage == "policy-rewrite":
        after_heads["POLICY"] = (semantic, *authority)
    elif damage == "unrelated":
        after_heads["PROMPT"] = (scope,)

    def frontier(heads, calls, sequence):
        return NS(
            tenant_commit_sequence=sequence, ordered_calls=(calls,),
            ordered_members=tuple(NS(family=k, ordered_heads=v) for k, v in heads.items()),
        )

    before = frontier(before_heads, call, 10)
    after = frontier(after_heads, current_call, 15)
    monkeypatch.setattr(preparation, "_expected_suspended_frontier", lambda *args: before)
    monkeypatch.setattr(preparation, "_run_ref", lambda _: "outer-authenticated-run")
    cut = NS(
        tenant_id=original.tenant_id, frontier=after,
        original_closures=(closure,), current_reductions=(reduction,),
        complete_sources=(
            NS(schema_id=preparation.SCHEMA_V5), original.current_obligation,
            original.call_terminal, *resolver_sources,
        ),
    )
    if damage == "opening-missing":
        cut.complete_sources = tuple(
            source for source in cut.complete_sources if source is not original.current_obligation
        )
    elif damage == "opening-duplicated":
        cut.complete_sources = (*cut.complete_sources, original.current_obligation)
    baseline = NS(
        original_obligations=(original.original,), frontier=before,
        predecessor_run="outer-authenticated-predecessor",
    )
    return cut, baseline


@pytest.mark.parametrize("existing_evidence", [False, True])
def test_obligation_resume_appends_exact_selected_closure(monkeypatch, existing_evidence):
    cut, baseline = _transition(monkeypatch, existing_evidence=existing_evidence)
    preparation._validate_obligation_resume_successor(cut, baseline, object())


def test_obligation_resume_selects_logical_terminal_among_physical_aliases(monkeypatch):
    cut, baseline = _transition(monkeypatch)
    terminal = next(
        source for source in cut.complete_sources
        if source.schema_id == "chiplog.execution.accepted-open-terminal-disposition.v1"
    )
    physical = terminal.model_copy(update={"subject": terminal.physical_record})
    assert physical.subject != terminal.subject
    cut.complete_sources = (*cut.complete_sources, physical)
    preparation._validate_obligation_resume_successor(cut, baseline, object())


def test_obligation_resume_rejects_duplicate_logical_terminal(monkeypatch):
    cut, baseline = _transition(monkeypatch)
    terminal = next(
        source for source in cut.complete_sources
        if source.schema_id == "chiplog.execution.accepted-open-terminal-disposition.v1"
    )
    cut.complete_sources = (*cut.complete_sources, terminal)
    with pytest.raises(RecoveryHold, match="lost or duplicated its selected terminal"):
        preparation._validate_obligation_resume_successor(cut, baseline, object())


@pytest.mark.parametrize("damage,reason", [
    ("evidence-rewrite", "rewrote existing EVIDENCE heads"),
    ("evidence-mixed", "rewrote existing EVIDENCE heads"),
    ("evidence-extra", "closure and consumable reduction"),
    ("reduction-existing", "unexpected reduction transition"),
    ("reduction-extra", "unexpected reduction transition"),
    ("authority-missing-inherited", "authority family has extra or missing sources"),
    ("authority-extra", "authority family has extra or missing sources"),
    ("policy-rewrite", "policy family has extra or missing sources"),
    ("unrelated", "changed unrelated frontier family PROMPT"),
    ("external-intent", "resolver invocation does not bind the exact opening and terminal"),
    ("opening-missing", "lost or duplicated its original opening"),
    ("opening-duplicated", "lost or duplicated its original opening"),
])
def test_obligation_resume_rejects_unregistered_frontier_changes(monkeypatch, damage, reason):
    cut, baseline = _transition(monkeypatch, damage=damage)
    with pytest.raises(RecoveryHold, match=reason):
        preparation._validate_obligation_resume_successor(cut, baseline, object())
