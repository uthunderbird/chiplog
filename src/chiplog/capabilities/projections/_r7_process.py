"""Projection-owner process handler; imported only by the projection worker."""

from __future__ import annotations

import json
from base64 import b64decode, b64encode

from pydantic import BaseModel, ConfigDict

ROUTES = (
    (
        "projections.render_planning",
        "broker",
        "projections",
        "chiplog.planning.public.render.v1",
        "chiplog.planning.public.render-result.v1",
    ),
)


class _ProjectionRenderDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    tenant_id: str
    planning_snapshot_bytes: bytes

    def canonical_bytes(self) -> bytes:
        values = self.model_dump()
        values["planning_snapshot_bytes"] = b64encode(self.planning_snapshot_bytes).decode("ascii")
        return json.dumps(values, sort_keys=True, separators=(",", ":")).encode()


class _ProjectionRenderResultDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    lines: tuple[str, ...]

    def canonical_bytes(self) -> bytes:
        return json.dumps(self.model_dump(), sort_keys=True, separators=(",", ":")).encode()


def dispatch(operation: str, payload: bytes) -> dict[str, object]:
    if operation != "projections.render_planning":
        return {"failure": "UNAVAILABLE", "reason": "projection operation has no handler"}
    values = json.loads(payload)
    values["planning_snapshot_bytes"] = b64decode(values["planning_snapshot_bytes"])
    command = _ProjectionRenderDTO.model_validate(values)
    if command.canonical_bytes() != payload:
        return {"failure": "PROTOCOL_REJECTED", "reason": "payload is not canonical"}
    snapshot = json.loads(command.planning_snapshot_bytes)
    lines = [f"tenant={command.tenant_id} records={int(snapshot['record_count'])}"]
    for item in snapshot["lines"]:
        lines.extend((str(item["intention_line_id"]), f"purpose={item['purpose']}"))
    result = _ProjectionRenderResultDTO(lines=tuple(lines))
    return {
        "payload": b64encode(result.canonical_bytes()).decode("ascii"),
        "schema_id": "chiplog.planning.public.render-result.v1",
    }
