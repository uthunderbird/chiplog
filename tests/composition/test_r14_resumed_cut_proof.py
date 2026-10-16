"""Retained-resume eligibility contract; owner authentication is a stubbed port."""

import json
from types import SimpleNamespace as NS

import pytest

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.recovery_contracts import Absent, Present
from chiplog.composition import r14_execution_resume as resume
from chiplog.composition.r14_native_recovery_cut import NativeRecoveryCutSource
from tests.capabilities.agent_loop.test_execution_recovery_preparation import _cut
from tests.capabilities.agent_loop.test_obligation_resume_transition import _transition


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage",
    (
        None,
        "missing-selection",
        "foreign-result",
        "duplicate-selection",
        "baseline",
        "obligations",
        "blockers",
        "accepted-missing",
        "closure-missing",
        "closure-foreign",
        "closure-terminal",
        "closure-outcome",
        "outcome-absent",
        "reduction-missing",
        "reduction-id",
        "reduction-closure",
        "reduction-outcome",
        "reduction-consumability",
        "validated-result",
    ),
)
async def test_selected_resume_proof_requires_exact_closed_original(monkeypatch, damage):
    typed_cut = await _cut()
    cut, baseline = _transition(monkeypatch)
    baseline.activation_blocking_predicates = ()
    baseline_ref = CallSubjectHead(
        subject_id="baseline", revision=Present(head="baseline", fingerprint="a" * 64)
    )
    foreign_ref = Present(head="foreign", fingerprint="f" * 64)
    run = typed_cut.complete_ordered_run_lineage[-1].model_copy(
        update={
            "state": "ACTIVE",
            "suspension_baseline": baseline_ref,
            "original_obligations": baseline.original_obligations,
            "no_retry_references": (),
            "event": "execution-resume:run:suspended",
        }
    )
    suspension = NS(pair=NS(baseline=baseline_ref), baseline=baseline)
    evidence = NS(request=NS(original_suspension=suspension), result=NS(run=run))
    call = cut.frontier.ordered_calls[0]
    closure = NS(**vars(cut.original_closures[0]))
    reduction = NS(**vars(cut.current_reductions[0]))
    entries = [{"kind": "DECIDED", "operation_kind": resume.OPERATION, "id": "selected"}]
    if damage == "missing-selection":
        entries = []
    elif damage == "foreign-result":
        evidence.result.run = run.model_copy(update={"head": "foreign"})
    elif damage == "duplicate-selection":
        entries.append({**entries[0], "id": "duplicate"})
    elif damage == "baseline":
        suspension.pair.baseline = baseline_ref.model_copy(update={"subject_id": "foreign"})
    elif damage == "obligations":
        baseline.original_obligations = ()
    elif damage == "blockers":
        baseline.activation_blocking_predicates = ("blocked",)
    elif damage == "closure-missing":
        closure = None
    elif damage == "closure-foreign":
        closure.original = closure.original.model_copy(update={"original_run_id": "foreign"})
    elif damage == "closure-terminal":
        closure.terminal_disposition = foreign_ref
    elif damage == "closure-outcome":
        closure.recovered_outcome = foreign_ref
    elif damage == "outcome-absent":
        disposition = call.disposition.model_copy(
            update={
                "recovered_outcome": call.disposition.recovered_outcome.model_copy(
                    update={
                        "outcome": Absent(),
                    }
                ),
            }
        )
        call = call.model_copy(update={"disposition": disposition})
    elif damage == "reduction-missing":
        reduction = None
    elif damage == "reduction-id":
        reduction.reduction_id = "foreign"
    elif damage == "reduction-closure":
        reduction.obligation_closure = foreign_ref
    elif damage == "reduction-outcome":
        reduction.accepted_outcome = foreign_ref
    elif damage == "reduction-consumability":
        reduction.consumability = "NOT_CONSUMABLE"

    command = object()
    publications = []
    validations = []

    def publication(entry):
        assert entry in entries
        publications.append(entry)
        return command

    runtime = NS(
        _loop_decisions=lambda: NS(
            entries=lambda: tuple(
                (entry["id"], None, json.dumps(entry).encode()) for entry in entries
            )
        ),
        _publication=publication,
    )

    def retained(entry):
        assert entry in entries
        return evidence

    def validate(actual_runtime, entry, actual_command, *, check_history):
        assert actual_runtime is runtime
        assert entry in entries
        assert actual_command is command
        assert check_history is False
        validations.append(entry)
        if damage == "validated-result":
            return NS(
                request=evidence.request, result=NS(run=run.model_copy(update={"head": "foreign"}))
            )
        return evidence

    monkeypatch.setattr(resume, "_retained", retained)
    monkeypatch.setattr(resume, "_validate_selected", validate)
    accepted = None if damage == "accepted-missing" else (call, closure, reduction, ())
    selected = NativeRecoveryCutSource._selected_resume_for_run(runtime, run, accepted)
    if damage is None:
        assert selected is evidence
        assert validations == entries
        assert publications == entries
    else:
        assert selected is None
    if damage in ("baseline", "obligations", "blockers", "validated-result"):
        assert validations == entries
