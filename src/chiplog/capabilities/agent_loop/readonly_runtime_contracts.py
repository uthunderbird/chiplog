"""Small persisted read-only lineage; historical proof-bearing V1 stays separate."""

from typing import Literal

from pydantic import Field

from .readonly_history_tool_contracts import ConversationHistoryQuery, ReadOnlyDTO
from .recovery_contracts import Identity

ReadOnlyStateV2 = Literal[
    "INITIALIZED",
    "ACCEPTED",
    "RETRY_PENDING",
    "SUCCEEDED",
    "FAILED_DEFINITE_FINAL",
    "RECOVERY_REQUIRED",
]


class HistorySnapshotSelectionV2(ReadOnlyDTO):
    snapshot_id: Identity
    tenant_id: Identity
    principal_id: Identity
    frontier: int = Field(strict=True, ge=0)


class ReadOnlyOutcomeV2(ReadOnlyDTO):
    kind: Literal[
        "SUCCEEDED", "FAILED_DEFINITE_RETRYABLE", "FAILED_DEFINITE_FINAL", "OUTCOME_UNKNOWN"
    ]
    value: bytes = b""
    reason: str | None = None


class ReadOnlyAttemptV2(ReadOnlyDTO):
    attempt_id: Identity
    ordinal: int = Field(strict=True, ge=1, le=3)
    run_id: Identity
    outcome: ReadOnlyOutcomeV2 | None = None


class ReadOnlyLineageV2(ReadOnlyDTO):
    """One atomic state record includes every consumed attempt, hence its counter."""

    schema_id: Literal["chiplog.readonly.lineage.v2"] = "chiplog.readonly.lineage.v2"
    head: Identity
    predecessor: Identity | None
    lineage_id: Identity
    original_call_id: Identity
    query: ConversationHistoryQuery
    selection: HistorySnapshotSelectionV2
    max_attempts: Literal[3] = 3
    attempts: tuple[ReadOnlyAttemptV2, ...] = Field(default=(), max_length=3)

    @property
    def attempts_consumed(self) -> int:
        return len(self.attempts)

    @property
    def budget_exhausted(self) -> bool:
        return (
            self.attempts_consumed == self.max_attempts
            and self.attempts[-1].outcome is not None
            and self.attempts[-1].outcome.kind == "FAILED_DEFINITE_RETRYABLE"
        )

    @property
    def state(self) -> ReadOnlyStateV2:
        if not self.attempts:
            return "INITIALIZED"
        outcome = self.attempts[-1].outcome
        if outcome is None:
            return "ACCEPTED"
        if outcome.kind == "SUCCEEDED":
            return "SUCCEEDED"
        if outcome.kind == "OUTCOME_UNKNOWN":
            return "RECOVERY_REQUIRED"
        if outcome.kind == "FAILED_DEFINITE_FINAL" or self.budget_exhausted:
            return "FAILED_DEFINITE_FINAL"
        return "RETRY_PENDING"

    @property
    def terminal(self) -> bool:
        return self.state in ("SUCCEEDED", "FAILED_DEFINITE_FINAL")
