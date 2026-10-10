"""Owner candidate for a subsequent native Turn; the result has no write authority.

The broker must authenticate and re-enumerate the complete recovery cut and
recompute strong continuation readiness at the publication transaction.
"""

from __future__ import annotations

import hashlib

from .call_acceptance_contracts import CallSubjectHead
from .contracts import Frozen
from .execution_contracts import ExecutionRunRecord, ExecutionTurn
from .execution_recovery_contracts import (
    PreparedNextExecutionTurn,
    PrepareNextExecutionTurn,
    SelectedContinuationPublication,
)
from .execution_recovery_observations import (
    ContinuationReadyRecord,
    ExecutionRecoveryCut,
    RecoverySourceRecord,
    SealedAccountingRecord,
)
from .native_recovery_profile_contracts import native_recovery_fingerprint
from .recovery_contracts import NonSchedulerFence, Present
from .recovery_domain import RecoveredClosure, model_continuation_ready, sealed_call_accounting
from .recovery_frontier_contracts import (
    CancelledBeforeAcceptResult,
    ConsequentialAcceptedCall,
    EvidenceReduction,
    InitializedCall,
    RecoveryRequiredResult,
    TerminalCallFrontier,
)
from .recovery_frontier_registry_contracts import frontier_registry_reference


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def _reference(subject_id: str, value: Frozen, *, head: str) -> CallSubjectHead:
    return CallSubjectHead(
        subject_id=subject_id,
        revision=Present(
            head=head, fingerprint=hashlib.sha256(value.canonical_bytes()).hexdigest()
        ),
    )


def _batch_fingerprint(run: ExecutionRunRecord, ready: ContinuationReadyRecord) -> str:
    return hashlib.sha256(
        b"chiplog.execution.next-turn.batch.v1\0"
        + run.canonical_bytes()
        + b"\0"
        + ready.canonical_bytes()
    ).hexdigest()


def _frontier_reference(cut: ExecutionRecoveryCut) -> CallSubjectHead:
    return _reference(
        "recovery-frontier:" + cut.frontier.run_id,
        cut.frontier,
        head="record:" + cut.frontier.digest(),
    )


def _selected_source(
    source: RecoverySourceRecord,
    record: SealedAccountingRecord | ContinuationReadyRecord,
    schema: str,
    identity: str,
    decision: CallSubjectHead,
) -> None:
    raw = record.canonical_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    _require(
        source.owner == "agent_loop"
        and source.schema_id == schema
        and source.canonical_record_bytes == raw
        and source.subject
        == CallSubjectHead(
            subject_id=identity,
            revision=Present(head="record:" + digest, fingerprint=digest),
        )
        and source.physical_record == source.subject
        and source.selected_decision == decision,
        "selected continuation source bytes, identity, physical record or decision differ",
    )


def reconstruct_prior_continuation_cut(request: PrepareNextExecutionTurn) -> ExecutionRecoveryCut:
    """Derive C from C' and the exact selected two-member publication stamp."""
    publication = request.selected_publication
    _require(publication is not None, "selected publication missing")
    assert isinstance(publication, SelectedContinuationPublication)
    stamp = publication.result_stamp
    current = request.cut
    excluded = (publication.accounting_source, publication.readiness_source)
    frontier_value = current.frontier.model_dump(mode="json")
    frontier_value["tenant_commit_sequence"] = stamp.predecessor_tenant_commit_sequence
    frontier_value.pop("fingerprint")
    frontier = current.frontier.model_copy(
        update={
            "tenant_commit_sequence": stamp.predecessor_tenant_commit_sequence,
            "fingerprint": native_recovery_fingerprint("frontier", frontier_value),
        }
    )
    old = current.model_copy(
        update={
            "tenant_commit_sequence": stamp.predecessor_tenant_commit_sequence,
            "materialization_commitment": stamp.predecessor_materialization_commitment,
            "frontier": frontier,
            "complete_sources": tuple(
                source for source in current.complete_sources if source not in excluded
            ),
            "complete_ordered_responses": tuple(
                response.model_copy(
                    update={
                        "frontier_head": _reference(
                            "recovery-frontier:" + frontier.run_id,
                            frontier,
                            head="record:" + frontier.digest(),
                        )
                    }
                )
                for response in current.complete_ordered_responses
            ),
            "complete_inventory_fingerprint": "0" * 64,
        }
    )
    cut_value = old.model_dump(mode="json")
    cut_value.pop("complete_inventory_fingerprint")
    return old.model_copy(
        update={"complete_inventory_fingerprint": native_recovery_fingerprint("cut", cut_value)}
    )


