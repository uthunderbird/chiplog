"""Structural owner checks only; these fixtures do not authenticate a recovery cut."""

import hashlib

import pytest
from tests.support.execution_fan_out import fixture

from chiplog.capabilities.agent_loop.call_acceptance_contracts import (
    CallSubjectHead,
    SealedResponseRecord,
)
from chiplog.capabilities.agent_loop.contracts import Frozen
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.capabilities.agent_loop.execution_next_turn_preparation import (
    _batch_fingerprint,
    prepare_next_native_execution_turn,
    reconstruct_prior_continuation_cut,
    verify_next_native_execution_turn,
)
from chiplog.capabilities.agent_loop.execution_recovery_contracts import (
    PrepareNextExecutionTurn,
    SelectedContinuationPublication,
    SelectedContinuationResultStamp,
)
from chiplog.capabilities.agent_loop.execution_recovery_observations import (
    ContinuationReadyRecord,
    ExecutionRecoveryCut,
    RecoverySourceRecord,
    SealedAccountingRecord,
    SealedResponseRecoveryView,
)
from chiplog.capabilities.agent_loop.native_recovery_profile_contracts import (
    native_recovery_fingerprint,
)
from chiplog.capabilities.agent_loop.recovery_contracts import NotApplicable, Present
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import (
    CancelledBeforeAcceptResult,
    FrontierMember,
    FrozenRunBindings,
    InitializedCall,
    RecoveryFrontier,
    TerminalCallFrontier,
)
from chiplog.capabilities.agent_loop.recovery_frontier_registry_contracts import (
    execution_native_one_call_cancellation_frontier_registry_v1,
    frontier_registry_reference,
)


def _ref(subject: str, value: Frozen, *, head: str | None = None) -> CallSubjectHead:
    raw = value.canonical_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    return CallSubjectHead(
        subject_id=subject,
        revision=Present(head=head or "record:" + digest, fingerprint=digest),
    )


def _seal_run(run: ExecutionRunRecord) -> ExecutionRunRecord:
    return run.model_copy(
        update={"head": "loop:" + run.model_copy(update={"head": "pending"}).digest()}
    )


