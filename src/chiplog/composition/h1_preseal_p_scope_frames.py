"""Canonical full-frame persistence for the H1 V2 preseal P exchanges.

This is deliberately a sibling of the frozen payload-only descriptor.  The
containing selected DECIDED authenticates both siblings; this codec proves that
the full broker frames are exactly those payload pairs already bound by the
P/E anchor.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Final, cast

from chiplog.composition.common_cli_execution_runtime import _H1ScopeWire
from chiplog.composition.h1_preseal_p_scope_wires import H1PresealPScopeWiresV1
from chiplog.platform.broker import PublicPortCall, PublicPortSuccess

_FIELDS: Final = frozenset(("kind", "version", "binding", "issue", "current"))
_FRAME_FIELDS: Final = frozenset(("sent", "returned", "sent_at_ns", "returned_at_ns"))


class H1PresealPScopeFramesError(ValueError):
    """The retained P full-frame sibling is not exact or canonical."""


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise H1PresealPScopeFramesError("P scope frames are not canonical JSON") from error


def _no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise H1PresealPScopeFramesError("P scope frames have a duplicate JSON key")
        result[key] = value
    return result


def _forbid_number(value: str) -> object:
    del value
    raise H1PresealPScopeFramesError("P scope frames do not permit non-integral numbers")


def _strict_json(raw: bytes) -> dict[str, object]:
    if not isinstance(raw, bytes) or not raw:
        raise H1PresealPScopeFramesError("P scope frame bytes are absent")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_no_duplicates,
            parse_float=_forbid_number,
            parse_constant=_forbid_number,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, H1PresealPScopeFramesError) as error:
        raise H1PresealPScopeFramesError("P scope frame bytes are not strict JSON") from error
    if not isinstance(value, dict) or _canonical(value) != raw:
        raise H1PresealPScopeFramesError("P scope frame bytes are noncanonical")
    return value


def _payload(value: object, name: str) -> bytes:
    if not isinstance(value, str):
        raise H1PresealPScopeFramesError(f"{name} is not base64 text")
    try:
        raw = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError) as error:
        raise H1PresealPScopeFramesError(f"{name} is invalid base64") from error
    if base64.b64encode(raw).decode("ascii") != value:
        raise H1PresealPScopeFramesError(f"{name} is noncanonical base64")
    return raw


def _frame(value: object, *, role: str, payload_pair: object) -> _H1ScopeWire:
    if not isinstance(value, dict) or set(value) != _FRAME_FIELDS:
        raise H1PresealPScopeFramesError(f"{role} frame fields differ")
    if not isinstance(payload_pair, dict):
        raise H1PresealPScopeFramesError(f"{role} payload pair is absent")
    try:
        # These records deliberately retain JSON-mode DTO dumps so payload
        # bytes are explicit canonical base64.  Re-enter through JSON rather
        # than Python strict-mode validation, which correctly treats those
        # bytes and tuples as wire representations.
        sent = PublicPortCall.model_validate_json(_canonical(value["sent"]))
        returned = PublicPortSuccess.model_validate_json(_canonical(value["returned"]))
    except (TypeError, ValueError) as error:
        raise H1PresealPScopeFramesError(f"{role} broker frame is malformed") from error
    sent_at_ns, returned_at_ns = value["sent_at_ns"], value["returned_at_ns"]
    if (
        sent.model_dump(mode="json") != value["sent"]
        or returned.model_dump(mode="json") != value["returned"]
        or type(sent_at_ns) is not int
        or type(returned_at_ns) is not int
        or sent_at_ns <= 0
        or returned_at_ns < sent_at_ns
        or returned_at_ns >= sent.budget.absolute_deadline_ns
        or returned.request_id != sent.request_id
        or returned.responder != sent.callee
    ):
        raise H1PresealPScopeFramesError(f"{role} broker frame differs")
    payload_sent = _payload(payload_pair.get("sent_payload_base64"), f"{role} sent payload")
    payload_returned = _payload(
        payload_pair.get("returned_payload_base64"), f"{role} returned payload"
    )
    if sent.canonical_payload != payload_sent or returned.canonical_payload != payload_returned:
        raise H1PresealPScopeFramesError(f"{role} frame payload differs from frozen V1 pair")
    return _H1ScopeWire(sent, returned, sent_at_ns, returned_at_ns)


@dataclass(frozen=True, slots=True)
class H1PresealPScopeFramesV1:
    """Canonical full broker frames for the already-authenticated P wire pairs."""

    _raw: bytes

    @classmethod
    def from_mapping(
        cls,
        value: object,
        *,
        anchor_binding: object,
        scope_wires: H1PresealPScopeWiresV1,
    ) -> H1PresealPScopeFramesV1:
        if not isinstance(value, dict) or set(value) != _FIELDS:
            raise H1PresealPScopeFramesError("P scope frame descriptor fields differ")
        if value["kind"] != "H1_PRESEAL_P_SCOPE_FRAMES_V1" or value["version"] != 1:
            raise H1PresealPScopeFramesError("P scope frame descriptor version differs")
        if not isinstance(value["binding"], dict) or _canonical(value["binding"]) != _canonical(
            anchor_binding
        ):
            raise H1PresealPScopeFramesError("P scope frame binding differs from anchor")
        old = scope_wires.as_dict()
        _frame(value["issue"], role="issue", payload_pair=old["issue"])
        _frame(value["current"], role="current", payload_pair=old["current"])
        return cls(_canonical(value))

    @classmethod
    def from_wires(
        cls,
        *,
        binding: dict[str, object],
        scope_wires: H1PresealPScopeWiresV1,
        issue_wire: _H1ScopeWire,
        current_wire: _H1ScopeWire,
    ) -> H1PresealPScopeFramesV1:
        def encode(wire: _H1ScopeWire) -> dict[str, object]:
            if type(wire.returned) is not PublicPortSuccess:
                raise H1PresealPScopeFramesError("accepted P owner response is not successful")
            return {
                "sent": wire.sent.model_dump(mode="json"),
                "returned": wire.returned.model_dump(mode="json"),
                "sent_at_ns": wire.sent_at_ns,
                "returned_at_ns": wire.returned_at_ns,
            }

        return cls.from_mapping(
            {
                "kind": "H1_PRESEAL_P_SCOPE_FRAMES_V1",
                "version": 1,
                "binding": binding,
                "issue": encode(issue_wire),
                "current": encode(current_wire),
            },
            anchor_binding=binding,
            scope_wires=scope_wires,
        )

    def canonical_bytes(self) -> bytes:
        return self._raw

    def as_dict(self) -> dict[str, object]:
        parsed = json.loads(self._raw)
        if not isinstance(parsed, dict):  # pragma: no cover
            raise H1PresealPScopeFramesError("P scope frame descriptor is not an object")
        return cast(dict[str, object], parsed)

    def wires(
        self, *, anchor_binding: object, scope_wires: H1PresealPScopeWiresV1
    ) -> tuple[_H1ScopeWire, _H1ScopeWire]:
        value = self.as_dict()
        if _canonical(value["binding"]) != _canonical(anchor_binding):
            raise H1PresealPScopeFramesError("P scope frame binding differs from anchor")
        old = scope_wires.as_dict()
        return (
            _frame(value["issue"], role="issue", payload_pair=old["issue"]),
            _frame(value["current"], role="current", payload_pair=old["current"]),
        )


def decode_h1_preseal_p_scope_frames(
    raw: bytes, *, anchor_binding: object, scope_wires: H1PresealPScopeWiresV1
) -> H1PresealPScopeFramesV1:
    return H1PresealPScopeFramesV1.from_mapping(
        _strict_json(raw), anchor_binding=anchor_binding, scope_wires=scope_wires
    )


__all__ = [
    "H1PresealPScopeFramesError",
    "H1PresealPScopeFramesV1",
    "decode_h1_preseal_p_scope_frames",
]
