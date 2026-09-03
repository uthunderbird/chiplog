"""Planning-owned R7 public-port DTOs generated from the inert boundary schema."""

from __future__ import annotations

import base64
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict


class _StrictDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class R7PlanningCreateDTO(_StrictDTO):
    tenant_id: str
    principal_id: str
    command_id: str
    intention_line_id: str
    revision_id: str
    purpose: str
    authority_act_id: str
    trust_reference_bytes: bytes
    planning_snapshot_bytes: bytes

    def canonical_bytes(self) -> bytes:
        values = self.model_dump()
        for field in ("planning_snapshot_bytes", "trust_reference_bytes"):
            values[field] = base64.b64encode(getattr(self, field)).decode("ascii")
        return json.dumps(values, sort_keys=True, separators=(",", ":")).encode()


class R7PlanningResultDTO(_StrictDTO):
    disposition: Literal["COMMITTED", "REPLAY", "CONFLICT", "STALE", "DENIED", "INDETERMINATE"]
    canonical_result_bytes: bytes | None
    reason: str | None

    def canonical_bytes(self) -> bytes:
        values = self.model_dump()
        if self.canonical_result_bytes is not None:
            values["canonical_result_bytes"] = base64.b64encode(self.canonical_result_bytes).decode(
                "ascii"
            )
        return json.dumps(values, sort_keys=True, separators=(",", ":")).encode()


class R7PlanningRenderDTO(_StrictDTO):
    tenant_id: str
    planning_snapshot_bytes: bytes

    def canonical_bytes(self) -> bytes:
        values = self.model_dump()
        values["planning_snapshot_bytes"] = base64.b64encode(self.planning_snapshot_bytes).decode(
            "ascii"
        )
        return json.dumps(values, sort_keys=True, separators=(",", ":")).encode()


class R7PlanningRenderResultDTO(_StrictDTO):
    lines: tuple[str, ...]

    def canonical_bytes(self) -> bytes:
        return json.dumps(self.model_dump(), sort_keys=True, separators=(",", ":")).encode()


__all__ = [
    "R7PlanningCreateDTO",
    "R7PlanningRenderDTO",
    "R7PlanningRenderResultDTO",
    "R7PlanningResultDTO",
]