def _check_publication_bridge(request: PrepareNextExecutionTurn) -> None:
    current = request.cut
    publication = request.selected_publication
    _require(publication is not None, "next Turn lacks selected continuation publication")
    assert isinstance(publication, SelectedContinuationPublication)
    historical = publication.source_cut or reconstruct_prior_continuation_cut(request)
    ready = request.continuation
    accounting = ready.accounting
    stamp = publication.result_stamp
    _require(
        accounting.source_cut_fingerprint == historical.digest()
        and ready.source_cut_fingerprint == historical.digest()
        and publication.selected_decision == stamp.selected_decision
        and stamp.predecessor_tenant_commit_sequence == historical.tenant_commit_sequence
        and stamp.predecessor_materialization_commitment == historical.materialization_commitment
        and stamp.resulting_tenant_commit_sequence == current.tenant_commit_sequence
        and stamp.resulting_materialization_commitment == current.materialization_commitment
        and current.tenant_commit_sequence == historical.tenant_commit_sequence + 1
        and current.materialization_commitment != historical.materialization_commitment,
        "selected publication does not bridge exact predecessor and result cuts",
    )
    _selected_source(
        publication.accounting_source,
        accounting,
        "chiplog.execution.sealed-accounting.v1",
        accounting.accounting_id,
        publication.selected_decision,
    )
    _selected_source(
        publication.readiness_source,
        ready,
        "chiplog.execution.continuation-ready.v1",
        ready.readiness_id,
        publication.selected_decision,
    )
    new_sources = (publication.accounting_source, publication.readiness_source)
    _require(
        new_sources[0] != new_sources[1]
        and all(source in current.complete_sources for source in new_sources)
        and tuple(source for source in current.complete_sources if source not in new_sources)
        == historical.complete_sources
        and len(current.complete_sources) == len(historical.complete_sources) + 2
        and all(current.complete_sources.count(source) == 1 for source in new_sources)
        and not any(source in historical.complete_sources for source in new_sources),
        "selected continuation publication has missing, duplicate or unrelated source delta",
    )
    batch = hashlib.sha256(
        b"chiplog.execution.selected-continuation-publication.v1\0"
        + publication.accounting_source.canonical_bytes()
        + b"\0"
        + publication.readiness_source.canonical_bytes()
    ).hexdigest()
    _require(stamp.complete_batch_fingerprint == batch, "publication batch stamp differs")
    _require(
        current.tenant_id == historical.tenant_id
        and current.database_id == historical.database_id
        and current.current_run == historical.current_run
        and current.complete_ordered_run_lineage == historical.complete_ordered_run_lineage
        and current.current_bindings == historical.current_bindings
        and current.original_closures == historical.original_closures
        and current.current_reductions == historical.current_reductions
        and current.complete_causal_changes == historical.complete_causal_changes
        and current.complete_inventory_fingerprint != historical.complete_inventory_fingerprint
        and current.frontier.model_dump(exclude={"tenant_commit_sequence", "fingerprint"})
        == historical.frontier.model_dump(exclude={"tenant_commit_sequence", "fingerprint"})
        and current.frontier.tenant_commit_sequence == current.tenant_commit_sequence
        and historical.frontier.tenant_commit_sequence == historical.tenant_commit_sequence
        and current.frontier.fingerprint != historical.frontier.fingerprint
        and len(current.complete_ordered_responses) == len(historical.complete_ordered_responses),
        "selected publication changed prior semantic observations or frontier contents",
    )
    _require(bool(historical.complete_ordered_responses), "historical cut has no sealed response")
    for old, new in zip(
        historical.complete_ordered_responses,
        current.complete_ordered_responses,
        strict=True,
    ):
        _require(
            old.model_dump(exclude={"frontier_head"}) == new.model_dump(exclude={"frontier_head"})
            and old.frontier_head == _frontier_reference(historical)
            and new.frontier_head == _frontier_reference(current),
            "response view changed beyond current frontier reference",
        )
    old_view = historical.complete_ordered_responses[-1]
    _require(
        accounting.frontier == _frontier_reference(historical)
        and accounting.registry == frontier_registry_reference(historical.frontier.registry)
        and accounting.sealed_response == old_view.selected_seal
        and accounting.sealed_manifest_fingerprint == old_view.seal.digest()
        and accounting.complete_ordered_calls == old_view.ordered_calls
        and ready.complete_original_closures == historical.original_closures
        and ready.complete_current_reductions == historical.current_reductions,
        "historical readiness does not bind its selected source cut",
    )


