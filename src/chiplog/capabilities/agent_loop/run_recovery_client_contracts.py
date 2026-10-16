"""Native suspension retains the actual source frontier without legacy proof DTOs."""

from typing import Literal

from pydantic import Field

from .contracts import Frozen


class NativeSuspensionBaselineV1(Frozen):
    schema_id: Literal["chiplog.agent-loop.native-suspension.v1"] = (
        "chiplog.agent-loop.native-suspension.v1"
    )
    head: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    source_run_head: str = Field(min_length=1)
    source_frontier: int = Field(strict=True, ge=0)
