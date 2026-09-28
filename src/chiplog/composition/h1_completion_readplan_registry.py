"""Closed, versioned grammar for H1 completion predecessor reads.

The registry is deliberately data-only: it tells the installed owner which
physical observations must be resolved, but it cannot resolve them or turn a
caller supplied list into authority.  Its exact canonical bytes are retained
by V2 issuance so historical verification never substitutes newer code for an
older policy revision.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

_SCHEMA = "chiplog.h1-completion-read-plan-registry.v1"
_VERSION = 1
_HEAD_PREFIX = "h1-completion-read-plan"
_Presence = Literal["PRESENT", "ABSENT"]


class H1CompletionReadPlanRegistryError(ValueError):
    """Registry bytes do not name one known closed H1 read plan."""


@dataclass(frozen=True, slots=True)
class H1CompletionReadPlanRole:
    """One fixed selector role; values are descriptions, never caller keys."""

    name: str
    presence: _Presence
    owner: str
    record_kind: str
    subject: str

    def as_json(self) -> dict[str, str]:
        return {
            "name": self.name,
            "owner": self.owner,
            "presence": self.presence,
            "record_kind": self.record_kind,
            "subject": self.subject,
        }


_ROLES: tuple[H1CompletionReadPlanRole, ...] = (
    H1CompletionReadPlanRole("sealed_run", "PRESENT", "agent_loop", "Run", "selected_seal"),
    H1CompletionReadPlanRole(
        "selected_seal", "PRESENT", "agent_loop", "ResponseSeal", "selected_seal"
    ),
    H1CompletionReadPlanRole(
        "recovery_frontier_registry",
        "PRESENT",
        "agent_loop",
        "RecoveryFrontierRegistry",
        "selected_seal",
    ),
    H1CompletionReadPlanRole("run_completion", "ABSENT", "agent_loop", "RunCompletion", "run_id"),
)


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise H1CompletionReadPlanRegistryError("registry has a duplicate JSON key")
        result[key] = value
    return result


@dataclass(frozen=True, slots=True)
class H1CompletionReadPlanRegistry:
    schema: str
    version: int
    roles: tuple[H1CompletionReadPlanRole, ...]
    canonical_bytes: bytes
    fingerprint: str
    head: str


def _from_parts(
    *, schema: str, version: int, roles: tuple[H1CompletionReadPlanRole, ...]
) -> H1CompletionReadPlanRegistry:
    value = {"roles": [role.as_json() for role in roles], "schema": schema, "version": version}
    canonical_bytes = _canonical(value)
    fingerprint = hashlib.sha256(canonical_bytes).hexdigest()
    return H1CompletionReadPlanRegistry(
        schema=schema,
        version=version,
        roles=roles,
        canonical_bytes=canonical_bytes,
        fingerprint=fingerprint,
        head=f"{_HEAD_PREFIX}:{version}:{fingerprint}",
    )


_CURRENT = _from_parts(schema=_SCHEMA, version=_VERSION, roles=_ROLES)


def current_registry() -> H1CompletionReadPlanRegistry:
    """Return the one installed revision accepted for fresh H1 capture."""
    return _CURRENT


def decode_registry(canonical_bytes: bytes) -> H1CompletionReadPlanRegistry:
    """Strictly decode exactly one known registry revision for historical replay."""
    if type(canonical_bytes) is not bytes or not canonical_bytes:
        raise H1CompletionReadPlanRegistryError("registry bytes are absent")
    try:
        value = json.loads(canonical_bytes.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as error:
        raise H1CompletionReadPlanRegistryError("registry bytes are invalid JSON") from error
    if not isinstance(value, dict) or _canonical(value) != canonical_bytes:
        raise H1CompletionReadPlanRegistryError("registry bytes are noncanonical")
    if set(value) != {"schema", "version", "roles"}:
        raise H1CompletionReadPlanRegistryError("registry envelope differs")
    schema, version, raw_roles = value["schema"], value["version"], value["roles"]
    if schema != _SCHEMA or type(version) is not int or version != _VERSION:
        raise H1CompletionReadPlanRegistryError("registry revision is not installed")
    if not isinstance(raw_roles, list) or len(raw_roles) != len(_ROLES):
        raise H1CompletionReadPlanRegistryError("registry role count differs")
    roles: list[H1CompletionReadPlanRole] = []
    for raw, expected in zip(raw_roles, _ROLES, strict=True):
        if (
            not isinstance(raw, dict)
            or set(raw) != {"name", "owner", "presence", "record_kind", "subject"}
            or any(type(item) is not str for item in raw.values())
        ):
            raise H1CompletionReadPlanRegistryError("registry role envelope differs")
        role = H1CompletionReadPlanRole(
            raw["name"], raw["presence"], raw["owner"], raw["record_kind"], raw["subject"]
        )
        if role != expected:
            raise H1CompletionReadPlanRegistryError("registry role differs from installed order")
        roles.append(role)
    registry = _from_parts(schema=schema, version=version, roles=tuple(roles))
    if registry.canonical_bytes != canonical_bytes:
        raise H1CompletionReadPlanRegistryError("registry bytes do not round-trip")
    return registry


def require_registry_identity(
    *, canonical_bytes: bytes, expected_head: str, expected_fingerprint: str
) -> H1CompletionReadPlanRegistry:
    """Bind retained bytes to both recorded identity fields before replay."""
    registry = decode_registry(canonical_bytes)
    if (
        type(expected_head) is not str
        or type(expected_fingerprint) is not str
        or registry.head != expected_head
        or registry.fingerprint != expected_fingerprint
    ):
        raise H1CompletionReadPlanRegistryError("registry identity differs from retained bytes")
    return registry