def _check_source(request: PrepareNextExecutionTurn) -> ExecutionRunRecord:
    """Check necessary structural joins; this does not authenticate selection."""
    run = request.run
    _require(isinstance(run, ExecutionRunRecord), "native next Turn requires a v2 Run")
    assert isinstance(run, ExecutionRunRecord)
    cut = request.cut
    ready = request.continuation
    _check_publication_bridge(request)
    _require(
        run.head == "loop:" + run.model_copy(update={"head": "pending"}).digest(),
        "prior Run self head differs",
    )
    _require(
        run.state == "ACTIVE"
        and run.event == "ModelResponseSealed"
        and run.delivery_acceptance is None
        and run.suspension_baseline is None
        and run.root_binding == "NOT_APPLICABLE"
        and not run.original_obligations,
        "next Turn requires an active sealed non-scheduler Run",
    )
    _require(bool(run.turns), "next Turn has no immediately preceding Turn")
    _require(
        tuple(turn.ordinal for turn in run.turns) == tuple(range(1, len(run.turns) + 1))
        and tuple(turn.turn_id for turn in run.turns)
        == tuple(f"{run.run_id}/turn/{number}" for number in range(1, len(run.turns) + 1))
        and request.next_ordinal == len(run.turns) + 1,
        "Turn ordinal or original identity chain differs",
    )
    for turn in run.turns:
        _require(
            turn.head == "execution-turn:" + turn.model_copy(update={"head": "pending"}).digest()
            and turn.response_seal is not None
            and turn.initialized_calls is not None,
            "prior Turn is unsealed or has a false self head",
        )
    prior = run.turns[-1]
    _require(
        prior.state == "ACCEPTED"
        and bool(prior.attempts)
        and prior.selector == len(prior.attempts) - 1
        and prior.attempts[prior.selector].state == "TERMINAL_ACCEPTED",
        "immediately preceding Turn has no accepted sealed response",
    )
    _require(
        request.expected_current_turn == _reference(prior.turn_id, prior, head=prior.head),
        "expected current Turn is stale",
    )
    _require(
        cut.tenant_id == run.tenant
        and cut.current_run == _reference(run.run_id, run, head=run.head)
        and bool(cut.complete_ordered_run_lineage)
        and cut.complete_ordered_run_lineage[-1] == run
        and cut.frontier.tenant_id == run.tenant
        and cut.frontier.run_id == run.run_id
        and cut.frontier.tenant_commit_sequence == cut.tenant_commit_sequence,
        "recovery cut differs from latest native Run/frontier",
    )
    _require(
        len(cut.complete_ordered_responses) == len(run.turns),
        "complete response inventory differs from prior Turns",
    )
    _require(
        tuple(call for view in cut.complete_ordered_responses for call in view.ordered_calls)
        == cut.frontier.ordered_calls,
        "response call inventory differs from complete current frontier",
    )
    for turn, view in zip(run.turns, cut.complete_ordered_responses, strict=True):
        assert turn.response_seal is not None
        _require(
            view.selected_seal == turn.response_seal
            and view.selected_seal
            == _reference(
                view.seal.response_seal_id, view.seal, head="record:" + view.seal.digest()
            )
            and view.seal.original_run_id == run.run_id
            and view.seal.original_turn_id == turn.turn_id
            and view.seal.complete_ordered_initialized == turn.initialized_calls
            and tuple(call.original_call_id for call in view.ordered_calls)
            == tuple(call.subject_id for call in turn.initialized_calls or ()),
            "sealed response inventory or original call lineage differs",
        )
    view = cut.complete_ordered_responses[-1]
    _require(
        len(cut.complete_ordered_run_lineage) >= 2
        and cut.complete_ordered_run_lineage[-2].head == run.predecessor
        and isinstance(cut.complete_ordered_run_lineage[-2], ExecutionRunRecord),
        "sealed Run lacks exact captured predecessor",
    )
    captured = cut.complete_ordered_run_lineage[-2]
    assert isinstance(captured, ExecutionRunRecord)
    captured_attempt = captured.turns[-1].attempts[captured.turns[-1].selector]
    sealed_attempt = prior.attempts[prior.selector]
    _require(
        captured.event == "ModelResponseCaptured"
        and captured.turns[:-1] == run.turns[:-1]
        and captured.turns[-1].turn_id == prior.turn_id
        and captured.turns[-1].response_seal is None
        and captured.turns[-1].state == "RESPONSE_AVAILABLE"
        and captured_attempt.state == "RESPONSE_CAPTURED"
        and sealed_attempt.model_copy(
            update={"state": "RESPONSE_CAPTURED", "head": captured_attempt.head}
        ) == captured_attempt
        and view.original_run == _reference(captured.run_id, captured, head=captured.head)
        and view.original_turn
        == _reference(captured.turns[-1].turn_id, captured.turns[-1], head=captured.turns[-1].head)
        and view.seal.captured_response == view.original_run,
        "selected seal is not the immediately preceding captured response",
    )
    _require(
        request.immediately_preceding_response == view.selected_seal
        and ready.accounting.sealed_response == view.selected_seal
        and ready.accounting.sealed_manifest_fingerprint == view.seal.digest()
        and ready.accounting.registry == frontier_registry_reference(cut.frontier.registry)
        and view.frontier_head == _frontier_reference(cut)
        and tuple(ready.accounting.complete_ordered_calls) == tuple(view.ordered_calls)
        and all(isinstance(call, TerminalCallFrontier) for call in view.ordered_calls)
        and ready.complete_original_closures == cut.original_closures
        and ready.complete_current_reductions == cut.current_reductions,
        "continuation readiness is absent, stale, or differs from complete cut",
    )
    _require(
        len(view.ordered_calls) == 1
        and isinstance(view.ordered_calls[0], TerminalCallFrontier),
        "supported next Turn requires one terminal call",
    )
    terminal = view.ordered_calls[0]
    assert isinstance(terminal, TerminalCallFrontier)
    closures: tuple[RecoveredClosure, ...] = ()
    reductions: tuple[EvidenceReduction, ...] = ()
    if isinstance(terminal.disposition, CancelledBeforeAcceptResult):
        _require(
            isinstance(terminal.acceptance, InitializedCall)
            and terminal.acceptance.initialized
            == view.seal.complete_ordered_initialized[0].revision
            and terminal.disposition.initialized_predecessor
            == view.seal.complete_ordered_initialized[0].revision
            and not cut.original_closures
            and not cut.current_reductions,
            "cancelled next Turn substituted the sealed initialized revision or recovery evidence",
        )
    elif isinstance(terminal.disposition, RecoveryRequiredResult):
        _require(
            isinstance(terminal.acceptance, ConsequentialAcceptedCall)
            and terminal.acceptance.initialized
            == view.seal.complete_ordered_initialized[0].revision
            and terminal.disposition.recovered_outcome.outcome.kind == "PRESENT"
            and bool(cut.original_closures)
            and bool(cut.current_reductions),
            "accepted next Turn lacks its exact recovered closure and current reduction",
        )
        closures = tuple(
            RecoveredClosure(
                original_call_id=item.original.original_call_id,
                obligation_id=item.original.obligation_id,
                original_obligation_head=item.original.obligation_head,
                terminal_disposition=item.terminal_disposition,
                outcome=item.recovered_outcome,
                accepted_evidence=item.accepted_evidence,
                closure=item.closure,
                resolver_batch=item.resolver_batch,
                resolver_id=item.original.resolver_id,
                resolver_version=item.original.resolver_version,
                reducer_id=item.original.reducer_id,
                reducer_version=item.original.reducer_version,
                reduction_id=item.reduction_id,
                accepted_semantic_class=item.accepted_semantic_class,
            )
            for item in cut.original_closures
        )
        reductions = cut.current_reductions
    else:
        raise ValueError("next Turn requires a cancellation or recovered obligation")
    recomputed = sealed_call_accounting(
        view.seal.response_seal_id,
        view.seal.digest(),
        tuple(call.subject_id for call in view.seal.complete_ordered_initialized),
        view.ordered_calls,
    )
    model_continuation_ready(recomputed, closures, reductions)
    _require(
        recomputed.calls == ready.accounting.complete_ordered_calls,
        "current cut no longer supports selected historical accounting",
    )
    _require(
        isinstance(request.fence, NonSchedulerFence)
        and request.fence.run_id == run.run_id
        and request.fence.run_head == run.head
        and request.fence.worker_session_id == run.worker_session,
        "native next Turn fence differs from current Run",
    )
    return run


