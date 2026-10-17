"""Frontier-transition boundary; outer cut and suspension authentication are separate."""

import pytest
from tests.support.capabilities.agent_loop.obligation_resume_transition import (
    _transition,
)

from chiplog.capabilities.agent_loop import execution_recovery_preparation as preparation
from chiplog.capabilities.agent_loop.recovery_domain import RecoveryHold


@pytest.mark.parametrize("existing_evidence", [False, True])
def test_obligation_resume_appends_exact_selected_closure(monkeypatch, existing_evidence):
    cut, baseline = _transition(monkeypatch, existing_evidence=existing_evidence)
    preparation._validate_obligation_resume_successor(cut, baseline, object())


def test_obligation_resume_selects_logical_terminal_among_physical_aliases(monkeypatch):
    cut, baseline = _transition(monkeypatch)
    terminal = next(
        source
        for source in cut.complete_sources
        if source.schema_id == "chiplog.execution.accepted-open-terminal-disposition.v1"
    )
    physical = terminal.model_copy(update={"subject": terminal.physical_record})
    assert physical.subject != terminal.subject
    cut.complete_sources = (*cut.complete_sources, physical)
    preparation._validate_obligation_resume_successor(cut, baseline, object())


def test_obligation_resume_rejects_duplicate_logical_terminal(monkeypatch):
    cut, baseline = _transition(monkeypatch)
    terminal = next(
        source
        for source in cut.complete_sources
        if source.schema_id == "chiplog.execution.accepted-open-terminal-disposition.v1"
    )
    cut.complete_sources = (*cut.complete_sources, terminal)
    with pytest.raises(RecoveryHold, match="lost or duplicated its selected terminal"):
        preparation._validate_obligation_resume_successor(cut, baseline, object())


@pytest.mark.parametrize(
    "damage,reason",
    [
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
    ],
)
def test_obligation_resume_rejects_unregistered_frontier_changes(monkeypatch, damage, reason):
    cut, baseline = _transition(monkeypatch, damage=damage)
    with pytest.raises(RecoveryHold, match=reason):
        preparation._validate_obligation_resume_successor(cut, baseline, object())
