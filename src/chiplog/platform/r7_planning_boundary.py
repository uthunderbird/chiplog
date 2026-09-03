"""Broker-owned interpretation of the planning R7 inert schema."""

from __future__ import annotations

import base64
import json

from pydantic import BaseModel, ConfigDict


class BrokerPlanningCreateDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

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


__all__ = ["BrokerPlanningCreateDTO"]