def prepare_next_native_execution_turn(
    request: PrepareNextExecutionTurn,
) -> PreparedNextExecutionTurn:
    """Produce exactly one PREPARING Turn after the selected sealed response."""
    request = PrepareNextExecutionTurn.model_validate_json(request.canonical_bytes())
    run = _check_source(request)
    turn = ExecutionTurn(
        turn_id=f"{run.run_id}/turn/{request.next_ordinal}",
        ordinal=request.next_ordinal,
        head="pending",
        state="PREPARING",
        accumulator=(),
        attempts=(),
        selector=0,
        response_seal=None,
        initialized_calls=None,
    )
    turn = turn.model_copy(update={"head": "execution-turn:" + turn.digest()})
    result = run.model_copy(
        update={
            "head": "pending",
            "predecessor": run.head,
            "turns": (*run.turns, turn),
            "event": "TurnStarted",
        }
    )
    result = result.model_copy(update={"head": "loop:" + result.digest()})
    return PreparedNextExecutionTurn(
        source_request_fingerprint=request.digest(),
        run=result,
        continuation=request.continuation,
        complete_batch_fingerprint=_batch_fingerprint(result, request.continuation),
    )


def verify_next_native_execution_turn(
    request: PrepareNextExecutionTurn, result: PreparedNextExecutionTurn
) -> None:
    """Verify a candidate without calling its producer; authority remains external."""
    request = PrepareNextExecutionTurn.model_validate_json(request.canonical_bytes())
    result = PreparedNextExecutionTurn.model_validate_json(result.canonical_bytes())
    before = _check_source(request)
    after = result.run
    _require(isinstance(after, ExecutionRunRecord), "next Turn changed native Run schema")
    assert isinstance(after, ExecutionRunRecord)
    _require(
        result.source_request_fingerprint == request.digest()
        and result.continuation == request.continuation
        and result.complete_batch_fingerprint == _batch_fingerprint(after, result.continuation),
        "next Turn proposal fingerprint or readiness differs",
    )
    _require(
        after.head == "loop:" + after.model_copy(update={"head": "pending"}).digest()
        and after.predecessor == before.head
        and after.event == "TurnStarted"
        and after.model_dump(exclude={"head", "predecessor", "event", "turns"})
        == before.model_dump(exclude={"head", "predecessor", "event", "turns"})
        and after.turns[:-1] == before.turns
        and len(after.turns) == len(before.turns) + 1,
        "next Turn proposal changed frozen Run or historical Turn fields",
    )
    turn = after.turns[-1]
    _require(
        turn.head == "execution-turn:" + turn.model_copy(update={"head": "pending"}).digest()
        and turn.turn_id == f"{before.run_id}/turn/{request.next_ordinal}"
        and turn.ordinal == request.next_ordinal
        and turn.state == "PREPARING"
        and not turn.accumulator
        and not turn.attempts
        and turn.selector == 0
        and turn.response_seal is None
        and turn.initialized_calls is None,
        "next Turn proposal is not one fresh PREPARING Turn",
    )