async def _request(
    *, substituted_initialized: Present | None = None, mismatched_frontier_call: bool = False
) -> PrepareNextExecutionTurn:
    """Build a coherent inert shape, not a selected or physical-source witness."""
    fanout = await fixture()
    captured = fanout.captured_run
    capture_turn = captured.turns[-1]
    capture_turn = capture_turn.model_copy(update={"turn_id": "run/turn/1", "head": "pending"})
    capture_turn = capture_turn.model_copy(
        update={"head": "execution-turn:" + capture_turn.digest()}
    )
    captured = _seal_run(captured.model_copy(update={"turns": (capture_turn,), "head": "pending"}))
    original = fanout.request.ordered_calls[-1].original
    call_id = "call:" + original.digest()
    initialized = CallSubjectHead(
        subject_id=call_id,
        revision=Present(head="record:initialized", fingerprint="a" * 64),
    )
    seal = SealedResponseRecord(
        response_seal_id="seal:run/turn/1",
        tenant_id=captured.tenant,
        original_run_id=captured.run_id,
        original_turn_id=capture_turn.turn_id,
        captured_response=_ref(captured.run_id, captured, head=captured.head),
        complete_ordered_initialized=(initialized,),
        bound=fanout.request.bound,
    )
    seal_ref = _ref(seal.response_seal_id, seal)
    prior = capture_turn.model_copy(
        update={
            "head": "pending",
            "state": "ACCEPTED",
            "attempts": (
                capture_turn.attempts[0].model_copy(
                    update={"state": "TERMINAL_ACCEPTED", "head": "pending"}
                ),
            ),
            "response_seal": seal_ref,
            "initialized_calls": (initialized,),
        }
    )
    accepted_attempt = prior.attempts[0]
    accepted_attempt = accepted_attempt.model_copy(
        update={"head": "execution-attempt:" + accepted_attempt.digest()}
    )
    prior = prior.model_copy(update={"attempts": (accepted_attempt,)})
    prior = prior.model_copy(update={"head": "execution-turn:" + prior.digest()})
    sealed = _seal_run(
        captured.model_copy(
            update={
                "head": "pending",
                "predecessor": captured.head,
                "turns": (prior,),
                "event": "ModelResponseSealed",
            }
        )
    )
    terminal = TerminalCallFrontier(
        original_call_id=call_id,
        response_id=seal.response_seal_id,
        acceptance=InitializedCall(initialized=substituted_initialized or initialized.revision),
        disposition=CancelledBeforeAcceptResult(
            terminal=Present(head="terminal", fingerprint="b" * 64),
            not_executed_result=Present(head="result", fingerprint="c" * 64),
            initialized_predecessor=substituted_initialized or initialized.revision,
            recovered_outcome=NotApplicable(),
        ),
    )
    registry = execution_native_one_call_cancellation_frontier_registry_v1()
    frontier = RecoveryFrontier(
        tenant_id=sealed.tenant,
        run_id=sealed.run_id,
        tenant_commit_sequence=10,
        registry=registry,
        ordered_members=(
            FrontierMember(
                family="RUN",
                subject=sealed.run_id,
                branch="ACTIVE",
                ordered_heads=(_ref(sealed.run_id, sealed, head=sealed.head).revision,),
                fingerprint="d" * 64,
            ),
        ),
        ordered_calls=(
            terminal.model_copy(update={"original_call_id": "different-call"})
            if mismatched_frontier_call
            else terminal,
        ),
        canonicalization_version="chiplog.recovery.frontier.v1",
        fingerprint="e" * 64,
    )
    frontier_value = frontier.model_dump(mode="json")
    frontier_value.pop("fingerprint")
    frontier = frontier.model_copy(
        update={"fingerprint": native_recovery_fingerprint("frontier", frontier_value)}
    )
    frontier_ref = _ref("recovery-frontier:" + sealed.run_id, frontier)
    view = SealedResponseRecoveryView(
        original_run=_ref(captured.run_id, captured, head=captured.head),
        original_turn=_ref(capture_turn.turn_id, capture_turn, head=capture_turn.head),
        selected_seal=seal_ref,
        seal=seal,
        ordered_calls=(terminal,),
        frontier_head=frontier_ref,
    )
    cut = ExecutionRecoveryCut(
        tenant_id=sealed.tenant,
        database_id="database",
        tenant_commit_sequence=10,
        materialization_commitment="f" * 64,
        current_run=_ref(sealed.run_id, sealed, head=sealed.head),
        complete_ordered_run_lineage=(captured, sealed),
        complete_ordered_responses=(view,),
        frontier=frontier,
        current_bindings=FrozenRunBindings(
            objective="Plan",
            requested_work="Plan",
            prompt_artifact=Present(head="prompt", fingerprint="a" * 64),
            ordered_tool_specs=(),
            generated_schema=Present(head="schema", fingerprint="a" * 64),
            semantic_bindings=(),
            recipient_effect_bindings=(),
            authority_scope=Present(head="scope", fingerprint="a" * 64),
            authority_mandate_heads=(),
            policy=Present(head="policy", fingerprint="a" * 64),
            no_retry_boundaries=(),
        ),
        original_closures=(),
        current_reductions=(),
        complete_sources=(
            RecoverySourceRecord(
                owner="agent_loop",
                subject=_ref(sealed.run_id, sealed, head=sealed.head),
                schema_id=sealed.schema_id,
                canonical_record_bytes=sealed.canonical_bytes(),
                selected_decision=CallSubjectHead(
                    subject_id="decision",
                    revision=Present(head="decision", fingerprint="a" * 64),
                ),
                physical_record=_ref(sealed.run_id, sealed, head=sealed.head),
            ),
        ),
        complete_causal_changes=(),
        complete_inventory_fingerprint="0" * 64,
    )
    cut_value = cut.model_dump(mode="json")
    cut_value.pop("complete_inventory_fingerprint")
    cut = cut.model_copy(
        update={"complete_inventory_fingerprint": native_recovery_fingerprint("cut", cut_value)}
    )
    accounting = SealedAccountingRecord(
        accounting_id="accounting",
        source_cut_fingerprint=cut.digest(),
        sealed_response=seal_ref,
        sealed_manifest_fingerprint=seal.digest(),
        registry=frontier_registry_reference(registry),
        frontier=frontier_ref,
        complete_ordered_calls=(terminal,),
    )
    ready = ContinuationReadyRecord(
        readiness_id="ready",
        source_cut_fingerprint=cut.digest(),
        accounting=accounting,
        complete_original_closures=(),
        complete_current_reductions=(),
    )
    decision = CallSubjectHead(
        subject_id="decision:continuation",
        revision=Present(head="decision:continuation", fingerprint="2" * 64),
    )

    def selected_source(record: Frozen, identity: str, schema: str) -> RecoverySourceRecord:
        reference = _ref(identity, record)
        return RecoverySourceRecord(
            owner="agent_loop",
            subject=reference,
            schema_id=schema,
            canonical_record_bytes=record.canonical_bytes(),
            selected_decision=decision,
            physical_record=reference,
        )

    accounting_source = selected_source(
        accounting, accounting.accounting_id, "chiplog.execution.sealed-accounting.v1"
    )
    readiness_source = selected_source(
        ready, ready.readiness_id, "chiplog.execution.continuation-ready.v1"
    )
    next_frontier_value = frontier.model_dump(mode="json")
    next_frontier_value["tenant_commit_sequence"] = 11
    next_frontier_value.pop("fingerprint")
    next_frontier = frontier.model_copy(
        update={
            "tenant_commit_sequence": 11,
            "fingerprint": native_recovery_fingerprint("frontier", next_frontier_value),
        }
    )
    next_view = view.model_copy(
        update={"frontier_head": _ref("recovery-frontier:" + sealed.run_id, next_frontier)}
    )
    current = cut.model_copy(
        update={
            "tenant_commit_sequence": 11,
            "materialization_commitment": "4" * 64,
            "frontier": next_frontier,
            "complete_ordered_responses": (next_view,),
            "complete_sources": (*cut.complete_sources, accounting_source, readiness_source),
            "complete_inventory_fingerprint": "0" * 64,
        }
    )
    current_value = current.model_dump(mode="json")
    current_value.pop("complete_inventory_fingerprint")
    current = current.model_copy(
        update={"complete_inventory_fingerprint": native_recovery_fingerprint("cut", current_value)}
    )
    batch = hashlib.sha256(
        b"chiplog.execution.selected-continuation-publication.v1\0"
        + accounting_source.canonical_bytes()
        + b"\0"
        + readiness_source.canonical_bytes()
    ).hexdigest()
    publication = SelectedContinuationPublication(
        source_cut=cut,
        accounting_source=accounting_source,
        readiness_source=readiness_source,
        selected_decision=decision,
        result_stamp=SelectedContinuationResultStamp(
            selected_decision=decision,
            predecessor_tenant_commit_sequence=10,
            predecessor_materialization_commitment=cut.materialization_commitment,
            resulting_tenant_commit_sequence=11,
            resulting_materialization_commitment=current.materialization_commitment,
            complete_batch_fingerprint=batch,
        ),
    )
    fence = fanout.request.cut.fence.model_copy(
        update={
            "run_id": sealed.run_id,
            "run_head": sealed.head,
            "worker_session_id": sealed.worker_session,
        }
    )
    return PrepareNextExecutionTurn(
        command_id="next:run:2",
        run=sealed,
        cut=current,
        immediately_preceding_response=seal_ref,
        expected_current_turn=_ref(prior.turn_id, prior, head=prior.head),
        next_ordinal=2,
        continuation=ready,
        selected_publication=publication,
        fence=fence,
    )


