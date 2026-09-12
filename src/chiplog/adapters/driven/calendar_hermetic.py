"""Hermetic, immutable provider observation leaf; has no write/effect operation."""

from chiplog.capabilities.calendar_observations.contracts import CalendarBatch


class HermeticCalendarProvider:
    def __init__(self, batch: CalendarBatch) -> None:
        self._batch = CalendarBatch.model_validate_json(batch.model_dump_json())

    async def observe(self) -> CalendarBatch:
        return self._batch
