"""Exact H1 terminal-call identities for clearance replay and broker admission."""

from __future__ import annotations

import hashlib
import json
from base64 import b64encode

from chiplog.platform.broker import PublicPortCall
from chiplog.platform.r7_runtime import _owner_request_bytes

_DOMAIN = b"chiplog.h1.terminal-public-port-call.v1\x00"


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _terminal_call_identity(call: PublicPortCall) -> tuple[str, bytes]:
    if type(call) is not PublicPortCall:
        raise TypeError("H1 terminal call must be an exact PublicPortCall")
    preimage = _canonical(
        {
            "operation_id": call.operation_id,
            "request_id": call.request_id,
            "caller": call.caller.model_dump(mode="json"),
            "callee": call.callee.model_dump(mode="json"),
            "schema_id": call.schema_id,
            "canonical_payload_base64": b64encode(call.canonical_payload).decode("ascii"),
            "budget": call.budget.model_dump(mode="json"),
            "held_resources": list(call.held_resources),
        }
    )
    return hashlib.sha256(_DOMAIN + preimage).hexdigest(), _owner_request_bytes(call)


__all__ = ["_terminal_call_identity"]
