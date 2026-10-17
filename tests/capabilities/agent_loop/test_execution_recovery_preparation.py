"""Owner recovery proposals over an exact, bounded preaccept cancellation cut."""

import base64
from typing import cast

import pytest
from tests.support.capabilities.agent_loop.execution_recovery_preparation import (
    _cut,
    _resume_request,
)

from chiplog.capabilities.agent_loop import _execution_recovery_process as process
from chiplog.capabilities.agent_loop.execution_recovery_contracts import (
    PreparedExecutionSuspension,
    PrepareExecutionAccounting,
    PrepareExecutionContinuation,
    PrepareExecutionSuspension,
)
from chiplog.capabilities.agent_loop.execution_recovery_preparation import (
    classify_execution_resume,
    prepare_execution_accounting,
    prepare_execution_continuation,
    prepare_execution_resume,
    prepare_execution_suspension,
)
from chiplog.capabilities.agent_loop.recovery_contracts import (
    Absent,
    NonSchedulerFence,
    NotApplicable,
    OriginalObligationBinding,
)
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import (
    RecoveredOutcomeSubject,
    RecoveryRequiredResult,
    TerminalCallFrontier,
)


async def test_owner_accounting_and_continuation_are_separate_exact_proposals() -> None:
    cut = await _cut()
    accounting_request = PrepareExecutionAccounting(command_id="account", cut=cut)
    accounting = prepare_execution_accounting(accounting_request)
    assert accounting.kind == "PREPARED_EXECUTION_ACCOUNTING_V1"
    assert len(accounting.complete_accounting) == 1
    continuation_request = PrepareExecutionContinuation(
        command_id="continue", cut=cut, complete_accounting=accounting.complete_accounting
    )
    ready = prepare_execution_continuation(continuation_request)
    assert ready.kind == "PREPARED_EXECUTION_CONTINUATION_V1"
    assert ready.source_request_fingerprint == continuation_request.digest()
    assert ready.complete_continuations[0].accounting == accounting.complete_accounting[0]
    for operation, request in (
        (process.ACCOUNTING_OPERATION, accounting_request),
        (process.CONTINUATION_OPERATION, continuation_request),
    ):
        reply = process.dispatch(operation, request.canonical_bytes())
        assert (
            base64.b64decode(cast(str, reply["payload"]))
            == (
                accounting if operation == process.ACCOUNTING_OPERATION else ready
            ).canonical_bytes()
        )


async def test_owner_prepares_acyclic_non_scheduler_suspension_batch() -> None:
    cut = await _cut()
    run = cut.complete_ordered_run_lineage[-1]
    request = PrepareExecutionSuspension(
        command_id="suspend-selected-run",
        run=run,
        cut=cut,
        activation_blocking_predicates=(),
        fence=NonSchedulerFence(
            lineage=NotApplicable(),
            physical_root=NotApplicable(),
            lease=NotApplicable(),
            clock_proof=NotApplicable(),
            run_id=run.run_id,
            run_head=run.head,
            worker_session_id=run.worker_session,
            runtime_generation="test-generation",
        ),
    )

    result = prepare_execution_suspension(request)

    assert result.kind == "PREPARED_EXECUTION_SUSPENSION_V2"
    assert result.run.state == "SUSPENDED"
    assert result.run.predecessor == run.head
    assert result.run.suspension_baseline == result.pair.baseline
    assert result.pair.predecessor_run == cut.current_run
    assert result.pair.suspended_run.revision.fingerprint == result.run.digest()
    assert result.baseline.frontier == cut.frontier
    assert result.baseline.bindings == cut.current_bindings
    assert result.baseline.activation_blocking_predicates == ()


