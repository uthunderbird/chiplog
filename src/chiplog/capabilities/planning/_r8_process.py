"""R8 isolated planning policy: all authority inputs are recorder reads."""

from __future__ import annotations

import base64
import json

from ._r7_owner import R7PlanningOwner
from ._r8_authority import PlanningAuthorityRecorder, decode_trace, require_reproduced_trace
from .r7_boundary import R7PlanningCreateDTO
from .r8_boundary import AuthorityRead, AuthorityReadKind, AuthorityTrace, R8PlanningRequest

ROUTES = (
    (
        "planning.r8_create_intention_line",
        "broker",
        "planning",
        "chiplog.planning.public.create.v2",
        "chiplog.planning.public.result.v1",
    ),
)


class _BrokerTraceSource:
    def __init__(self, trace: AuthorityTrace) -> None:
        self._trace = trace

    def read_authority(self, kind: AuthorityReadKind) -> AuthorityRead:
        matches = [read for read in self._trace.reads if read.kind == kind]
        if len(matches) != 1:
            raise ValueError("broker trace contains missing or duplicate authority input")
        return matches[0]


def _dispatch(operation: str, payload: bytes) -> dict[str, object]:
    if operation != "planning.r8_create_intention_line":
        return {"failure": "UNAVAILABLE", "reason": "R8 has no direct untracked planning route"}
    values = json.loads(payload)
    for key in ("command_bytes", "authority_trace_bytes"):
        values[key] = base64.b64decode(values[key], validate=True)
    request = R8PlanningRequest.model_validate(values)
    if request.canonical_bytes() != payload:
        raise ValueError("R8 request is noncanonical")
    original = decode_trace(request.authority_trace_bytes)
    recorder = PlanningAuthorityRecorder(
        _BrokerTraceSource(original),
        tenant_id=original.tenant_id,
        principal_id=original.principal_id,
        now_ns=request.observed_time_ns,
    )
    trust = recorder.read("TRUST")
    planning = recorder.read("PLANNING")
    recorder.read("REGISTRY")
    reproduced = recorder.finish()
    require_reproduced_trace(original, reproduced)
    command = json.loads(request.command_bytes)
    if json.dumps(command, sort_keys=True, separators=(",", ":")).encode() != request.command_bytes:
        raise ValueError("R8 command bytes are not canonical")
    # Extra caller-supplied trust/snapshot/cache fields fail the closed command set.
    if set(command) != {
        "command_id",
        "intention_line_id",
        "revision_id",
        "purpose",
        "authority_act_id",
    }:
        raise ValueError("R8 command contains untracked authority input")
    result = R7PlanningOwner().execute(
        R7PlanningCreateDTO(
            tenant_id=original.tenant_id,
            principal_id=original.principal_id,
            trust_reference_bytes=trust.canonical_value,
            planning_snapshot_bytes=planning.canonical_value,
            **command,
        )
    )
    return {
        "payload": base64.b64encode(result.canonical_bytes()).decode("ascii"),
        "schema_id": "chiplog.planning.public.result.v1",
    }


def dispatch(operation: str, payload: bytes) -> dict[str, object]:
    try:
        return _dispatch(operation, payload)
    except ValueError, TypeError, KeyError:
        return {"failure": "PROTOCOL_REJECTED", "reason": "invalid or untracked R8 authority input"}


__all__ = ["dispatch"]
