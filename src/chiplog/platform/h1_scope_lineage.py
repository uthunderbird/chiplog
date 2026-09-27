"""Authenticated physical lineage for durable H1 output-scope records."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead
from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
    HermeticOutputScopeAnchorV1,
    HermeticOutputScopeV1,
)

_RECORD_TYPE = "chiplog.deployment_trust.hermetic_output_scope"
_SCHEMA = "chiplog.deployment_trust.record.v1"


@dataclass(frozen=True, slots=True)
class AuthenticatedHermeticOutputScope:
    """A scope whose decision and materialized record were authenticated together."""

    decision_id: str
    decision_bytes: bytes
    record_ordinal: int
    record_bytes: bytes
    scope: HermeticOutputScopeV1

    @property
    def decision(self) -> ExactHead:
        return ExactHead(
            identity="deployment-trust/journal",
            head=self.decision_id,
            fingerprint=hashlib.sha256(self.decision_bytes).hexdigest(),
        )

    @property
    def anchor(self) -> HermeticOutputScopeAnchorV1:
        record_identity = f"trust-record:{self.decision_id}:{self.record_ordinal}"
        return HermeticOutputScopeAnchorV1(
            owner_id="deployment_trust",
            decision=self.decision,
            record_ordinal=self.record_ordinal,
            record_type_id=_RECORD_TYPE,
            schema_id=_SCHEMA,
            record=ExactHead(
                identity=record_identity,
                head=record_identity + "/" + hashlib.sha256(self.record_bytes).hexdigest(),
                fingerprint=hashlib.sha256(self.record_bytes).hexdigest(),
            ),
            scope_revision=self.scope.revision,
            predecessor=self.scope.predecessor,
            selected_resource_observation_ref=self.scope.selected_resource_observation_ref,
        )


def authenticated_h1_scope_lineage(
    entries: tuple[tuple[str, str | None, bytes], ...],
    record_at: Callable[[str, int], bytes | None],
) -> tuple[AuthenticatedHermeticOutputScope, ...]:
    """Validate every H1 scope issuance and return its physical authenticated anchors.

    The caller has already authenticated the complete trust observation while
    holding its AuthorityGate.  This scan deliberately rebinds each H1 payload
    to its physical envelope and exact scope materialization before admitting
    any result as lineage evidence.
    """
    result: list[AuthenticatedHermeticOutputScope] = []
    latest: dict[tuple[str, str], AuthenticatedHermeticOutputScope] = {}
    for decision_id, _, decision_bytes in entries:
        try:
            envelope = json.loads(decision_bytes)
        except (TypeError, json.JSONDecodeError) as error:
            raise RuntimeError("H1 scope lineage decision is malformed") from error
        if not isinstance(envelope, dict) or envelope.get("kind") != "HERMETIC_OUTPUT_SCOPE_V1":
            continue
        payload = envelope.get("payload")
        if not isinstance(payload, dict) or set(payload) != {"scope"}:
            raise RuntimeError("H1 scope lineage payload is malformed")
        scope_value = payload["scope"]
        if not isinstance(scope_value, dict):
            raise RuntimeError("H1 scope lineage payload is malformed")
        scope_bytes = json.dumps(scope_value, sort_keys=True, separators=(",", ":")).encode()
        try:
            scope = HermeticOutputScopeV1.model_validate_json(scope_bytes)
        except ValueError as error:
            raise RuntimeError("H1 scope lineage scope is malformed") from error
        if scope.canonical_bytes() != scope_bytes:
            raise RuntimeError("H1 scope lineage scope is not canonical")
        record = record_at(decision_id, 1)
        expected_record = _canonical_record(decision_id, scope_value)
        if record != expected_record:
            raise RuntimeError("H1 scope lineage materialized record differs")
        authenticated = AuthenticatedHermeticOutputScope(
            decision_id=decision_id,
            decision_bytes=decision_bytes,
            record_ordinal=1,
            record_bytes=record,
            scope=scope,
        )
        key = (scope.database_id, scope.scope_id)
        predecessor = latest.get(key)
        if predecessor is None:
            if scope.revision != 0 or scope.predecessor is not None:
                raise RuntimeError("H1 scope lineage genesis differs")
        elif (
            scope.revision != predecessor.scope.revision + 1
            or scope.predecessor != predecessor.decision
        ):
            raise RuntimeError("H1 scope lineage successor differs")
        latest[key] = authenticated
        result.append(authenticated)
    return tuple(result)


def _canonical_record(decision_id: str, scope_value: dict[str, object]) -> bytes:
    return json.dumps(
        {
            "decision_id": decision_id,
            "operation_kind": "HERMETIC_OUTPUT_SCOPE_V1",
            "record_type_id": _RECORD_TYPE,
            "schema_id": _SCHEMA,
            "scope": scope_value,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


__all__ = ["AuthenticatedHermeticOutputScope", "authenticated_h1_scope_lineage"]