async def test_legacy_suspension_owner_route_cannot_return_an_open_obligation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cut = await _cut()
    run = cut.complete_ordered_run_lineage[-1]
    request = PrepareExecutionSuspension(
        command_id="suspend-route-version-check",
        run=run,
        cut=cut,
        activation_blocking_predicates=(),
        fence=NonSchedulerFence(
            lineage=NotApplicable(),
            physical_root=NotApplicable(),
            lease=NotApplicable(),
            clock_proof=NotApplicable(),
            run_id=run.run_id,
            run_head=run.head,
            worker_session_id=run.worker_session,
            runtime_generation="test-generation",
        ),
    )
    prepared = prepare_execution_suspension(request)
    assert isinstance(prepared, PreparedExecutionSuspension)
    obligation = OriginalObligationBinding(
        original_run_id=run.run_id,
        original_call_id="call:open",
        obligation_id="obligation:open",
        obligation_stream_id="stream:open",
        obligation_head="head:open",
        closure_predicate_id="predicate:close",
        closure_predicate_version="v1",
        resolver_id="R14_AUTHENTICATED_ORIGINAL_PRINCIPAL_RESOLVER",
        resolver_version="r14-original-resolver.v1",
        reducer_id="R14_ORIGINAL_EFFECT_EVIDENCE_REDUCER",
        reducer_version="r14-effect-evidence.v1",
        evidence_stream_id="evidence:open",
        evidence_head=Absent(),
    )
    prepared_with_obligation = prepared.model_copy(
        update={
            "baseline": prepared.baseline.model_copy(update={"original_obligations": (obligation,)})
        }
    )
    monkeypatch.setattr(
        process, "prepare_execution_suspension", lambda _request: prepared_with_obligation
    )

    legacy = process.dispatch(process.SUSPENSION_OPERATION, request.canonical_bytes())
    v37 = process.dispatch(process.SUSPENSION_OBLIGATION_OPERATION, request.canonical_bytes())

    assert legacy["failure"] == "PROTOCOL_REJECTED"
    assert "legacy suspension route" in cast(str, legacy["reason"])
    assert base64.b64decode(cast(str, v37["payload"])) == prepared_with_obligation.canonical_bytes()


async def test_first_suspension_profile_holds_with_activation_blocker() -> None:
    cut = await _cut()
    run = cut.complete_ordered_run_lineage[-1]
    request = PrepareExecutionSuspension(
        command_id="suspend-selected-run-with-blocker",
        run=run,
        cut=cut,
        activation_blocking_predicates=(cut.complete_sources[3].subject,),
        fence=NonSchedulerFence(
            lineage=NotApplicable(),
            physical_root=NotApplicable(),
            lease=NotApplicable(),
            clock_proof=NotApplicable(),
            run_id=run.run_id,
            run_head=run.head,
            worker_session_id=run.worker_session,
            runtime_generation="test-generation",
        ),
    )

    result = prepare_execution_suspension(request)

    assert result.kind == "CALL_PREPARATION_REJECTED_V1"


async def test_owner_prepares_same_run_resume_only_for_exact_suspension_frontier() -> None:
    request = await _resume_request()
    suspended = request.run
    selected_pair = request.original_suspension.selected_pair
    current_cut = request.cut
    result = prepare_execution_resume(request)

    assert result.kind == "PREPARED_EXECUTION_RESUME_V2"
    assert classify_execution_resume(request, result).disposition == "SAME_RUN"
    assert result.run.state == "ACTIVE"
    assert result.run.predecessor == suspended.head
    assert result.run.suspension_baseline == suspended.suspension_baseline
    assert result.run.turns == suspended.turns
    assert result.run.no_retry_references == suspended.no_retry_references
    assert result.original_pair == selected_pair
    reply = process.dispatch(process.RESUME_OPERATION, request.canonical_bytes())
    assert reply["schema_id"] == process.RESULT_SCHEMA
    assert base64.b64decode(cast(str, reply["payload"])) == result.canonical_bytes()

    changed = request.model_copy(
        update={"cut": current_cut.model_copy(update={"tenant_commit_sequence": 3})}
    )
    assert prepare_execution_resume(changed).kind == "CALL_PREPARATION_REJECTED_V1"
    classified_reply = process.dispatch(process.RESUME_OPERATION, changed.canonical_bytes())
    from chiplog.capabilities.agent_loop.execution_recovery_contracts import (
        ExecutionRecoveryClassified,
    )

    observation = ExecutionRecoveryClassified.model_validate_json(
        base64.b64decode(cast(str, classified_reply["payload"]))
    )
    assert observation.source_request_fingerprint == changed.digest()
    assert observation.disposition in ("RECOVERY_HOLD", "TERMINAL_RECOVERY_FAULT")
    assert observation.kind == "EXECUTION_RECOVERY_CLASSIFIED_V1"


