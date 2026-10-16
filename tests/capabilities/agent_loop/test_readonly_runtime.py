import pytest

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.readonly_history_tool_contracts import ConversationHistoryQuery
from chiplog.capabilities.agent_loop.readonly_runtime import (
    accept_readonly_attempt,
    initialize_readonly_lineage,
    record_readonly_outcome,
)
from chiplog.capabilities.agent_loop.readonly_runtime_contracts import (
    HistorySnapshotSelectionV2,
    ReadOnlyLineageV2,
    ReadOnlyOutcomeV2,
)


def initialized() -> ReadOnlyLineageV2:
    return initialize_readonly_lineage(
        lineage_id="lineage:original",
        original_call_id="original",
        query=ConversationHistoryQuery(limit=10, after_cursor=None),
        selection=HistorySnapshotSelectionV2(
            snapshot_id="history:7", tenant_id="tenant", principal_id="principal", frontier=7
        ),
    )


def reopen(value: ReadOnlyLineageV2) -> ReadOnlyLineageV2:
    return ReadOnlyLineageV2.model_validate_json(value.canonical_bytes())


def test_three_attempt_budget_survives_reopen_and_successor_runs() -> None:
    original = initialized()
    current = original
    for ordinal in (1, 2, 3):
        previous = current
        current = accept_readonly_attempt(
            reopen(current), run_id=f"run-{ordinal}", expected_head=current.head
        )
        assert current.predecessor == previous.head
        assert current.attempts_consumed == ordinal
        assert current.state == "ACCEPTED"
        assert not current.terminal
        assert current.attempts[-1].run_id == f"run-{ordinal}"
        current = record_readonly_outcome(
            current, ReadOnlyOutcomeV2(kind="FAILED_DEFINITE_RETRYABLE", reason="unavailable")
        )
        assert current.state == ("RETRY_PENDING" if ordinal < 3 else "FAILED_DEFINITE_FINAL")
        assert current.query == original.query and current.selection == original.selection
        assert current.lineage_id == original.lineage_id
        assert current.original_call_id == original.original_call_id
    current = reopen(current)
    assert current.budget_exhausted and current.terminal
    with pytest.raises(LoopRejected, match=r"terminal|budget"):
        accept_readonly_attempt(current, run_id="run-4", expected_head=current.head)
    assert current.attempts_consumed == 3


def test_selected_success_replays_and_cannot_be_replaced_or_retried() -> None:
    current = initialized()
    accepted = accept_readonly_attempt(current, run_id="run", expected_head=current.head)
    outcome = ReadOnlyOutcomeV2(kind="SUCCEEDED", value=b'[{"text":"history"}]')
    complete = reopen(record_readonly_outcome(accepted, outcome))
    assert complete.state == "SUCCEEDED" and complete.terminal
    assert record_readonly_outcome(complete, outcome) == complete
    with pytest.raises(LoopRejected, match="outcome"):
        record_readonly_outcome(complete, ReadOnlyOutcomeV2(kind="FAILED_DEFINITE_FINAL"))
    with pytest.raises(LoopRejected, match="terminal"):
        accept_readonly_attempt(complete, run_id="other", expected_head=complete.head)


def test_missing_or_unknown_outcome_holds_without_consuming_another_attempt() -> None:
    current = initialized()
    accepted = reopen(accept_readonly_attempt(current, run_id="run", expected_head=current.head))
    with pytest.raises(LoopRejected, match="unresolved"):
        accept_readonly_attempt(accepted, run_id="restart", expected_head=accepted.head)
    unknown = record_readonly_outcome(accepted, ReadOnlyOutcomeV2(kind="OUTCOME_UNKNOWN"))
    assert unknown.state == "RECOVERY_REQUIRED" and not unknown.terminal
    with pytest.raises(LoopRejected, match="unresolved"):
        accept_readonly_attempt(unknown, run_id="restart", expected_head=unknown.head)
    assert unknown.attempts_consumed == accepted.attempts_consumed == 1


def test_stale_lineage_head_cannot_accept_and_outcome_requires_acceptance() -> None:
    current = initialized()
    with pytest.raises(LoopRejected, match="stale"):
        accept_readonly_attempt(current, run_id="run", expected_head="old")
    with pytest.raises(LoopRejected, match="accepted"):
        record_readonly_outcome(current, ReadOnlyOutcomeV2(kind="SUCCEEDED"))
