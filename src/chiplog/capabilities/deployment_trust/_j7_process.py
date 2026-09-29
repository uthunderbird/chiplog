"""Private J7 owner evaluator for signed prepared self-delivery policies.

The dispatcher closes over an operator binding derived from the protected
broker pin at startup.  It has no pin path, gate, journal, or reload capability:
the broker supplies an authenticated snapshot and repeats the physical-policy
CAS when it appends a proposal.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Callable
from typing import Literal

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead

from .operator_policy_authorization_contracts import (
    RetainedOperatorPolicyAuthorizationSourceV1,
    SignedOperatorPolicyAuthorizationV1,
    operator_policy_source_content_head,
)
from .operator_policy_command_verifier import (
    OperatorPolicyKeyBindingV1,
    OperatorPolicyVerificationError,
    verify_operator_policy_command,
)
from .prepared_external_delivery_policy_contracts import (
    IssuePreparedExternalSelfDeliveryPolicyRequestV1,
    IssuePreparedExternalSelfDeliveryPolicyV1,
    PreparedExternalSelfDeliveryPolicyV1,
    RevokePreparedExternalSelfDeliveryPolicyRequestV1,
    RevokePreparedExternalSelfDeliveryPolicyV1,
)
from .prepared_external_delivery_policy_owner_contracts import (
    AuthorizePreparedSelfDeliveryPolicyCallV1,
    PreparedSelfDeliveryPolicyProposalV1,
    PreparedSelfDeliveryPolicyRejectedV1,
    prepared_self_delivery_policy_request_content_head,
)

_OPERATION = "deployment_trust.authorize_prepared_external_self_delivery_policy"
_ROUTE = (
    _OPERATION,
    "broker",
    "deployment_trust",
    "chiplog.deployment-trust.authorize-self-delivery-policy-call.v1",
    "chiplog.deployment-trust.self-delivery-policy-result.v1",
)
_MAX_CALL_BYTES = 262_144
type _Request = (
    IssuePreparedExternalSelfDeliveryPolicyRequestV1
    | RevokePreparedExternalSelfDeliveryPolicyRequestV1
)


def make_dispatch(
    operator_policy_key_binding_bytes: bytes | None,
) -> Callable[[str, bytes], dict[str, object]]:
    """Freeze one canonical startup binding and return the owner dispatcher.

    ``None`` represents an owner started without a provisioned pin.  It keeps
    legacy routes usable while making every otherwise valid J7 request DENIED.
    """
    binding = _startup_binding(operator_policy_key_binding_bytes)

    def dispatch(operation: str, payload: bytes) -> dict[str, object]:
        if operation != _OPERATION:
            return {"failure": "UNAVAILABLE", "reason": "J7 operation has no handler"}
        try:
            if len(payload) > _MAX_CALL_BYTES:
                raise ValueError("J7 owner call exceeds bound")
            call = AuthorizePreparedSelfDeliveryPolicyCallV1.model_validate_json(payload)
            if call.canonical_bytes() != payload:
                raise ValueError("J7 owner call is not canonical")
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            return {"failure": "PROTOCOL_REJECTED", "reason": str(error)}

        result = _evaluate(call, binding)
        return {
            "payload": base64.b64encode(result.canonical_bytes()).decode("ascii"),
            "schema_id": _ROUTE[4],
        }

    return dispatch


def _startup_binding(raw: bytes | None) -> OperatorPolicyKeyBindingV1 | None:
    if raw is None:
        return None
    try:
        binding = OperatorPolicyKeyBindingV1.model_validate_json(raw)
        if binding.canonical_bytes() != raw:
            raise ValueError("operator policy key binding is not canonical")
        # Reconstruct from the immutable canonical representation before the
        # audit hook is installed; retain no caller-owned model instance.
        return OperatorPolicyKeyBindingV1.model_validate_json(binding.canonical_bytes())
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def _evaluate(
    call: AuthorizePreparedSelfDeliveryPolicyCallV1,
    binding: OperatorPolicyKeyBindingV1 | None,
) -> PreparedSelfDeliveryPolicyProposalV1 | PreparedSelfDeliveryPolicyRejectedV1:
    call_sha256 = hashlib.sha256(call.canonical_bytes()).hexdigest()
    try:
        source = SignedOperatorPolicyAuthorizationV1.model_validate_json(
            call.canonical_signed_source_bytes
        )
        if source.canonical_bytes() != call.canonical_signed_source_bytes:
            raise ValueError("signed source is not canonical")
        request = _request(source)
        if (
            request.expected_trust_observation != call.expected_trust_observation
            or request.expected_policy != call.latest_policy_anchor
        ):
            return _rejected("STALE", call_sha256, "signed-request-cas-mismatch")
        if not _matches_snapshot(call):
            return _rejected("STALE", call_sha256, "trust-snapshot-mismatch")

        prior = _previous_policy(call, request)
        retained_source = _retained_source(call.canonical_signed_source_bytes, source)
        command = _command(request, retained_source.ref)
        verify_operator_policy_command(
            command, retained_source=retained_source, current_binding=binding
        )
        proposal = PreparedSelfDeliveryPolicyProposalV1(
            call_sha256=call_sha256,
            operator_source=retained_source,
            policy=_policy(request, retained_source.ref, prior),
        )
        proposal.check_pinned_call(call)
        return proposal
    except OperatorPolicyVerificationError:
        return _rejected("DENIED", call_sha256, "operator-command-denied")
    except (ValueError, TypeError, KeyError, binascii.Error, json.JSONDecodeError):
        return _rejected("DENIED", call_sha256, "policy-input-denied")


def _request(
    source: SignedOperatorPolicyAuthorizationV1,
) -> _Request:
    payload = source.payload
    if payload.operation == "ISSUE_PREPARED_EXTERNAL_SELF_DELIVERY_POLICY":
        return IssuePreparedExternalSelfDeliveryPolicyRequestV1.model_validate_json(
            payload.canonical_request_bytes
        )
    return RevokePreparedExternalSelfDeliveryPolicyRequestV1.model_validate_json(
        payload.canonical_request_bytes
    )


def _matches_snapshot(call: AuthorizePreparedSelfDeliveryPolicyCallV1) -> bool:
    """Check the canonical logical hash chain available to the isolated owner.

    The broker authenticates its snapshot capture and proves currentness; those
    sources are intentionally unavailable in this process.  This evaluator
    checks only the supplied snapshot's canonical internal consistency.
    """
    entries = json.loads(call.snapshot_bytes)
    if not isinstance(entries, list) or not entries:
        return False
    predecessor: str | None = None
    for entry in entries:
        if (
            not isinstance(entry, list)
            or len(entry) != 3
            or not isinstance(entry[0], str)
            or not entry[0]
            or entry[1] != predecessor
            or not isinstance(entry[2], str)
        ):
            return False
        raw = base64.b64decode(entry[2], validate=True)
        if not raw or base64.b64encode(raw).decode("ascii") != entry[2]:
            return False
        envelope = json.loads(raw)
        if (
            not isinstance(envelope, dict)
            or set(envelope) != {"kind", "payload", "predecessor"}
            or envelope.get("predecessor") != predecessor
            or not isinstance(envelope.get("kind"), str)
            or not isinstance(envelope.get("payload"), dict)
            or _canonical(envelope) != raw
        ):
            return False
        logical_id = hashlib.sha256(
            (predecessor or "GENESIS").encode() + b"\x00" + raw
        ).hexdigest()
        if entry[0] != logical_id:
            return False
        predecessor = logical_id
    return (
        _canonical(entries) == call.snapshot_bytes
        and predecessor == call.expected_trust_observation.logical_snapshot_head
    )


def _previous_policy(
    call: AuthorizePreparedSelfDeliveryPolicyCallV1,
    request: _Request,
) -> PreparedExternalSelfDeliveryPolicyV1 | None:
    if call.latest_policy_bytes is None:
        if isinstance(request, RevokePreparedExternalSelfDeliveryPolicyRequestV1):
            raise ValueError("revocation has no previous policy")
        return None
    prior = PreparedExternalSelfDeliveryPolicyV1.model_validate_json(call.latest_policy_bytes)
    if prior.canonical_bytes() != call.latest_policy_bytes:
        raise ValueError("previous policy is not canonical")
    if (prior.tenant_id, prior.database_id, prior.policy_id) != (
        request.tenant_id,
        request.database_id,
        request.policy_id,
    ):
        raise ValueError("previous policy scope differs from request")
    return prior


def _retained_source(
    raw: bytes, source: SignedOperatorPolicyAuthorizationV1
) -> RetainedOperatorPolicyAuthorizationSourceV1:
    return RetainedOperatorPolicyAuthorizationSourceV1(
        ref=ExactHead(
            identity=source.payload.source_id,
            head=operator_policy_source_content_head(raw),
            fingerprint=hashlib.sha256(raw).hexdigest(),
        ),
        canonical_source_bytes=raw,
    )


def _command(
    request: _Request,
    source_ref: ExactHead,
) -> IssuePreparedExternalSelfDeliveryPolicyV1 | RevokePreparedExternalSelfDeliveryPolicyV1:
    if isinstance(request, IssuePreparedExternalSelfDeliveryPolicyRequestV1):
        return IssuePreparedExternalSelfDeliveryPolicyV1(
            request=request, authenticated_operator_source=source_ref
        )
    return RevokePreparedExternalSelfDeliveryPolicyV1(
        request=request, authenticated_operator_source=source_ref
    )


def _policy(
    request: _Request,
    source_ref: ExactHead,
    prior: PreparedExternalSelfDeliveryPolicyV1 | None,
) -> PreparedExternalSelfDeliveryPolicyV1:
    if isinstance(request, IssuePreparedExternalSelfDeliveryPolicyRequestV1):
        terms = request.terms
        status: Literal["ACTIVE", "REVOKED"] = "ACTIVE"
    else:
        if prior is None:
            raise ValueError("revocation has no previous policy")
        terms = prior.terms
        status = "REVOKED"
    raw_request = request.canonical_bytes()
    return PreparedExternalSelfDeliveryPolicyV1(
        issuer="deployment_trust",
        tenant_id=request.tenant_id,
        database_id=request.database_id,
        policy_id=request.policy_id,
        revision=0 if prior is None else prior.revision + 1,
        predecessor=None if request.expected_policy is None else request.expected_policy.policy,
        status=status,
        terms=terms,
        authorization_command=ExactHead(
            identity=request.command_id,
            head=prepared_self_delivery_policy_request_content_head(raw_request),
            fingerprint=hashlib.sha256(raw_request).hexdigest(),
        ),
        authorization_source=source_ref,
    )


def _rejected(
    disposition: Literal["DENIED", "STALE"], call_sha256: str, reason: str
) -> PreparedSelfDeliveryPolicyRejectedV1:
    return PreparedSelfDeliveryPolicyRejectedV1(
        disposition=disposition, call_sha256=call_sha256, reason=reason
    )


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


ROUTES = (_ROUTE,)