async def test_readiness_rejects_missing_extra_and_changed_accounting() -> None:
    cut = await _cut()
    accounting = prepare_execution_accounting(PrepareExecutionAccounting(command_id="a", cut=cut))
    assert accounting.kind == "PREPARED_EXECUTION_ACCOUNTING_V1"
    row = accounting.complete_accounting[0]
    for rows in (
        (),
        (row, row),
        (row.model_copy(update={"source_cut_fingerprint": "0" * 64}),),
    ):
        result = prepare_execution_continuation(
            PrepareExecutionContinuation(command_id="c", cut=cut, complete_accounting=rows)
        )
        assert result.kind == "CALL_PREPARATION_REJECTED_V1"


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "extra",
        "duplicate",
        "reordered",
        "wrong_source",
        "physical_mismatch",
        "false_absence",
        "recovery_required",
        "forged_current_run",
        "forged_bindings",
    ],
)
async def test_owner_holds_invalid_supplied_cut(mutation: str) -> None:
    cut = await _cut()
    sources = list(cut.complete_sources)
    if mutation == "missing":
        sources.pop(3)
    elif mutation == "extra":
        sources.append(
            sources[0].model_copy(
                update={"subject": sources[0].subject.model_copy(update={"subject_id": "extra"})}
            )
        )
    elif mutation == "duplicate":
        sources.insert(3, sources[3])
    elif mutation == "reordered":
        sources[3], sources[4] = sources[4], sources[3]
    elif mutation == "wrong_source":
        sources[3] = sources[3].model_copy(update={"canonical_record_bytes": b"forged"})
    elif mutation == "physical_mismatch":
        sources[3] = sources[3].model_copy(update={"physical_record": sources[0].physical_record})
    elif mutation == "false_absence":
        cut = cut.model_copy(
            update={"frontier": cut.frontier.model_copy(update={"ordered_calls": ()})}
        )
    elif mutation == "forged_current_run":
        cut = cut.model_copy(update={"current_run": cut.complete_ordered_responses[0].original_run})
    elif mutation == "forged_bindings":
        cut = cut.model_copy(
            update={
                "current_bindings": cut.current_bindings.model_copy(
                    update={"prompt_artifact": cut.current_bindings.authority_scope}
                )
            }
        )
    elif mutation == "recovery_required":
        call = cast(TerminalCallFrontier, cut.complete_ordered_responses[0].ordered_calls[0])
        obligation = OriginalObligationBinding(
            original_run_id=cut.current_run.subject_id,
            original_call_id=call.original_call_id,
            obligation_id="obligation",
            obligation_stream_id="stream",
            obligation_head="obligation-head",
            closure_predicate_id="closure",
            closure_predicate_version="1",
            resolver_id="resolver",
            resolver_version="1",
            reducer_id="reducer",
            reducer_version="1",
            evidence_stream_id="evidence",
            evidence_head=Absent(),
        )
        changed = call.model_copy(
            update={
                "disposition": RecoveryRequiredResult(
                    terminal=call.disposition.terminal,
                    result=Absent(),
                    obligation=obligation,
                    recovered_outcome=RecoveredOutcomeSubject(
                        original_call_id=call.original_call_id,
                        recovery_obligation_id=obligation.obligation_id,
                        outcome=Absent(),
                    ),
                )
            }
        )
        view = cut.complete_ordered_responses[0].model_copy(update={"ordered_calls": (changed,)})
        cut = cut.model_copy(update={"complete_ordered_responses": (view,)})
    if mutation in {
        "missing",
        "extra",
        "duplicate",
        "reordered",
        "wrong_source",
        "physical_mismatch",
    }:
        cut = cut.model_copy(update={"complete_sources": tuple(sources)})
    result = prepare_execution_accounting(PrepareExecutionAccounting(command_id="account", cut=cut))
    assert result.kind == "CALL_PREPARATION_REJECTED_V1"
    if mutation == "extra":
        assert result.code == "HOLD"
        assert "extension is not registered" in result.reason
