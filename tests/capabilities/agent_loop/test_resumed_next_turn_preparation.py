"""Actual suspension/resume proposals feeding the structural next-Turn owner."""

import hashlib

import pytest
from tests.capabilities.agent_loop.test_execution_next_turn_preparation import (
    _ref,
    _request,
    _seal_run,
)
from tests.capabilities.agent_loop.test_execution_recovery_preparation import _cut, _source

from chiplog.capabilities.agent_loop.call_acceptance_contracts import (
    CancelledBeforeAcceptRecord,
    InitializedCallRecord,
    NotExecutedCallResultRecord,
)
from chiplog.capabilities.agent_loop.execution_next_turn_preparation import (
    _check_publication_bridge,
    _resumed_sealed_predecessor,
    prepare_next_native_execution_turn,
    verify_next_native_execution_turn,
)
from chiplog.capabilities.agent_loop.execution_recovery_contracts import (
    PrepareExecutionResume,
    PrepareExecutionSuspension,
    SelectedContinuationPublication,
    SelectedContinuationResultStamp,
)
from chiplog.capabilities.agent_loop.execution_recovery_observations import (
    ContinuationReadyRecord,
    SealedAccountingRecord,
    SelectedExecutionSuspension,
)
from chiplog.capabilities.agent_loop.execution_recovery_preparation import (
    _expected_suspended_frontier,
    prepare_execution_resume,
    prepare_execution_suspension,
)
from chiplog.capabilities.agent_loop.native_recovery_profile_contracts import (
    native_recovery_fingerprint,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Absent, NotApplicable
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import (
    CancelledBeforeAcceptResult,
    InitializedCall,
    TerminalCallFrontier,
)
from chiplog.capabilities.agent_loop.recovery_frontier_registry_contracts import (
    frontier_registry_reference,
)


def _fingerprinted(value, kind, field):
    body = value.model_dump(mode="json")
    body.pop(field)
    return value.model_copy(update={field: native_recovery_fingerprint(kind, body)})


async def _resumed_request():
    template = await _request()
    original_cut = await _cut()
    captured = template.cut.complete_ordered_run_lineage[0]
    run = template.run
    old_init = InitializedCallRecord.model_validate_json(
        original_cut.complete_sources[3].canonical_record_bytes
    )
    original = old_init.call.original.model_copy(
        update={
            "original_run_id": run.run_id,
            "original_turn_id": run.turns[-1].turn_id,
            "captured_response": _ref(captured.run_id, captured, head=captured.head),
        }
    )
    call_id = "call:" + original.digest()
    initialized = InitializedCallRecord(
        original_call_id=call_id,
        call=old_init.call.model_copy(update={"original": original}),
        predecessor=Absent(),
    )
    init_ref = _ref(call_id, initialized)
    seal = template.cut.complete_ordered_responses[0].seal.model_copy(
        update={
            "complete_ordered_initialized": (init_ref,),
        }
    )
    seal_ref = _ref(seal.response_seal_id, seal)
    turn = run.turns[-1].model_copy(
        update={
            "head": "pending",
            "response_seal": seal_ref,
            "initialized_calls": (init_ref,),
        }
    )
    turn = turn.model_copy(update={"head": "execution-turn:" + turn.digest()})
    run = _seal_run(run.model_copy(update={"turns": (turn,)}))
    terminal = CancelledBeforeAcceptRecord(
        terminal_id="resumed-cancellation",
        source_command_id="cancel",
        original_call_id=call_id,
        original=original,
        initialized=init_ref,
        cancellation_act=init_ref,
        cut=CancelledBeforeAcceptRecord.model_validate_json(
            original_cut.complete_sources[4].canonical_record_bytes
        ).cut,
        not_executed_result_id="resumed-not-executed",
    )
    terminal_ref = _ref(terminal.terminal_id, terminal)
    result = NotExecutedCallResultRecord(
        result_id=terminal.not_executed_result_id,
        original_call_id=call_id,
        initialized=init_ref,
        terminal=terminal_ref,
        outcome="NOT_EXECUTED",
    )
    result_ref = _ref(result.result_id, result)
    call = TerminalCallFrontier(
        original_call_id=call_id,
        response_id=seal.response_seal_id,
        acceptance=InitializedCall(initialized=init_ref.revision),
        disposition=CancelledBeforeAcceptResult(
            terminal=terminal_ref.revision,
            not_executed_result=result_ref.revision,
            initialized_predecessor=init_ref.revision,
            recovered_outcome=NotApplicable(),
        ),
    )
    frontier = original_cut.frontier.model_copy(
        update={
            "ordered_calls": (call,),
            "ordered_members": (
                original_cut.frontier.ordered_members[0].model_copy(
                    update={
                        "ordered_heads": (_ref(run.run_id, run, head=run.head).revision,),
                    }
                ),
            ),
        }
    )
    view = template.cut.complete_ordered_responses[0].model_copy(
        update={
            "seal": seal,
            "selected_seal": seal_ref,
            "ordered_calls": (call,),
            "frontier_head": _ref("recovery-frontier:" + run.run_id, frontier),
        }
    )
    records = (
        (captured.run_id, captured, captured.head),
        (run.run_id, run, run.head),
        (seal.response_seal_id, seal, None),
        (call_id, initialized, None),
        (terminal.terminal_id, terminal, None),
        (result.result_id, result, None),
    )
    schemas = tuple(row.schema_id for row in original_cut.complete_sources)
    sources = tuple(
        _source(schema, _ref(identity, record, head=head), record)
        for schema, (identity, record, head) in zip(schemas, records, strict=True)
    )
    cut = original_cut.model_copy(
        update={
            "current_run": _ref(run.run_id, run, head=run.head),
            "complete_ordered_run_lineage": (captured, run),
            "frontier": frontier,
            "complete_ordered_responses": (view,),
            "complete_sources": sources,
        }
    )
    fence = template.fence.model_copy(update={"run_head": run.head})
    suspended = prepare_execution_suspension(
        PrepareExecutionSuspension(
            command_id="suspend-next-turn",
            run=run,
            cut=cut,
            activation_blocking_predicates=(),
            fence=fence,
        )
    )
    assert suspended.kind == "PREPARED_EXECUTION_SUSPENSION_V2", suspended
    suspended_ref = _ref(run.run_id, suspended.run, head=suspended.run.head)
    suspended_cut = cut.model_copy(
        update={
            "tenant_commit_sequence": cut.tenant_commit_sequence + 1,
            "current_run": suspended_ref,
            "complete_ordered_run_lineage": (*cut.complete_ordered_run_lineage, suspended.run),
            "frontier": _expected_suspended_frontier(
                frontier,
                cut.current_run,
                suspended_ref,
                cut.tenant_commit_sequence + 1,
            ),
            "complete_sources": (
                *sources[:2],
                _source(suspended.run.schema_id, suspended_ref, suspended.run),
                *sources[2:],
            ),
        }
    )
    pair_ref = _ref(
        "chiplog.execution.suspension-pair.v2:" + suspended.pair.digest(),
        suspended.pair,
        head="chiplog.execution.suspension-pair.v2:" + suspended.pair.digest(),
    )
    selected = SelectedExecutionSuspension(
        baseline=suspended.baseline,
        pair=suspended.pair,
        selected_pair=pair_ref,
        selected_decision=cut.current_run,
        canonical_pair_bytes=suspended.pair.canonical_bytes(),
    )
    resumed = prepare_execution_resume(
        PrepareExecutionResume(
            command_id=f"execution-resume:{run.run_id}:{suspended.run.head}",
            run=suspended.run,
            original_suspension=selected,
            cut=suspended_cut,
            disposition_version="r14-same-run-equality.v1",
            activation_payload=b"resume",
            fence=fence.model_copy(update={"run_head": suspended.run.head}),
        )
    )
    assert resumed.kind == "PREPARED_EXECUTION_RESUME_V2", resumed
    run = resumed.run
    run_ref = _ref(run.run_id, run, head=run.head)
    frontier = frontier.model_copy(
        update={
            "tenant_commit_sequence": 3,
            "ordered_members": (
                frontier.ordered_members[0].model_copy(
                    update={
                        "ordered_heads": (run_ref.revision,),
                    }
                ),
            ),
        }
    )
    frontier = _fingerprinted(frontier, "frontier", "fingerprint")
    view = view.model_copy(
        update={"frontier_head": _ref("recovery-frontier:" + run.run_id, frontier)}
    )
    historical = cut.model_copy(
        update={
            "current_run": run_ref,
            "tenant_commit_sequence": 3,
            "frontier": frontier,
            "complete_ordered_run_lineage": (*suspended_cut.complete_ordered_run_lineage, run),
            "complete_ordered_responses": (view,),
            "complete_sources": (
                *suspended_cut.complete_sources,
                _source(run.schema_id, run_ref, run),
                _source(
                    suspended.baseline.schema_id,
                    _ref(suspended.baseline.baseline_id, suspended.baseline),
                    suspended.baseline,
                ),
                _source(
                    suspended.pair.schema_id,
                    _ref(pair_ref.subject_id, suspended.pair),
                    suspended.pair,
                ),
            ),
        }
    )
    historical = _fingerprinted(historical, "cut", "complete_inventory_fingerprint")
    accounting = SealedAccountingRecord(
        accounting_id="resumed-accounting",
        source_cut_fingerprint=historical.digest(),
        sealed_response=seal_ref,
        sealed_manifest_fingerprint=seal.digest(),
        registry=frontier_registry_reference(frontier.registry),
        frontier=view.frontier_head,
        complete_ordered_calls=(call,),
    )
    ready = ContinuationReadyRecord(
        readiness_id="resumed-ready",
        source_cut_fingerprint=historical.digest(),
        accounting=accounting,
        complete_original_closures=(),
        complete_current_reductions=(),
    )
    decision = template.selected_publication.selected_decision
    new_sources = tuple(
        _source(schema, _ref(identity, record), record).model_copy(
            update={"selected_decision": decision}
        )
        for identity, record, schema in (
            (accounting.accounting_id, accounting, "chiplog.execution.sealed-accounting.v1"),
            (ready.readiness_id, ready, "chiplog.execution.continuation-ready.v1"),
        )
    )
    current_frontier = _fingerprinted(
        frontier.model_copy(update={"tenant_commit_sequence": 4}), "frontier", "fingerprint"
    )
    current = historical.model_copy(
        update={
            "tenant_commit_sequence": 4,
            "materialization_commitment": "e" * 64,
            "frontier": current_frontier,
            "complete_ordered_responses": (
                view.model_copy(
                    update={
                        "frontier_head": _ref("recovery-frontier:" + run.run_id, current_frontier)
                    }
                ),
            ),
            "complete_sources": (*historical.complete_sources, *new_sources),
        }
    )
    current = _fingerprinted(current, "cut", "complete_inventory_fingerprint")
    publication = SelectedContinuationPublication(
        source_cut=historical,
        accounting_source=new_sources[0],
        readiness_source=new_sources[1],
        selected_decision=decision,
        result_stamp=SelectedContinuationResultStamp(
            selected_decision=decision,
            predecessor_tenant_commit_sequence=3,
            predecessor_materialization_commitment=historical.materialization_commitment,
            resulting_tenant_commit_sequence=4,
            resulting_materialization_commitment=current.materialization_commitment,
            complete_batch_fingerprint=hashlib.sha256(
                b"chiplog.execution.selected-continuation-publication.v1\0"
                + new_sources[0].canonical_bytes()
                + b"\0"
                + new_sources[1].canonical_bytes()
            ).hexdigest(),
        ),
    )
    return template.model_copy(
        update={
            "run": run,
            "cut": current,
            "continuation": ready,
            "selected_publication": publication,
            "immediately_preceding_response": seal_ref,
            "expected_current_turn": _ref(turn.turn_id, turn, head=turn.head),
            "fence": fence.model_copy(update={"run_head": run.head}),
        }
    )


async def test_next_turn_after_owner_produced_resume_preserves_historical_baseline():
    request = await _resumed_request()
    _check_publication_bridge(request)
    assert request.run.head == "loop:" + request.run.model_copy(update={"head": "pending"}).digest()
    assert request.run.suspension_baseline is not None
    assert (
        request.run.event
        == "execution-resume:" + request.run.run_id + ":" + request.run.predecessor
    )
    result = prepare_next_native_execution_turn(request)
    assert result.run.suspension_baseline == request.run.suspension_baseline
    assert result.run.original_obligations == request.run.original_obligations
    assert result.run.turns[:-1] == request.run.turns
    assert result.run.turns[-1].ordinal == 2


async def test_resumed_next_turn_verifier_preserves_all_frozen_fields():
    request = await _resumed_request()
    result = prepare_next_native_execution_turn(request)
    verify_next_native_execution_turn(request, result)
    assert result.run.model_dump(
        exclude={"head", "predecessor", "event", "turns", "worker_session"}
    ) == request.run.model_dump(exclude={"head", "predecessor", "event", "turns", "worker_session"})


@pytest.mark.parametrize(
    "mutation",
    (
        "missing-baseline",
        "duplicate-baseline",
        "missing-pair",
        "duplicate-pair",
        "foreign-baseline",
        "foreign-pair",
        "missing-capture",
        "missing-suspension",
        "reordered-lineage",
        "extra-lineage",
        "duplicate-source",
        "foreign-original-reference",
        "changed-worker",
        "changed-turns",
        "changed-predecessor",
        "wrong-baseline-physical",
        "wrong-pair-physical",
        "baseline-original-closure-mismatch",
    ),
)
async def test_resumed_structural_proof_rejects_inexact_retained_preimages(mutation):
    request = await _resumed_request()
    cut = request.cut
    rows = cut.complete_ordered_run_lineage
    sources = cut.complete_sources
    baseline = next(
        source for source in sources if source.schema_id.endswith("suspension-baseline.v2")
    )
    pair = next(source for source in sources if source.schema_id.endswith("suspension-pair.v2"))
    update = {}
    if mutation == "baseline-original-closure-mismatch":
        from tests.capabilities.agent_loop.test_original_continuation_witness import (
            _original_cut_accounting,
        )

        recovered_cut, _ = _original_cut_accounting()
        update["original_closures"] = recovered_cut.original_closures
    elif mutation.startswith("missing-") and mutation.split("-", 1)[1] in ("baseline", "pair"):
        target = baseline if mutation == "missing-baseline" else pair
        update["complete_sources"] = tuple(source for source in sources if source != target)
    elif mutation.startswith("duplicate-"):
        target = (
            baseline
            if mutation == "duplicate-baseline"
            else pair
            if mutation == "duplicate-pair"
            else sources[0]
        )
        update["complete_sources"] = (*sources, target)
    elif mutation.startswith("foreign-") and mutation != "foreign-original-reference":
        target = baseline if mutation == "foreign-baseline" else pair
        update["complete_sources"] = tuple(
            source.model_copy(update={"owner": "foreign"}) if source == target else source
            for source in sources
        )
    elif mutation.startswith("wrong-"):
        target = baseline if mutation == "wrong-baseline-physical" else pair
        update["complete_sources"] = tuple(
            source.model_copy(update={"physical_record": sources[0].physical_record})
            if source == target
            else source
            for source in sources
        )
    elif mutation == "missing-capture":
        update["complete_ordered_run_lineage"] = rows[1:]
    elif mutation == "missing-suspension":
        update["complete_ordered_run_lineage"] = (*rows[:2], rows[-1])
    elif mutation == "reordered-lineage":
        update["complete_ordered_run_lineage"] = (rows[1], rows[0], *rows[2:])
    elif mutation == "extra-lineage":
        update["complete_ordered_run_lineage"] = (rows[0], *rows)
    elif mutation == "foreign-original-reference":
        view = cut.complete_ordered_responses[-1]
        wrong = view.original_run.model_copy(
            update={
                "revision": view.original_run.revision.model_copy(update={"fingerprint": "0" * 64})
            }
        )
        update["complete_ordered_responses"] = (view.model_copy(update={"original_run": wrong}),)
    else:
        run = request.run
        if mutation == "changed-worker":
            run = run.model_copy(update={"worker_session": "foreign-worker"})
        elif mutation == "changed-turns":
            run = run.model_copy(update={"turns": ()})
        else:
            run = run.model_copy(update={"predecessor": rows[0].head})
        run = _seal_run(run)
        request = request.model_copy(update={"run": run})
        update["complete_ordered_run_lineage"] = (*rows[:-1], run)
        update["complete_sources"] = (
            *tuple(
                source
                for source in sources
                if source.subject != _ref(request.run.run_id, rows[-1], head=rows[-1].head)
            ),
            _source(run.schema_id, _ref(run.run_id, run, head=run.head), run),
        )
    request = request.model_copy(update={"cut": cut.model_copy(update=update)})
    if mutation in ("missing-capture", "missing-suspension"):
        expected_error = "resumed Run lacks captured/sealed/suspended lineage"
    elif mutation == "extra-lineage":
        expected_error = "resumed Run lineage repeats a revision"
    elif mutation in ("reordered-lineage", "changed-predecessor"):
        expected_error = "resumed Run lineage identity, self head or adjacency differs"
    elif mutation == "duplicate-source":
        expected_error = "resumed Run lineage exact source missing or repeated"
    elif mutation == "foreign-original-reference":
        expected_error = "resumed selected capture is not the exact adjacent preimage"
    elif mutation in ("changed-worker", "changed-turns"):
        expected_error = "resume changed fields outside exact producer delta"
    elif mutation == "baseline-original-closure-mismatch":
        expected_error = "resumed frozen baseline or pair joins differ"
    elif mutation.startswith(("foreign-", "wrong-")):
        expected_error = "resumed baseline or pair canonical physical source differs"
    else:
        expected_error = "resumed baseline or pair missing or repeated"
    with pytest.raises(ValueError, match=expected_error):
        _resumed_sealed_predecessor(request)
