"""Owner-local immutable calendar evidence and provider read contracts."""

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .boundary import DisclosureEnvelope


class CalendarObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    provider_id: str = Field(min_length=1)
    calendar_id: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    title: str
    start_ns: int = Field(ge=0)
    end_ns: int = Field(ge=0)
    observed_at_ns: int = Field(ge=0)
    freshness: Literal["CURRENT", "LAGGING", "UNKNOWN"]
    envelope: DisclosureEnvelope


class CalendarBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    revision: str = Field(min_length=1)
    observations: tuple[CalendarObservation, ...]


class CalendarProviderPort(Protocol):
    async def observe(self) -> CalendarBatch: ...


__all__ = ["CalendarBatch", "CalendarObservation", "CalendarProviderPort"]
