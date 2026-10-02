"""Authenticated physical lineage for durable native one-call output-scope records."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead
from chiplog.capabilities.deployment_trust.native_one_call_scope_contracts import (
    NativeOneCallContextScopeAnchorV1,
    NativeOneCallContextScopeV1,
)

_RECORD_TYPE = "chiplog.deployment_trust.native_one_call_context_scope"
_SCHEMA = "chiplog.deployment_trust.record.v1"


@dataclass(frozen=True, slots=True)
class AuthenticatedNativeOneCallContextScope:
    """A scope whose decision and materialized record were authenticated together."""

    decision_id: str
    decision_bytes: bytes
    record_ordinal: int
    record_bytes: bytes
    scope: NativeOneCallContextScopeV1

    @property
    def scope_head(self) -> ExactHead:
        fingerprint = hashlib.sha256(self.scope.canonical_bytes()).hexdigest()
        return ExactHead(
            identity=self.scope.scope_id,
            head=self.scope.scope_id + "/" + fingerprint,
            fingerprint=fingerprint,
        )

    @property
    def decision(self) -> ExactHead:
        return ExactHead(
            identity="deployment-trust/journal",
            head=self.decision_id,
            fingerprint=hashlib.sha256(self.decision_bytes).hexdigest(),
        )

    @property
    def anchor(self) -> NativeOneCallContextScopeAnchorV1:
        record_identity = f"trust-record:{self.decision_id}:{self.record_ordinal}"
        return NativeOneCallContextScopeAnchorV1(
            owner_id="deployment_trust",
            decision=self.decision,
            record=ExactHead(
                identity=record_identity,
                head=record_identity + "/" + hashlib.sha256(self.record_bytes).hexdigest(),
                fingerprint=hashlib.sha256(self.record_bytes).hexdigest(),
            ),
            scope_revision=self.scope.revision,
            predecessor=self.scope.predecessor,
            selected_resource_observation_ref=self.scope.selected_resource_observation_ref,
        )


def authenticated_native_one_call_scope_lineage(
    entries: tuple[tuple[str, str | None, bytes], ...],
    record_at: Callable[[str, int], bytes | None],
) -> tuple[AuthenticatedNativeOneCallContextScope, ...]:
    """Validate every native one-call scope issuance and return its physical authenticated anchors.

    The caller has already authenticated the complete trust observation while
    holding its AuthorityGate.  This scan deliberately rebinds each native one-call payload
    to its physical envelope and exact scope materialization before admitting
    any result as lineage evidence.
    """
    result: list[AuthenticatedNativeOneCallContextScope] = []
    latest: dict[tuple[str, str], AuthenticatedNativeOneCallContextScope] = {}
    for decision_id, _, decision_bytes in entries:
        try:
            envelope = json.loads(decision_bytes)
        except (TypeError, json.JSONDecodeError) as error:
            raise RuntimeError("native one-call scope lineage decision is malformed") from error
        if (
            not isinstance(envelope, dict)
            or envelope.get("kind") != "NATIVE_ONE_CALL_CONTEXT_SCOPE_V1"
        ):
            continue
        payload = envelope.get("payload")
        if not isinstance(payload, dict) or set(payload) != {"scope"}:
            raise RuntimeError("native one-call scope lineage payload is malformed")
        scope_value = payload["scope"]
        if not isinstance(scope_value, dict):
            raise RuntimeError("native one-call scope lineage payload is malformed")
        scope_bytes = json.dumps(scope_value, sort_keys=True, separators=(",", ":")).encode()
        try:
            scope = NativeOneCallContextScopeV1.model_validate_json(scope_bytes)
        except ValueError as error:
            raise RuntimeError("native one-call scope lineage scope is malformed") from error
        if scope.canonical_bytes() != scope_bytes:
            raise RuntimeError("native one-call scope lineage scope is not canonical")
        record = record_at(decision_id, 1)
        expected_record = _canonical_record(decision_id, scope_value)
        if record != expected_record:
            raise RuntimeError("native one-call scope lineage materialized record differs")
        authenticated = AuthenticatedNativeOneCallContextScope(
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
                raise RuntimeError("native one-call scope lineage genesis differs")
        elif (
            scope.revision != predecessor.scope.revision + 1
            or scope.predecessor != predecessor.decision
        ):
            raise RuntimeError("native one-call scope lineage successor differs")
        latest[key] = authenticated
        result.append(authenticated)
    return tuple(result)


def _canonical_record(decision_id: str, scope_value: dict[str, object]) -> bytes:
    return json.dumps(
        {
            "decision_id": decision_id,
            "operation_kind": "NATIVE_ONE_CALL_CONTEXT_SCOPE_V1",
            "record_type_id": _RECORD_TYPE,
            "schema_id": _SCHEMA,
            "scope": scope_value,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


__all__ = ["AuthenticatedNativeOneCallContextScope", "authenticated_native_one_call_scope_lineage"]