async def test_next_turn_preserves_sealed_original_call_lineage() -> None:
    request = await _request()
    publication = request.selected_publication
    assert publication is not None
    assert request.cut.tenant_commit_sequence == publication.source_cut.tenant_commit_sequence + 1
    assert request.cut.frontier != publication.source_cut.frontier
    result = prepare_next_native_execution_turn(request)
    verify_next_native_execution_turn(request, result)
    assert (
        result.continuation.canonical_bytes() == publication.readiness_source.canonical_record_bytes
    )
    assert result.run.predecessor == request.run.head
    assert result.run.turns[:-1] == request.run.turns
    assert result.run.turns[-1].turn_id == "run/turn/2"
    assert result.run.turns[-1].state == "PREPARING"


async def test_next_turn_compact_publication_reconstructs_exact_historical_cut() -> None:
    request = await _request()
    publication = request.selected_publication
    assert publication is not None and publication.source_cut is not None
    compact = request.model_copy(
        update={"selected_publication": publication.model_copy(update={"source_cut": None})}
    )
    assert reconstruct_prior_continuation_cut(compact) == publication.source_cut
    result = prepare_next_native_execution_turn(compact)
    verify_next_native_execution_turn(compact, result)


@pytest.mark.parametrize(
    "mutation", ["stale_ready", "wrong_turn", "wrong_ordinal", "old_response", "old_run"]
)
async def test_next_turn_rejects_stale_or_substituted_predecessor(mutation: str) -> None:
    request = await _request()
    if mutation == "stale_ready":
        request = request.model_copy(
            update={
                "continuation": request.continuation.model_copy(
                    update={"source_cut_fingerprint": "a" * 64}
                )
            }
        )
    elif mutation == "wrong_turn":
        request = request.model_copy(
            update={
                "expected_current_turn": request.expected_current_turn.model_copy(
                    update={"subject_id": "old-turn"}
                )
            }
        )
    elif mutation == "wrong_ordinal":
        request = request.model_copy(update={"next_ordinal": 3})
    elif mutation == "old_response":
        request = request.model_copy(
            update={"immediately_preceding_response": request.expected_current_turn}
        )
    else:
        request = request.model_copy(update={"run": request.cut.complete_ordered_run_lineage[0]})
    with pytest.raises(ValueError):
        prepare_next_native_execution_turn(request)


