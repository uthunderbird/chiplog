"""Broker-owned inert wire values for calls to the deployment-trust owner."""

from __future__ import annotations

import base64
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class TrustOwnerCall(_Strict):
    mode: Literal["AUTHENTICATE", "BOOTSTRAP", "REVALIDATE", "RUNTIME_ADMISSION"]
    snapshot_bytes: bytes
    request_bytes: bytes

    def canonical_bytes(self) -> bytes:
        import base64

        return json.dumps(
            {
                "mode": self.mode,
                "request_bytes": base64.b64encode(self.request_bytes).decode(),
                "snapshot_bytes": base64.b64encode(self.snapshot_bytes).decode(),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()


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
    "encode_trust_journal",
]
