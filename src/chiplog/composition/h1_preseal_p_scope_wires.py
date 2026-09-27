"""Closed retained-wire descriptor for the H1 V2 preseal P seam.

This codec only establishes exact byte shape and the anchor digest join.  The
installed owner and historical readers establish provenance separately.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Final, cast

from chiplog.capabilities.deployment_trust.h1_broker_evidence_contracts import (
    H1OwnerCandidateCallV1,
    H1OwnerCandidateV1,
    H1OwnerCurrentCallV1,
    H1OwnerCurrentCandidateV1,
)
from chiplog.platform.r7_trust import decode_trust_owner_call_canonical

_FIELDS: Final = frozenset(("kind", "version", "binding", "issue", "current"))
_PAIR_FIELDS: Final = frozenset(("sent_payload_base64", "returned_payload_base64"))


class H1PresealPScopeWiresError(ValueError):
    """The retained P exchange descriptor is not an exact accepted pair."""


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
    except (TypeError, ValueError) as error:
        raise H1PresealPScopeWiresError("P scope wires are not canonical JSON") from error


def _no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise H1PresealPScopeWiresError("P scope wires have a duplicate JSON key")
        result[key] = value
    return result


def _forbid_number(value: str) -> object:
    del value
    raise H1PresealPScopeWiresError("P scope wires do not permit non-integral numbers")


def _strict_json(raw: bytes) -> dict[str, object]:
    if not isinstance(raw, bytes) or not raw:
        raise H1PresealPScopeWiresError("P scope wire bytes are absent")
    try:
        value = json.loads(
            raw.decode(),
            object_pairs_hook=_no_duplicates,
            parse_float=_forbid_number,
            parse_constant=_forbid_number,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, H1PresealPScopeWiresError) as error:
        raise H1PresealPScopeWiresError("P scope wire bytes are not strict JSON") from error
    if not isinstance(value, dict) or _canonical(value) != raw:
        raise H1PresealPScopeWiresError("P scope wire bytes are noncanonical")
    return value


def _payload(value: object, name: str) -> tuple[str, bytes]:
    if not isinstance(value, str):
        raise H1PresealPScopeWiresError(f"{name} is not base64 text")
    try:
        raw = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError) as error:
        raise H1PresealPScopeWiresError(f"{name} is invalid base64") from error
    if base64.b64encode(raw).decode() != value:
        raise H1PresealPScopeWiresError(f"{name} is noncanonical base64")
    return value, raw


def _digest(pair: dict[str, object]) -> str:
    return hashlib.sha256(
        _canonical([pair["sent_payload_base64"], pair["returned_payload_base64"]])
    ).hexdigest()


def _pair(value: object, *, role: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != _PAIR_FIELDS:
        raise H1PresealPScopeWiresError(f"{role} wire fields differ")
    _sent_b64, sent = _payload(value["sent_payload_base64"], f"{role} sent payload")
    _returned_b64, returned = _payload(value["returned_payload_base64"], f"{role} returned payload")
    try:
        outer = decode_trust_owner_call_canonical(sent)
        if outer.canonical_bytes() != sent:
            raise ValueError("sent wrapper is noncanonical")
        if role == "issue":
            if outer.mode != "ISSUE_HERMETIC_OUTPUT_SCOPE_V1":
                raise ValueError("issue mode differs")
            issue_request = H1OwnerCandidateCallV1.model_validate_json(outer.request_bytes)
            issue_result = H1OwnerCandidateV1.model_validate_json(returned)
            if (
                issue_request.canonical_bytes() != outer.request_bytes
                or issue_result.canonical_bytes() != returned
            ):
                raise ValueError("accepted payload is noncanonical")
            issue_result.check_pinned_call(issue_request)
        else:
            if outer.mode != "READ_CURRENT_HERMETIC_OUTPUT_SCOPE_V1":
                raise ValueError("current mode differs")
            current_request = H1OwnerCurrentCallV1.model_validate_json(outer.request_bytes)
            current_result = H1OwnerCurrentCandidateV1.model_validate_json(returned)
            if (
                current_request.canonical_bytes() != outer.request_bytes
                or current_result.canonical_bytes() != returned
            ):
                raise ValueError("accepted payload is noncanonical")
            current_result.check_pinned_call(current_request)
    except (TypeError, ValueError) as error:
        raise H1PresealPScopeWiresError(f"{role} accepted exchange differs") from error
    return value


@dataclass(frozen=True, slots=True)
class H1PresealPScopeWiresV1:
    """Canonical descriptor whose payload bytes are immutable after capture."""

    _raw: bytes

    @classmethod
    def from_mapping(
        cls,
        value: object,
        *,
        anchor_binding: object,
        issue_digest: str,
        current_digest: str,
    ) -> H1PresealPScopeWiresV1:
        if not isinstance(value, dict) or set(value) != _FIELDS:
            raise H1PresealPScopeWiresError("P scope wire descriptor fields differ")
        if value["kind"] != "H1_PRESEAL_P_SCOPE_WIRES_V1" or value["version"] != 1:
            raise H1PresealPScopeWiresError("P scope wire descriptor version differs")
        binding = value["binding"]
        if not isinstance(binding, dict) or _canonical(binding) != _canonical(anchor_binding):
            raise H1PresealPScopeWiresError("P scope wire binding differs from anchor")
        issue = _pair(value["issue"], role="issue")
        current = _pair(value["current"], role="current")
        if _digest(issue) != issue_digest or _digest(current) != current_digest:
            raise H1PresealPScopeWiresError("P scope wire digest differs from anchor")
        return cls(_canonical(value))

    @classmethod
    def from_wires(
        cls, *, binding: dict[str, object], issue_wire: object, current_wire: object
    ) -> H1PresealPScopeWiresV1:
        def retained(wire: object) -> dict[str, str]:
            sent = getattr(getattr(wire, "sent", None), "canonical_payload", None)
            returned = getattr(getattr(wire, "returned", None), "canonical_payload", None)
            if not isinstance(sent, bytes) or not isinstance(returned, bytes):
                raise H1PresealPScopeWiresError("accepted P owner wire is unavailable")
            return {
                "sent_payload_base64": base64.b64encode(sent).decode(),
                "returned_payload_base64": base64.b64encode(returned).decode(),
            }

        value: dict[str, object] = {
            "kind": "H1_PRESEAL_P_SCOPE_WIRES_V1",
            "version": 1,
            "binding": binding,
            "issue": retained(issue_wire),
            "current": retained(current_wire),
        }
        return cls.from_mapping(
            value,
            anchor_binding=binding,
            issue_digest=_digest(cast(dict[str, object], value["issue"])),
            current_digest=_digest(cast(dict[str, object], value["current"])),
        )

    def canonical_bytes(self) -> bytes:
        return self._raw

    def as_dict(self) -> dict[str, object]:
        parsed = json.loads(self._raw)
        if not isinstance(parsed, dict):  # pragma: no cover - constructor closes this.
            raise H1PresealPScopeWiresError("P scope wire descriptor is not an object")
        return cast(dict[str, object], parsed)


def decode_h1_preseal_p_scope_wires(
    raw: bytes,
    *,
    anchor_binding: object,
    issue_digest: str,
    current_digest: str,
) -> H1PresealPScopeWiresV1:
    """Decode canonical retained bytes after the containing decision is authenticated."""

    return H1PresealPScopeWiresV1.from_mapping(
        _strict_json(raw),
        anchor_binding=anchor_binding,
        issue_digest=issue_digest,
        current_digest=current_digest,
    )


__all__ = [
    "H1PresealPScopeWiresError",
    "H1PresealPScopeWiresV1",
    "decode_h1_preseal_p_scope_wires",
]
