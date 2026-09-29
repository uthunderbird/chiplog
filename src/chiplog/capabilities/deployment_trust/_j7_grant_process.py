"""Pure J7 evaluator for the first signed V2 grant ISSUE route."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Callable
from typing import Literal

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead, OriginSelection

from .operator_grant_authorization_contracts import (
    RetainedOperatorGrantAuthorizationSourceV2,
    SignedOperatorGrantAuthorizationV2,
    operator_grant_source_content_head_v2,
)
from .operator_grant_command_verifier import (
    OperatorGrantKeyBindingV2,
    OperatorGrantVerificationError,
    verify_operator_grant_request,
)
from .prepared_external_delivery_contracts import (
    BoundedExternalSelfSendMandateV1,
    ExternalDeliveryResourcesV1,
    IssuePreparedExternalDeliveryGrantRequestV2,
    PreparedExternalDeliveryGrantScopeV2,
    PreparedExternalDeliveryGrantV2,
)
from .prepared_external_delivery_grant_owner_contracts import (
    AuthorizePreparedExternalDeliveryGrantCallV2,
    PreparedExternalDeliveryGrantProposalV2,
    PreparedExternalDeliveryGrantRejectedV2,
    PreparedExternalDeliveryGrantResultV2,
    prepared_delivery_basis_head_v3,
    prepared_external_delivery_grant_request_content_head_v2,
)
from .prepared_external_delivery_policy_contracts import PreparedExternalSelfDeliveryPolicyV1

_OPERATION = "deployment_trust.authorize_prepared_external_delivery_grant"
_ROUTE = (
    _OPERATION,
    "broker",
    "deployment_trust",
    "chiplog.deployment-trust.authorize-prepared-external-delivery-grant-call.v2",
    "chiplog.deployment-trust.prepared-external-delivery-grant-result.v2",
)
_MAX_CALL_BYTES = 524_288


def make_dispatch(binding_bytes: bytes | None) -> Callable[[str, bytes], dict[str, object]]:
    """Freeze a canonical protected startup binding; this process performs no I/O."""
    binding = _startup_binding(binding_bytes)

    def dispatch(operation: str, payload: bytes) -> dict[str, object]:
        if operation != _OPERATION:
            return {"failure": "UNAVAILABLE", "reason": "J7 grant operation has no handler"}
        try:
            if len(payload) > _MAX_CALL_BYTES:
                raise ValueError("J7 grant owner call exceeds bound")
            call = AuthorizePreparedExternalDeliveryGrantCallV2.model_validate_json(payload)
            if call.canonical_bytes() != payload:
                raise ValueError("J7 grant owner call is not canonical")
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            return {"failure": "PROTOCOL_REJECTED", "reason": str(error)}
        result = evaluate(call, binding)
        return {
            "payload": base64.b64encode(result.canonical_bytes()).decode("ascii"),
            "schema_id": _ROUTE[4],
        }

    return dispatch


def _startup_binding(raw: bytes | None) -> OperatorGrantKeyBindingV2 | None:
    if raw is None:
        return None
    try:
        binding = OperatorGrantKeyBindingV2.model_validate_json(raw)
        if binding.canonical_bytes() != raw:
            raise ValueError("grant key binding is not canonical")
        return OperatorGrantKeyBindingV2.model_validate_json(binding.canonical_bytes())
    except ValueError, TypeError, json.JSONDecodeError:
        return None


def evaluate(
    call: AuthorizePreparedExternalDeliveryGrantCallV2, binding: OperatorGrantKeyBindingV2 | None
) -> PreparedExternalDeliveryGrantResultV2:
    call_sha256 = hashlib.sha256(call.canonical_bytes()).hexdigest()
    try:
        request = request_from_source(call.canonical_signed_source_bytes)
        if request.expected_trust_observation != call.expected_trust_observation:
            return _rejected("STALE", call_sha256, "signed-request-trust-cas-mismatch")
        if request.expected_grant != call.latest_grant_anchor:
            return _rejected("STALE", call_sha256, "signed-request-grant-cas-mismatch")
        if not _matches_snapshot(
            call.snapshot_bytes, call.expected_trust_observation.logical_snapshot_head
        ):
            return _rejected("STALE", call_sha256, "trust-snapshot-mismatch")
        source = retained_source(call.canonical_signed_source_bytes)
        verify_operator_grant_request(request, retained_source=source, current_binding=binding)
        _validate_issue(request, call)
        proposal = PreparedExternalDeliveryGrantProposalV2(
            call_sha256=call_sha256,
            operator_source=source,
            evidence=call.evidence,
            grant=derive_grant(request, source.ref, call),
        )
        proposal.check_pinned_call(call)
        return proposal
    except OperatorGrantVerificationError:
        return _rejected("DENIED", call_sha256, "operator-command-denied")
    except ValueError, TypeError, KeyError, binascii.Error, json.JSONDecodeError:
        return _rejected("DENIED", call_sha256, "grant-input-denied")


def request_from_source(raw: bytes) -> IssuePreparedExternalDeliveryGrantRequestV2:
    source = SignedOperatorGrantAuthorizationV2.model_validate_json(raw)
    if source.canonical_bytes() != raw:
        raise ValueError("signed grant source is not canonical")
    if source.payload.operation != "ISSUE_PREPARED_EXTERNAL_DELIVERY_GRANT":
        raise ValueError("grant revocation is unsupported on this J7 route")
    request = IssuePreparedExternalDeliveryGrantRequestV2.model_validate_json(
        source.payload.canonical_request_bytes
    )
    if request.canonical_bytes() != source.payload.canonical_request_bytes:
        raise ValueError("signed grant request is not canonical")
    return request


def retained_source(raw: bytes) -> RetainedOperatorGrantAuthorizationSourceV2:
    source = SignedOperatorGrantAuthorizationV2.model_validate_json(raw)
    return RetainedOperatorGrantAuthorizationSourceV2(
        ref=ExactHead(
            identity=source.payload.source_id,
            head=operator_grant_source_content_head_v2(raw),
            fingerprint=hashlib.sha256(raw).hexdigest(),
        ),
        canonical_source_bytes=raw,
    )


def derive_grant(
    request: IssuePreparedExternalDeliveryGrantRequestV2,
    source_ref: ExactHead,
    call: AuthorizePreparedExternalDeliveryGrantCallV2,
) -> PreparedExternalDeliveryGrantV2:
    prior = _prior(call, request)
    raw = request.canonical_bytes()
    return PreparedExternalDeliveryGrantV2(
        issuer="deployment_trust",
        tenant_id=request.tenant_id,
        database_id=request.database_id,
        grant_id=request.grant_id,
        revision=0 if prior is None else prior.revision + 1,
        predecessor=None if request.expected_grant is None else request.expected_grant.grant,
        status="ACTIVE",
        selected_policy_anchor=request.selected_policy_anchor,
        scope=_scope(request, call),
        authorization_command=ExactHead(
            identity=request.command_id,
            head=prepared_external_delivery_grant_request_content_head_v2(raw),
            fingerprint=hashlib.sha256(raw).hexdigest(),
        ),
        authorization_source=source_ref,
    )


def _prior(
    call: AuthorizePreparedExternalDeliveryGrantCallV2,
    request: IssuePreparedExternalDeliveryGrantRequestV2,
) -> PreparedExternalDeliveryGrantV2 | None:
    if call.latest_grant_bytes is None:
        return None
    prior = PreparedExternalDeliveryGrantV2.model_validate_json(call.latest_grant_bytes)
    if prior.canonical_bytes() != call.latest_grant_bytes or (
        prior.tenant_id,
        prior.database_id,
        prior.grant_id,
    ) != (request.tenant_id, request.database_id, request.grant_id):
        raise ValueError("latest grant differs from request scope")
    return prior


def _validate_issue(
    request: IssuePreparedExternalDeliveryGrantRequestV2,
    call: AuthorizePreparedExternalDeliveryGrantCallV2,
) -> None:
    evidence = call.evidence
    derived_scope = _scope(request, call)
    if request.proposed_scope != derived_scope:
        raise ValueError("broker evidence scope differs from signed proposed scope")
    if (evidence.route.tenant_id, evidence.route.database_id) != (
        request.tenant_id,
        request.database_id,
    ):
        raise ValueError("route differs from signed request")
    if request.selected_policy_anchor != call.current_policy_anchor:
        raise ValueError("signed selected policy is not current policy")
    if request.canonical_selected_policy_bytes != call.current_policy_bytes:
        raise ValueError("signed selected policy bytes are not current policy bytes")
    policy = PreparedExternalSelfDeliveryPolicyV1.model_validate_json(call.current_policy_bytes)
    if policy.status != "ACTIVE" or (policy.tenant_id, policy.database_id, policy.policy_id) != (
        request.tenant_id,
        request.database_id,
        request.policy_id,
    ):
        raise ValueError("current active policy differs from request")
    terms, scope = policy.terms, derived_scope
    mandate, resources = scope.mandate, scope.resources
    if (
        not isinstance(evidence.accepted_delivery.selection, OriginSelection)
        or scope.selected_source.source_class != "CLI"
    ):
        raise ValueError("issue requires a CLI origin-exact selection")
    if scope.principal_id != terms.principal_id:
        raise ValueError("policy principal differs from selected scope")
    if evidence.channel_id != terms.channel_id:
        raise ValueError("policy channel differs from selected broker channel")
    if (
        resources.recipient != terms.recipient
        or evidence.accepted_delivery.selection.recipient != terms.recipient
    ):
        raise ValueError("policy recipient differs from selected delivery")
    if (
        evidence.accepted_delivery.selection.ingress_binding
        != scope.selected_source.ingress_binding
    ):
        raise ValueError("selected origin differs from accepted delivery")
    if (
        mandate.selection != "ORIGIN_EXACT"
        or mandate.payload_class != terms.payload_class
        or mandate.payload_digest != terms.payload_digest
        or mandate.payload_byte_length != terms.payload_byte_length
        or mandate.max_calls != terms.max_calls
        or mandate.clock_contract != terms.clock_contract
        or mandate.clock_epoch != terms.clock_epoch
        or mandate.preparation_basis != prepared_delivery_basis_head_v3(evidence.basis)
    ):
        raise ValueError("policy terms differ from selected mandate")
    if not (
        terms.not_before_ns <= mandate.not_before_ns < mandate.expires_at_ns <= terms.expires_at_ns
    ):
        raise ValueError("mandate horizon escapes policy horizon")
    if not (
        terms.not_before_ns <= evidence.now_ns < terms.expires_at_ns
        and mandate.not_before_ns <= evidence.now_ns < mandate.expires_at_ns
    ):
        raise ValueError("named clock is outside policy or mandate horizon")
    policy_head = request.selected_policy_anchor.policy
    if (
        terms.communication_permission != "PREPARED_EXTERNAL_SEND"
        or terms.disclosure_permission != "EXACT_RENDERED_PAYLOAD"
        or terms.self_recipient_semantics != "OPERATOR_ATTESTED_SELF"
        or mandate.communication_authority != policy_head
        or mandate.disclosure_authority != policy_head
        or mandate.self_recipient_binding != policy_head
    ):
        raise ValueError("explicit policy permission semantics differ")
    if "CLI" not in terms.source_classes or "ORIGIN_EXACT" not in terms.selection_modes:
        raise ValueError("policy does not allow CLI origin selection")


def _scope(
    request: IssuePreparedExternalDeliveryGrantRequestV2,
    call: AuthorizePreparedExternalDeliveryGrantCallV2,
) -> PreparedExternalDeliveryGrantScopeV2:
    evidence = call.evidence
    local = evidence.selected_scope.scope
    accepted = evidence.accepted_delivery
    if not isinstance(accepted.selection, OriginSelection):
        raise ValueError("accepted delivery is not origin exact")
    terms = PreparedExternalSelfDeliveryPolicyV1.model_validate_json(
        call.current_policy_bytes
    ).terms
    policy_head = request.selected_policy_anchor.policy
    return PreparedExternalDeliveryGrantScopeV2(
        principal_id=local.principal_id,
        worker_session_id=local.worker_session_id,
        contour_head=local.contour_head,
        authenticated_credential_head=local.authenticated_cli_state.credential_head,
        authenticated_session_head=local.authenticated_cli_state.session_head,
        selected_source=evidence.selected_source,
        resources=ExternalDeliveryResourcesV1(
            signature_domain="dispatch-resources.v1",
            signed_observation_fingerprint=local.selected_resource_observation_ref.signed_observation_fingerprint,
            resource_grant=evidence.resource_grant,
            recipient=accepted.selection.recipient,
            clock_epoch=evidence.clock_epoch,
        ),
        mandate=BoundedExternalSelfSendMandateV1(
            purpose="PREPARED_EXTERNAL_SELF_SEND",
            external_delivery=True,
            selection="ORIGIN_EXACT",
            payload_class=terms.payload_class,
            communication_authority=policy_head,
            disclosure_authority=policy_head,
            self_recipient_binding=policy_head,
            original_run=evidence.original_run,
            captured_attempt=evidence.captured_attempt,
            preparation_basis=prepared_delivery_basis_head_v3(evidence.basis),
            delivery_id=accepted.delivery_id,
            payload_digest=accepted.render_digest,
            payload_byte_length=len(accepted.rendered_bytes),
            max_calls=1,
            clock_contract=evidence.clock_contract,
            clock_epoch=evidence.clock_epoch,
            not_before_ns=terms.not_before_ns,
            expires_at_ns=terms.expires_at_ns,
        ),
    )


def _matches_snapshot(raw: bytes, expected_head: str) -> bool:
    entries = json.loads(raw)
    if not isinstance(entries, list) or not entries:
        return False
    predecessor: str | None = None
    for entry in entries:
        if (
            not isinstance(entry, list)
            or len(entry) != 3
            or entry[1] != predecessor
            or not isinstance(entry[0], str)
            or not isinstance(entry[2], str)
        ):
            return False
        envelope_raw = base64.b64decode(entry[2], validate=True)
        envelope = json.loads(envelope_raw)
        if (
            not isinstance(envelope, dict)
            or set(envelope) != {"kind", "payload", "predecessor"}
            or envelope["predecessor"] != predecessor
            or _canonical(envelope) != envelope_raw
        ):
            return False
        logical = hashlib.sha256(
            (predecessor or "GENESIS").encode() + b"\x00" + envelope_raw
        ).hexdigest()
        if entry[0] != logical:
            return False
        predecessor = logical
    return _canonical(entries) == raw and predecessor == expected_head


def _rejected(
    disposition: Literal["DENIED", "STALE"], call_sha256: str, reason: str
) -> PreparedExternalDeliveryGrantRejectedV2:
    return PreparedExternalDeliveryGrantRejectedV2(
        disposition=disposition, call_sha256=call_sha256, reason=reason
    )


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _exact(value: object) -> ExactHead:
    """Translate equivalent effects head DTOs at the isolated owner boundary."""
    identity = getattr(value, "identity", None)
    head = getattr(value, "head", None)
    fingerprint = getattr(value, "fingerprint", None)
    if (
        not isinstance(identity, str)
        or not isinstance(head, str)
        or not isinstance(fingerprint, str)
    ):
        raise ValueError("effects head is not an exact head")
    return ExactHead(identity=identity, head=head, fingerprint=fingerprint)


ROUTES = (_ROUTE,)
