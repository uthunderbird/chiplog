"""Read-only attempt transitions; publication and query execution belong to composition."""

from .contracts import LoopRejected
from .readonly_history_tool_contracts import ConversationHistoryQuery
from .readonly_runtime_contracts import (
    HistorySnapshotSelectionV2,
    ReadOnlyAttemptV2,
    ReadOnlyLineageV2,
    ReadOnlyOutcomeV2,
)


def initialize_readonly_lineage(
    *,
    lineage_id: str,
    original_call_id: str,
    query: ConversationHistoryQuery,
    selection: HistorySnapshotSelectionV2,
) -> ReadOnlyLineageV2:
    return ReadOnlyLineageV2(
        head=lineage_id + ":initialized",
        predecessor=None,
        lineage_id=lineage_id,
        original_call_id=original_call_id,
        query=query,
        selection=selection,
    )


def accept_readonly_attempt(
    current: ReadOnlyLineageV2, *, run_id: str, expected_head: str
) -> ReadOnlyLineageV2:
    if current.head != expected_head:
        raise LoopRejected("stale read-only lineage head")
    if current.terminal:
        raise LoopRejected("read-only lineage is terminal; no attempt budget remains")
    if current.state not in ("INITIALIZED", "RETRY_PENDING"):
        raise LoopRejected("read-only attempt has an unresolved outcome")
    ordinal = current.attempts_consumed + 1
    attempt = ReadOnlyAttemptV2(
        attempt_id=f"{current.lineage_id}:attempt:{ordinal}", ordinal=ordinal, run_id=run_id
    )
    return current.model_copy(
        update={
            "head": attempt.attempt_id + ":accepted",
            "predecessor": current.head,
            "attempts": (*current.attempts, attempt),
        }
    )


def record_readonly_outcome(
    current: ReadOnlyLineageV2, outcome: ReadOnlyOutcomeV2
) -> ReadOnlyLineageV2:
    if not current.attempts:
        raise LoopRejected("read-only outcome requires an accepted attempt")
    attempt = current.attempts[-1]
    if attempt.outcome is not None:
        if attempt.outcome == outcome:
            return current
        raise LoopRejected("read-only attempt already has a different selected outcome")
    return current.model_copy(
        update={
            "head": attempt.attempt_id + ":outcome",
            "predecessor": current.head,
            "attempts": (*current.attempts[:-1], attempt.model_copy(update={"outcome": outcome})),
        }
    )
