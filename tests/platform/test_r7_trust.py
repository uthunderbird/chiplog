"""Closed canonical decoding for deployment-trust owner-call wires."""

from __future__ import annotations

import base64
import json

import pytest

from chiplog.platform.r7_trust import TrustOwnerCall, decode_trust_owner_call_canonical


def _wire(*, snapshot: bytes = b'[["decision",null,"e30="]]') -> TrustOwnerCall:
    return TrustOwnerCall(
        mode="ISSUE_HERMETIC_OUTPUT_SCOPE_V1",
        snapshot_bytes=snapshot,
        request_bytes=b'{"evidence":"pinned-owner-request"}',
    )


def test_canonical_owner_call_wire_decodes_exact_base64_bytes() -> None:
    wire = _wire()

    decoded = decode_trust_owner_call_canonical(wire.canonical_bytes())

    assert decoded == wire
    assert decoded.snapshot_bytes == b'[["decision",null,"e30="]]'
    assert decoded.request_bytes == b'{"evidence":"pinned-owner-request"}'


@pytest.mark.parametrize(
    "payload",
    (
        b'{"mode":"ISSUE_HERMETIC_OUTPUT_SCOPE_V1","snapshot_bytes":"eA==","snapshot_bytes":"eQ==","request_bytes":"e30="}',
        b'{"mode":"ISSUE_HERMETIC_OUTPUT_SCOPE_V1","snapshot_bytes":"eA=="}',
        b'{"mode":"ISSUE_HERMETIC_OUTPUT_SCOPE_V1","snapshot_bytes":"eA==","request_bytes":"e30=","extra":true}',
        b'{"mode":"ISSUE_HERMETIC_OUTPUT_SCOPE_V1","snapshot_bytes":"eA==","request_bytes":"%%%"}',
        b'{"mode":"ISSUE_HERMETIC_OUTPUT_SCOPE_V1","snapshot_bytes":"eA","request_bytes":"e30="}',
    ),
    ids=("duplicate", "missing", "unknown", "invalid-base64", "unpadded-base64"),
)
def test_owner_call_decoder_rejects_nonclosed_or_noncanonical_wire(payload: bytes) -> None:
    with pytest.raises(ValueError):
        decode_trust_owner_call_canonical(payload)


def test_owner_call_decoder_rejects_reordered_outer_wire() -> None:
    wire = _wire()
    decoded = json.loads(wire.canonical_bytes())
    reordered = json.dumps(
        {
            "mode": decoded["mode"],
            "snapshot_bytes": decoded["snapshot_bytes"],
            "request_bytes": decoded["request_bytes"],
        },
        separators=(",", ":"),
    ).encode()

    with pytest.raises(ValueError):
        decode_trust_owner_call_canonical(reordered)


def test_owner_call_decoder_does_not_treat_base64_text_as_utf8_bytes() -> None:
    wire = _wire(snapshot=b"\xff\x00",)
    raw = json.loads(wire.canonical_bytes())
    assert raw["snapshot_bytes"] == base64.b64encode(b"\xff\x00").decode("ascii")

    assert decode_trust_owner_call_canonical(wire.canonical_bytes()).snapshot_bytes == b"\xff\x00"
