"""Authenticated physical lineage for prepared self-delivery policies.

The caller must first authenticate the complete trust-entry sequence and its
materialization while holding the shared ``AuthorityGate``.  This scanner does
not verify Ed25519 signatures, operator-key provenance, or key currentness; it
only rebinds already-authenticated bytes to their structural policy lineage.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead
from chiplog.capabilities.deployment_trust.operator_policy_authorization_contracts import (
    RetainedOperatorPolicyAuthorizationSourceV1,
    SignedOperatorPolicyAuthorizationV1,
)
from chiplog.capabilities.deployment_trust.prepared_external_delivery_policy_contracts import (
    IssuePreparedExternalSelfDeliveryPolicyRequestV1,
    PreparedExternalSelfDeliveryPolicyAnchorV1,
    PreparedExternalSelfDeliveryPolicyV1,
    RevokePreparedExternalSelfDeliveryPolicyRequestV1,
)
from chiplog.capabilities.deployment_trust.prepared_external_delivery_policy_owner_contracts import (  # noqa: E501
    prepared_self_delivery_policy_content_head,
    prepared_self_delivery_policy_request_content_head,
)

_KIND = "PREPARED_SELF_DELIVERY_POLICY_V1"
_RECORD_TYPE = "chiplog.deployment_trust.prepared_self_delivery_policy"
_SCHEMA = "chiplog.deployment_trust.record.v1"


@dataclass(frozen=True, slots=True)
class AuthenticatedPreparedSelfDeliveryPolicy:
    """One policy revision rebound to its exact journal and record envelopes."""

    decision_id: str
    decision_bytes: bytes
    record_ordinal: int
    record_bytes: bytes
    operator_source: RetainedOperatorPolicyAuthorizationSourceV1
    policy: PreparedExternalSelfDeliveryPolicyV1

    @property
    def decision(self) -> ExactHead:
        return ExactHead(
            identity="deployment-trust/journal",
            head=self.decision_id,
            fingerprint=hashlib.sha256(self.decision_bytes).hexdigest(),
        )

    @property
    def anchor(self) -> PreparedExternalSelfDeliveryPolicyAnchorV1:
        record_identity = f"trust-record:{self.decision_id}:{self.record_ordinal}"
        policy_bytes = self.policy.canonical_bytes()
        return PreparedExternalSelfDeliveryPolicyAnchorV1(
            owner_id="deployment_trust",
            decision=self.decision,
            record_ordinal=self.record_ordinal,
            record_type_id="chiplog.deployment_trust.prepared_self_delivery_policy",
            schema_id="chiplog.deployment_trust.record.v1",
            record=ExactHead(
                identity=record_identity,
                head=record_identity + "/" + hashlib.sha256(self.record_bytes).hexdigest(),
                fingerprint=hashlib.sha256(self.record_bytes).hexdigest(),
            ),
            policy=ExactHead(
                identity=self.policy.policy_id,
                head=prepared_self_delivery_policy_content_head(policy_bytes),
                fingerprint=hashlib.sha256(policy_bytes).hexdigest(),
            ),
            revision=self.policy.revision,
        )


def authenticated_prepared_self_delivery_policy_lineage(
    entries: tuple[tuple[str, str | None, bytes], ...],
    record_at: Callable[[str, int], bytes | None],
) -> tuple[AuthenticatedPreparedSelfDeliveryPolicy, ...]:
    """Return every structurally valid, physically rebound policy revision.

    ``entries`` and ``record_at`` are deliberately read-only inputs.  Their
    provenance and completeness are the caller's AuthorityGate responsibility.
    """
    result: list[AuthenticatedPreparedSelfDeliveryPolicy] = []
    latest: dict[tuple[str, str, str], AuthenticatedPreparedSelfDeliveryPolicy] = {}
    used_commands: set[str] = set()
    used_sources: set[str] = set()

    for decision_id, _physical_predecessor, decision_bytes in entries:
        envelope = _envelope(decision_bytes)
        if envelope.get("kind") != _KIND:
            continue
        payload = envelope.get("payload")
        if not isinstance(payload, dict) or set(payload) != {"operator_source", "policy"}:
            raise RuntimeError("prepared self-delivery policy payload is malformed")
        source_value, policy_value = payload["operator_source"], payload["policy"]
        if not isinstance(source_value, dict) or not isinstance(policy_value, dict):
            raise RuntimeError("prepared self-delivery policy payload is malformed")
        source = _source(source_value)
        policy = _policy(policy_value)

        record = record_at(decision_id, 1)
        expected_record = _canonical_record(decision_id, payload)
        if record != expected_record:
            raise RuntimeError("prepared self-delivery policy materialized record differs")

        authenticated = AuthenticatedPreparedSelfDeliveryPolicy(
            decision_id=decision_id,
            decision_bytes=decision_bytes,
            record_ordinal=1,
            record_bytes=record,
            operator_source=source,
            policy=policy,
        )
        signed = SignedOperatorPolicyAuthorizationV1.model_validate_json(
            source.canonical_source_bytes
        )
        command_id = signed.payload.command_id
        source_id = signed.payload.source_id
        if command_id in used_commands or source_id in used_sources:
            raise RuntimeError("prepared self-delivery policy command or source is replayed")
        used_commands.add(command_id)
        used_sources.add(source_id)

        key = (policy.tenant_id, policy.database_id, policy.policy_id)
        previous = latest.get(key)
        request = _request(signed)
        _check_derived_policy(request, source, policy, previous)
        latest[key] = authenticated
        result.append(authenticated)
    return tuple(result)


def _envelope(decision_bytes: bytes) -> dict[str, object]:
    try:
        envelope = json.loads(decision_bytes)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("prepared self-delivery policy decision is malformed") from error
    if not isinstance(envelope, dict):
        raise RuntimeError("prepared self-delivery policy decision is malformed")
    return envelope


def _source(value: dict[str, object]) -> RetainedOperatorPolicyAuthorizationSourceV1:
    source_bytes = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    try:
        source = RetainedOperatorPolicyAuthorizationSourceV1.model_validate_json(source_bytes)
    except ValueError as error:
        raise RuntimeError("prepared self-delivery operator source is malformed") from error
    if source.model_dump(mode="json") != value:
        raise RuntimeError("prepared self-delivery operator source is not canonical")
    return source


def _policy(value: dict[str, object]) -> PreparedExternalSelfDeliveryPolicyV1:
    policy_bytes = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    try:
        policy = PreparedExternalSelfDeliveryPolicyV1.model_validate_json(policy_bytes)
    except ValueError as error:
        raise RuntimeError("prepared self-delivery policy is malformed") from error
    if policy.canonical_bytes() != policy_bytes:
        raise RuntimeError("prepared self-delivery policy is not canonical")
    return policy


def _request(
    signed: SignedOperatorPolicyAuthorizationV1,
) -> (
    IssuePreparedExternalSelfDeliveryPolicyRequestV1
    | RevokePreparedExternalSelfDeliveryPolicyRequestV1
):
    raw = signed.payload.canonical_request_bytes
    try:
        request: (
            IssuePreparedExternalSelfDeliveryPolicyRequestV1
            | RevokePreparedExternalSelfDeliveryPolicyRequestV1
        )
        if signed.payload.operation == "ISSUE_PREPARED_EXTERNAL_SELF_DELIVERY_POLICY":
            request = IssuePreparedExternalSelfDeliveryPolicyRequestV1.model_validate_json(raw)
        else:
            request = RevokePreparedExternalSelfDeliveryPolicyRequestV1.model_validate_json(raw)
    except ValueError as error:
        raise RuntimeError("prepared self-delivery signed request is malformed") from error
    if request.canonical_bytes() != raw:
        raise RuntimeError("prepared self-delivery signed request is not canonical")
    return request


def _check_derived_policy(
    request: IssuePreparedExternalSelfDeliveryPolicyRequestV1
    | RevokePreparedExternalSelfDeliveryPolicyRequestV1,
    source: RetainedOperatorPolicyAuthorizationSourceV1,
    policy: PreparedExternalSelfDeliveryPolicyV1,
    previous: AuthenticatedPreparedSelfDeliveryPolicy | None,
) -> None:
    if previous is None:
        if policy.revision != 0 or policy.predecessor is not None:
            raise RuntimeError("prepared self-delivery policy genesis differs")
        if request.expected_policy is not None:
            raise RuntimeError("prepared self-delivery policy genesis CAS differs")
    else:
        if (
            policy.revision != previous.policy.revision + 1
            or policy.predecessor != previous.anchor.policy
            or request.expected_policy != previous.anchor
        ):
            raise RuntimeError("prepared self-delivery policy successor CAS differs")

    if isinstance(request, IssuePreparedExternalSelfDeliveryPolicyRequestV1):
        expected_terms = request.terms
        expected_status: Literal["ACTIVE", "REVOKED"] = "ACTIVE"
    else:
        if previous is None:
            raise RuntimeError("prepared self-delivery revocation lacks a predecessor")
        expected_terms = previous.policy.terms
        expected_status = "REVOKED"
    request_bytes = request.canonical_bytes()
    expected = PreparedExternalSelfDeliveryPolicyV1(
        issuer="deployment_trust",
        tenant_id=request.tenant_id,
        database_id=request.database_id,
        policy_id=request.policy_id,
        revision=0 if previous is None else previous.policy.revision + 1,
        predecessor=None if request.expected_policy is None else request.expected_policy.policy,
        status=expected_status,
        terms=expected_terms,
        authorization_command=ExactHead(
            identity=request.command_id,
            head=prepared_self_delivery_policy_request_content_head(request_bytes),
            fingerprint=hashlib.sha256(request_bytes).hexdigest(),
        ),
        authorization_source=source.ref,
    )
    if policy != expected:
        raise RuntimeError("prepared self-delivery policy differs from signed request")


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
    "AuthenticatedPreparedSelfDeliveryPolicy",
    "authenticated_prepared_self_delivery_policy_lineage",
]
