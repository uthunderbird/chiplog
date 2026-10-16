"""A selected native successor retains immutable historical Turns and one counter."""

from typing import Literal

from pydantic import Field

from .contracts import Frozen


class ReadOnlySuccessorV2(Frozen):
    schema_id: Literal["chiplog.readonly.successor.v2"] = "chiplog.readonly.successor.v2"
    edge_id: str = Field(min_length=1)
    original_call_id: str = Field(min_length=1)
    lineage_id: str = Field(min_length=1)
    pending_head: str = Field(min_length=1)
    source_run_id: str = Field(min_length=1)
    source_run_head: str = Field(min_length=1)
    successor_run_id: str = Field(min_length=1)
    successor_active_head: str = Field(min_length=1)
    accepted_head: str = Field(min_length=1)
    inherited_turn_count: int = Field(strict=True, ge=1)
