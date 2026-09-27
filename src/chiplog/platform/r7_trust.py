"""Broker-owned inert wire values for calls to the deployment-trust owner."""

from __future__ import annotations

import base64
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class TrustOwnerCall(_Strict):
    mode: Literal[
        "AUTHENTICATE",
        "BOOTSTRAP",
        "REVALIDATE",
        "RUNTIME_ADMISSION",
        "ISSUE_HERMETIC_OUTPUT_SCOPE_V1",
        "READ_CURRENT_HERMETIC_OUTPUT_SCOPE_V1",
    ]
    snapshot_bytes: bytes
    request_bytes: bytes

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            {
                "mode": self.mode,
                "request_bytes": base64.b64encode(self.request_bytes).decode(),
                "snapshot_bytes": base64.b64encode(self.snapshot_bytes).decode(),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _canonical_base64_bytes(value: object) -> bytes:
    if not isinstance(value, str):
        raise ValueError("trust owner wire bytes are not base64 text")
    try:
        encoded = value.encode("ascii")
        decoded = base64.b64decode(encoded, validate=True)
    except (UnicodeEncodeError, ValueError) as error:
        raise ValueError("trust owner wire bytes are not valid base64") from error
    if base64.b64encode(decoded).decode("ascii") != value:
        raise ValueError("trust owner wire bytes are noncanonical base64")
    return decoded


def decode_trust_owner_call_canonical(payload: bytes) -> TrustOwnerCall:
    """Decode one closed, canonical deployment-trust owner-call wire."""
    try:
        raw = json.loads(payload, object_pairs_hook=_unique_object)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("trust owner call is not JSON") from error
    if not isinstance(raw, dict) or set(raw) != {"mode", "request_bytes", "snapshot_bytes"}:
        raise ValueError("trust owner call has an unexpected outer shape")
    call = TrustOwnerCall.model_validate(
        {
            "mode": raw["mode"],
            "request_bytes": _canonical_base64_bytes(raw["request_bytes"]),
            "snapshot_bytes": _canonical_base64_bytes(raw["snapshot_bytes"]),
        }
    )
    if call.canonical_bytes() != payload:
        raise ValueError("trust owner call is noncanonical")
    return call


class TrustOwnerResult(_Strict):
    disposition: Literal["VALID", "DENIED", "STALE", "INDETERMINATE"]
    reference_bytes: bytes | None
    reason: str | None

    def canonical_bytes(self) -> bytes:
        import base64

        return json.dumps(
            {
                "disposition": self.disposition,
                "reason": self.reason,
                "reference_bytes": None
                if self.reference_bytes is None
                else base64.b64encode(self.reference_bytes).decode(),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()


def encode_trust_journal(
    entries: tuple[tuple[str, str | None, bytes], ...],
) -> bytes:
    return json.dumps(
        [
            [decision_id, predecessor, base64.b64encode(raw).decode("ascii")]
            for decision_id, predecessor, raw in entries
        ],
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


__all__ = [
    "TrustOwnerCall",
    "TrustOwnerResult",
    "decode_trust_owner_call_canonical",
    "encode_trust_journal",
]
