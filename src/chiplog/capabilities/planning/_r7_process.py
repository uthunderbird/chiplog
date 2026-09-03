"""Planning-owner process handler; imported only by the planning worker."""

from __future__ import annotations

import json
from base64 import b64decode, b64encode

from ._r7_owner import R7PlanningOwner
from .r7_boundary import R7PlanningCreateDTO

ROUTES = (
    (
        "planning.create_intention_line",
        "broker",
        "planning",
        "chiplog.planning.public.create.v1",
        "chiplog.planning.public.result.v1",
    ),
)


def dispatch(operation: str, payload: bytes) -> dict[str, object]:
    if operation != "planning.create_intention_line":
        return {"failure": "UNAVAILABLE", "reason": "planning operation has no handler"}
    values = json.loads(payload)
    for field in ("planning_snapshot_bytes", "trust_reference_bytes"):
        values[field] = b64decode(values[field])
    command = R7PlanningCreateDTO.model_validate(values)
    if command.canonical_bytes() != payload:
        return {"failure": "PROTOCOL_REJECTED", "reason": "payload is not canonical"}
    result = R7PlanningOwner().execute(command)
    return {
        "payload": b64encode(result.canonical_bytes()).decode("ascii"),
        "schema_id": "chiplog.planning.public.result.v1",
    }
