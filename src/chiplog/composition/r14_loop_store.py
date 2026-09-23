"""Canonical R14 loop store: sole writer and shared authenticated Run projection."""

from collections.abc import Callable

from chiplog.adapters.driven.loop_sqlite import SQLiteLoopStore
from chiplog.capabilities.agent_loop.contracts import LoopRejected, LoopSnapshot, RunRecord
from chiplog.composition.r14_runtime import R14PlanningRuntime


class R14LoopStore(SQLiteLoopStore):
    def __init__(self, runtime: R14PlanningRuntime) -> None:
        self._runtime = runtime
        super().__init__(
            runtime._database, runtime._appender, runtime._tenant_id, "r6", authority=runtime
        )

    def snapshot(self) -> LoopSnapshot:
        return self._runtime._loop_snapshot()

    async def publish(
        self,
        record: RunRecord,
        expected: LoopSnapshot,
        validate: Callable[[LoopSnapshot], None] | None = None,
    ) -> None:
        if record.event in ("ModelResponseReceived", "CompleteAcceptance"):
            from chiplog.composition.r14_fanout import publish_fanout

            try:
                await publish_fanout(self._runtime, record, expected, validate)
            except LoopRejected:
                raise
            except ValueError as error:
                raise LoopRejected("invalid fanout preparation or publication") from error
        else:
            await super().publish(record, expected, validate)
