"""Authenticated physical lineage for prepared external-delivery V2 grants."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead
from chiplog.capabilities.deployment_trust._j7_grant_process import (
    derive_grant,
    request_from_source,
    retained_source,
)
from chiplog.capabilities.deployment_trust.operator_grant_authorization_contracts import (
    RetainedOperatorGrantAuthorizationSourceV2,
    SignedOperatorGrantAuthorizationV2,
)
from chiplog.capabilities.deployment_trust.prepared_external_delivery_contracts import (
    PreparedExternalDeliveryGrantAnchorV2,
    PreparedExternalDeliveryGrantV2,
)
from chiplog.capabilities.deployment_trust.prepared_external_delivery_grant_owner_contracts import (
    AuthorizePreparedExternalDeliveryGrantCallV2,
    PreparedExternalDeliveryGrantProposalV2,
)

_KIND = "PREPARED_EXTERNAL_DELIVERY_GRANT_V2"
_RECORD_TYPE = "chiplog.deployment_trust.prepared_external_delivery_grant"
_SCHEMA = "chiplog.deployment_trust.record.v1"


@dataclass(frozen=True, slots=True)
class AuthenticatedPreparedExternalDeliveryGrant:
    """One exactly retained V2 grant revision and its physical envelopes."""

    decision_id: str
    decision_bytes: bytes
    record_ordinal: int
    record_bytes: bytes
    call: AuthorizePreparedExternalDeliveryGrantCallV2
    operator_source: RetainedOperatorGrantAuthorizationSourceV2
    grant: PreparedExternalDeliveryGrantV2

    @property
    def decision(self) -> ExactHead:
        return ExactHead(
            identity="deployment-trust/journal",
            head=self.decision_id,
            fingerprint=hashlib.sha256(self.decision_bytes).hexdigest(),
        )

    @property
    def anchor(self) -> PreparedExternalDeliveryGrantAnchorV2:
        record_identity = f"trust-record:{self.decision_id}:{self.record_ordinal}"
        grant_bytes = self.grant.canonical_bytes()
        from chiplog.capabilities.deployment_trust.prepared_external_delivery_contracts import (
            prepared_external_delivery_grant_content_head_v2,
        )

        return PreparedExternalDeliveryGrantAnchorV2(
            owner_id="deployment_trust",
            decision=self.decision,
            record_ordinal=self.record_ordinal,
            record_type_id="chiplog.deployment_trust.prepared_external_delivery_grant",
            schema_id="chiplog.deployment_trust.record.v1",
            record=ExactHead(
                identity=record_identity,
                head=record_identity + "/" + hashlib.sha256(self.record_bytes).hexdigest(),
                fingerprint=hashlib.sha256(self.record_bytes).hexdigest(),
            ),
            grant=ExactHead(
                identity=self.grant.grant_id,
                head=prepared_external_delivery_grant_content_head_v2(grant_bytes),
                fingerprint=hashlib.sha256(grant_bytes).hexdigest(),
            ),
            revision=self.grant.revision,
        )


def authenticated_prepared_external_delivery_grant_lineage(
    entries: tuple[tuple[str, str | None, bytes], ...],
    record_at: Callable[[str, int], bytes | None],
) -> tuple[AuthenticatedPreparedExternalDeliveryGrant, ...]:
    """Rebind retained ISSUE preimages to exact journal and SQLite evidence.

    Provenance, signature verification, and live pin currentness are deliberately
    outside this pure scanner.  Callers authenticate the complete physical source
    while holding their AuthorityGate before invoking it.
    """
    result: list[AuthenticatedPreparedExternalDeliveryGrant] = []
    latest: dict[tuple[str, str, str], AuthenticatedPreparedExternalDeliveryGrant] = {}
    used_commands: set[str] = set()
    used_sources: set[str] = set()
    for decision_id, _physical_predecessor, decision_bytes in entries:
        envelope = _envelope(decision_bytes)
        if envelope.get("kind") != _KIND:
            continue
        payload = envelope.get("payload")
        if not isinstance(payload, dict) or set(payload) != {"call", "operator_source", "grant"}:
            raise RuntimeError("prepared external delivery grant payload is malformed")
        call_value, source_value, grant_value = (
            payload["call"],
            payload["operator_source"],
            payload["grant"],
        )
        if not all(isinstance(value, dict) for value in (call_value, source_value, grant_value)):
            raise RuntimeError("prepared external delivery grant payload is malformed")
        call = _call(call_value)
        source = _source(source_value)
        grant = _grant(grant_value)
        record = record_at(decision_id, 1)
        if record != _canonical_record(decision_id, payload):
            raise RuntimeError("prepared external delivery grant materialized record differs")
        authenticated = AuthenticatedPreparedExternalDeliveryGrant(
            decision_id=decision_id,
            decision_bytes=decision_bytes,
            record_ordinal=1,
            record_bytes=record,
            call=call,
            operator_source=source,
            grant=grant,
        )
        signed = SignedOperatorGrantAuthorizationV2.model_validate_json(
            source.canonical_source_bytes
        )
        command_id, source_id = signed.payload.command_id, signed.payload.source_id
        if command_id in used_commands or source_id in used_sources:
            raise RuntimeError("prepared external delivery grant command or source is replayed")
        used_commands.add(command_id)
        used_sources.add(source_id)
        key = (grant.tenant_id, grant.database_id, grant.grant_id)
        _check_derived_grant(call, source, grant, latest.get(key))
        latest[key] = authenticated
        result.append(authenticated)
    return tuple(result)


def _envelope(raw: bytes) -> dict[str, object]:
    try:
        value = json.loads(raw)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("prepared external delivery grant decision is malformed") from error
    if not isinstance(value, dict):
        raise RuntimeError("prepared external delivery grant decision is malformed")
    return value


def _canonical_model(value: dict[str, object], model: type[object], label: str) -> object:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    try:
        parsed = model.model_validate_json(raw)  # type: ignore[attr-defined]
    except ValueError as error:
        raise RuntimeError(f"prepared external delivery {label} is malformed") from error
    if parsed.canonical_bytes() != raw:
        raise RuntimeError(f"prepared external delivery {label} is not canonical")
    return parsed


def _call(value: dict[str, object]) -> AuthorizePreparedExternalDeliveryGrantCallV2:
    return cast(
        AuthorizePreparedExternalDeliveryGrantCallV2,
        _canonical_model(value, AuthorizePreparedExternalDeliveryGrantCallV2, "grant call"),
    )


def _source(value: dict[str, object]) -> RetainedOperatorGrantAuthorizationSourceV2:
    return cast(
        RetainedOperatorGrantAuthorizationSourceV2,
        _canonical_model(value, RetainedOperatorGrantAuthorizationSourceV2, "operator source"),
    )


def _grant(value: dict[str, object]) -> PreparedExternalDeliveryGrantV2:
    return cast(
        PreparedExternalDeliveryGrantV2,
        _canonical_model(value, PreparedExternalDeliveryGrantV2, "grant"),
    )


def _check_derived_grant(
    call: AuthorizePreparedExternalDeliveryGrantCallV2,
    source: RetainedOperatorGrantAuthorizationSourceV2,
    grant: PreparedExternalDeliveryGrantV2,
    previous: AuthenticatedPreparedExternalDeliveryGrant | None,
) -> None:
    try:
        if previous is not None:
            raise ValueError("grant successor issuance is unsupported")
        if source != retained_source(call.canonical_signed_source_bytes):
            raise ValueError("retained source differs from pinned call")
        request = request_from_source(call.canonical_signed_source_bytes)
        if request.expected_grant is not None:
            raise ValueError("grant issuance must be genesis")
        expected_previous = None if previous is None else previous.grant
        if request.expected_grant != (None if previous is None else previous.anchor):
            raise ValueError("grant request CAS differs")
        if call.latest_grant_anchor != (None if previous is None else previous.anchor):
            raise ValueError("pinned grant anchor CAS differs")
        if call.latest_grant_bytes != (
            None if expected_previous is None else expected_previous.canonical_bytes()
        ):
            raise ValueError("pinned grant bytes CAS differs")
        expected = derive_grant(request, source.ref, call)
        if grant != expected:
            raise ValueError("grant differs from signed request")
        proposal = PreparedExternalDeliveryGrantProposalV2(
            call_sha256=hashlib.sha256(call.canonical_bytes()).hexdigest(),
            operator_source=source,
            evidence=call.evidence,
            grant=grant,
        )
        proposal.check_pinned_call(call)
    except Exception as error:
        raise RuntimeError("prepared external delivery grant derivation differs") from error


def _canonical_record(decision_id: str, payload: dict[str, object]) -> bytes:
    return json.dumps(
        {
            "decision_id": decision_id,
            "operation_kind": _KIND,
            "record_type_id": _RECORD_TYPE,
            "schema_id": _SCHEMA,
            **payload,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


__all__ = [
    "AuthenticatedPreparedExternalDeliveryGrant",
    "authenticated_prepared_external_delivery_grant_lineage",
]
