"""Structural owner checks only; these fixtures do not authenticate a recovery cut."""

import pytest
from tests.support.capabilities.agent_loop.execution_next_turn_preparation import (
    _request,
    _seal_run,
)

from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.capabilities.agent_loop.execution_next_turn_preparation import (
    _batch_fingerprint,
    prepare_next_native_execution_turn,
    reconstruct_prior_continuation_cut,
    verify_next_native_execution_turn,
)
from chiplog.capabilities.agent_loop.execution_recovery_preparation import (
    _continuation_closures,
)
from chiplog.capabilities.agent_loop.recovery_contracts import (
    Present,
)
from chiplog.capabilities.agent_loop.recovery_domain import sealed_call_accounting


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


async def test_next_turn_rebinds_fresh_turn_to_authenticated_worker_generation() -> None:
    request = await _request()
    request = request.model_copy(
        update={
            "fence": request.fence.model_copy(
                update={"worker_session_id": "reopened-worker", "runtime_generation": "r7:2"}
            )
        }
    )
    result = prepare_next_native_execution_turn(request)
    verify_next_native_execution_turn(request, result)
    assert result.run.worker_session == "reopened-worker"
    assert result.run.turns[:-1] == request.run.turns


async def test_next_turn_verifier_rejects_worker_not_bound_to_fence() -> None:
    request = await _request()
    result = prepare_next_native_execution_turn(request)
    forged_run = result.run.model_copy(update={"worker_session": "substituted-worker"})
    forged_run = forged_run.model_copy(
        update={"head": "loop:" + forged_run.model_copy(update={"head": "pending"}).digest()}
    )
    forged = result.model_copy(
        update={
            "run": forged_run,
            "complete_batch_fingerprint": _batch_fingerprint(forged_run, result.continuation),
        }
    )
    with pytest.raises(ValueError, match="proposal changed frozen Run"):
        verify_next_native_execution_turn(request, forged)


async def test_next_turn_preserves_recovered_obligation_and_current_reduction() -> None:
    request = await _request(recovered=True)
    result = prepare_next_native_execution_turn(request)
    verify_next_native_execution_turn(request, result)
    assert result.run.turns[-1].ordinal == 2
    assert request.run.turns[-1].response_seal == request.immediately_preceding_response
    assert request.cut.complete_ordered_responses[0].ordered_calls[0].disposition.kind == (
        "RECOVERY_REQUIRED"
    )
    assert result.continuation.complete_original_closures == request.cut.original_closures
    assert result.continuation.complete_current_reductions == request.cut.current_reductions


@pytest.mark.parametrize("kind", ["SUCCEEDED", "FAILED_DEFINITE"])
async def test_next_turn_accepts_only_direct_terminal_with_no_recovery_pair(kind: str) -> None:
    request = await _request(direct_terminal=kind)
    result = prepare_next_native_execution_turn(request)
    verify_next_native_execution_turn(request, result)
    assert request.cut.original_closures == ()
    assert request.cut.current_reductions == ()
    assert result.run.turns[-1].state == "PREPARING"


async def test_next_turn_holds_direct_terminal_with_recovery_material() -> None:
    request = await _request(recovered=True, direct_terminal="SUCCEEDED")
    with pytest.raises(ValueError, match="direct terminal next Turn carries"):
        prepare_next_native_execution_turn(request)


async def test_continuation_projection_keeps_exact_recovered_pair() -> None:
    request = await _request(recovered=True)
    cut = request.cut
    view = cut.complete_ordered_responses[0]
    accounting = sealed_call_accounting(
        view.seal.response_seal_id,
        view.seal.digest(),
        tuple(item.subject_id for item in view.seal.complete_ordered_initialized),
        view.ordered_calls,
    )

    closures, reductions = _continuation_closures(cut, accounting)

    assert closures == cut.original_closures
    assert reductions == cut.current_reductions
    assert len(closures) == len(reductions) == 1
    assert (
        closures[0].recovered_outcome == view.ordered_calls[0].disposition.recovered_outcome.outcome
    )
    assert reductions[0].accepted_witness == closures[0].accepted_evidence


@pytest.mark.parametrize("mutation", ["missing_closure", "missing_reduction", "held_reduction"])
async def test_next_turn_holds_recovered_call_without_current_consumable_pair(
    mutation: str,
) -> None:
    request = await _request(recovered=True)
    cut = request.cut
    if mutation == "missing_closure":
        cut = cut.model_copy(update={"original_closures": ()})
    elif mutation == "missing_reduction":
        cut = cut.model_copy(update={"current_reductions": ()})
    else:
        held = cut.current_reductions[0].model_copy(update={"consumability": "HOLD"})
        cut = cut.model_copy(update={"current_reductions": (held,)})
    request = request.model_copy(update={"cut": cut})
    with pytest.raises(ValueError):
        prepare_next_native_execution_turn(request)


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
