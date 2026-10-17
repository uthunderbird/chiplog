"""Native client shapes only; selected PG publication supplies authority.

Clock, cutoff, holder/session and proof issuance belong to the runtime. A cycle
publishes dispositions, boundary, lineage and native Run initialization together.
Exact replay returns selected bytes rather than sampling a new interval.
"""

from typing import Annotated, Literal, Protocol

from pydantic import ConfigDict, Field

from .contracts import BudgetPolicy, SchedulerRootReference
from .recovery_contracts import Identity, Present, RecoveryDTO, UInt64
from .scheduler_contracts import (
    MissedOccurrencePolicyHead,
    OccurrenceDisposition,
    ScheduleDefinitionHead,
    SchedulerEligibilityManifest,
    SchedulerIntervalBound,
    SchedulerIntervalBoundHead,
    SchedulerLineageView,
    SchedulerOverflowHold,
)


class SchedulerClientDTO(RecoveryDTO):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        ser_json_bytes="base64",
        val_json_bytes="base64",
    )


class NativeScheduleDraft(SchedulerClientDTO):
    act_id: Identity
    schedule_id: Identity
    start_ns: UInt64
    period_ns: int = Field(gt=0)
    end_exclusive_ns: UInt64
    prompt: Identity
    budget: BudgetPolicy
    missed_policy: Literal["SKIP", "COALESCE", "MATERIALIZE_EACH"]
    bound: SchedulerIntervalBound


class NativeSchedulerAdoption(SchedulerClientDTO):
    """Persist original act and exact preview bytes; malformed bytes stay untrusted."""

    act_id: Identity
    canonical_preview: bytes = Field(min_length=1)


class NativeScheduleSelection(SchedulerClientDTO):
    disposition: Literal["COMMITTED", "REPLAY"]
    publication_sequence: int = Field(gt=0)
    schedule: ScheduleDefinitionHead
    policy: MissedOccurrencePolicyHead
    bound: SchedulerIntervalBoundHead


class NativeSchedulerCycle(SchedulerClientDTO):
    act_id: Identity
    schedule: ScheduleDefinitionHead


class NativeScheduledExecution(SchedulerClientDTO):
    kind: Literal["EXECUTION"] = "EXECUTION"
    run_id: Identity
    initial_run: Present
    root: SchedulerRootReference


class NativeSkippedExecution(SchedulerClientDTO):
    kind: Literal["NO_EXECUTION"] = "NO_EXECUTION"


class NativeOccurrenceMember(SchedulerClientDTO):
    disposition: OccurrenceDisposition
    execution: Annotated[
        NativeScheduledExecution | NativeSkippedExecution, Field(discriminator="kind")
    ]


class NativeOrdinaryInterval(SchedulerClientDTO):
    kind: Literal["ORDINARY"] = "ORDINARY"
    interval: Present
    manifest: SchedulerEligibilityManifest
    members: tuple[NativeOccurrenceMember, ...]
    boundary: Present


class NativeOverflowInterval(SchedulerClientDTO):
    kind: Literal["OVERFLOW"] = "OVERFLOW"
    hold: SchedulerOverflowHold


class NativeSchedulerCycleResult(SchedulerClientDTO):
    disposition: Literal["COMMITTED", "REPLAY"]
    publication_sequence: int = Field(gt=0)
    result: Annotated[NativeOrdinaryInterval | NativeOverflowInterval, Field(discriminator="kind")]


class NativeOverflowResolutionDraft(SchedulerClientDTO):
    """Preview/adoption authorizes explicit bound replacement and held membership."""

    act_id: Identity
    schedule: ScheduleDefinitionHead
    hold: Present
    expected_bound: SchedulerIntervalBoundHead
    sufficient_bound: SchedulerIntervalBound


class NativeSchedulerLeaseRequest(SchedulerClientDTO):
    act_id: Identity
    transition: Literal["ACQUIRE", "RENEW", "TAKEOVER"]
    expected: SchedulerLineageView


class NativeSchedulerLeaseResult(SchedulerClientDTO):
    disposition: Literal["COMMITTED", "REPLAY"]
    publication_sequence: int = Field(gt=0)
    lineage: SchedulerLineageView


class NativeSchedulerPort(Protocol):
    """Rejections raise LoopRejected/LoopHold; corrupt durable input raises integrity error.

    Previews grant no authority. Root/Run/member joins and current lease guards
    are runtime obligations, including at model/effect handoff, not shape checks.
    """

    async def preview_schedule(self, peer: str, draft: NativeScheduleDraft) -> bytes: ...
    async def adopt_schedule(
        self, peer: str, adoption: NativeSchedulerAdoption
    ) -> NativeScheduleSelection: ...
    async def scheduler_cycle(
        self, peer: str, request: NativeSchedulerCycle
    ) -> NativeSchedulerCycleResult: ...
    async def preview_overflow_resolution(
        self, peer: str, draft: NativeOverflowResolutionDraft
    ) -> bytes: ...
    async def resolve_scheduler_overflow(
        self, peer: str, adoption: NativeSchedulerAdoption
    ) -> NativeSchedulerCycleResult: ...
    async def transition_scheduler_lease(
        self, peer: str, request: NativeSchedulerLeaseRequest
    ) -> NativeSchedulerLeaseResult: ...