async def test_verifier_rejects_duplicate_or_mutated_next_turn() -> None:
    request = await _request()
    result = prepare_next_native_execution_turn(request)
    assert isinstance(result.run, ExecutionRunRecord)
    duplicate = result.run.model_copy(
        update={"turns": (*result.run.turns, result.run.turns[-1]), "head": "pending"}
    )
    duplicate = _seal_run(duplicate)
    forged = result.model_copy(
        update={
            "run": duplicate,
            "complete_batch_fingerprint": _batch_fingerprint(duplicate, result.continuation),
        }
    )
    with pytest.raises(ValueError, match="historical Turn"):
        verify_next_native_execution_turn(request, forged)


@pytest.mark.parametrize(
    "mutation",
    ["missing_publication", "split_decision", "forged_result", "extra_source", "changed_frontier"],
)
async def test_next_turn_rejects_forged_or_split_publication(mutation: str) -> None:
    request = await _request()
    publication = request.selected_publication
    assert publication is not None
    if mutation == "missing_publication":
        request = request.model_copy(update={"selected_publication": None})
    elif mutation == "split_decision":
        substituted = publication.readiness_source.model_copy(
            update={"selected_decision": request.expected_current_turn}
        )
        request = request.model_copy(
            update={
                "selected_publication": publication.model_copy(
                    update={"readiness_source": substituted}
                ),
                "cut": request.cut.model_copy(
                    update={"complete_sources": (*request.cut.complete_sources[:-1], substituted)}
                ),
            }
        )
    elif mutation == "forged_result":
        stamp = publication.result_stamp.model_copy(
            update={"predecessor_materialization_commitment": "9" * 64}
        )
        request = request.model_copy(
            update={"selected_publication": publication.model_copy(update={"result_stamp": stamp})}
        )
    elif mutation == "extra_source":
        request = request.model_copy(
            update={
                "cut": request.cut.model_copy(
                    update={
                        "complete_sources": (
                            *request.cut.complete_sources,
                            request.cut.complete_sources[0],
                        )
                    }
                )
            }
        )
    else:
        frontier = request.cut.frontier.model_copy(update={"ordered_calls": ()})
        request = request.model_copy(
            update={"cut": request.cut.model_copy(update={"frontier": frontier})}
        )
    with pytest.raises(ValueError):
        prepare_next_native_execution_turn(request)


async def test_next_turn_rejects_frontier_initialized_head_substituted_under_same_call_id() -> None:
    request = await _request(
        substituted_initialized=Present(head="record:substituted", fingerprint="6" * 64)
    )
    with pytest.raises(ValueError, match="sealed initialized revision"):
        prepare_next_native_execution_turn(request)


async def test_next_turn_rejects_frontier_call_substituted_behind_response_view() -> None:
    request = await _request(mismatched_frontier_call=True)
    with pytest.raises(ValueError, match="complete current frontier"):
        prepare_next_native_execution_turn(request)
