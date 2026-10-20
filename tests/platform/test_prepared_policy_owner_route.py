"""Mounted witnesses for the J7 prepared-policy owner route.

These tests use the installed H1 runtime, rather than calling trust durability
directly: the public broker method alone may select an owner result and make
its proposal durable.
"""

from __future__ import annotations

import base64
import hashlib
import json

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import TypeAdapter

from chiplog.architecture.r7_runtime import R14_R17_H1_LOCAL_EFFECTS_J7_PRODUCTION_MANIFEST
from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead, ProviderRecipient
from chiplog.capabilities.deployment_trust._j7_process import make_dispatch
from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
    HermeticTrustObservationV1,
)
from chiplog.capabilities.deployment_trust.operator_policy_command_verifier import (
    OperatorPolicyKeyBindingV1,
)
from chiplog.capabilities.deployment_trust.prepared_external_delivery_policy_contracts import (
    IssuePreparedExternalSelfDeliveryPolicyRequestV1,
    PreparedExternalSelfDeliveryPolicyTermsV1,
)
from chiplog.capabilities.deployment_trust.prepared_external_delivery_policy_owner_contracts import (  # noqa: E501
    AuthorizePreparedSelfDeliveryPolicyCallV1,
    PreparedSelfDeliveryPolicyProposalV1,
    PreparedSelfDeliveryPolicyRejectedV1,
    PreparedSelfDeliveryPolicyResultV1,
)
from tests.support.h1_installed_launch import (
    DATABASE,
    TENANT,
)
from tests.support.platform.prepared_policy_owner_route import (
    _OPERATOR_KEY,
    _signed_source,
)

_FOREIGN_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(33, 65)))


def _head(identity: str, body: bytes = b"j7") -> ExactHead:
    return ExactHead(
        identity=identity,
        head=f"{identity}/{hashlib.sha256(body).hexdigest()}",
        fingerprint=hashlib.sha256(body).hexdigest(),
    )


def _terms() -> PreparedExternalSelfDeliveryPolicyTermsV1:
    payload = b"prepared self delivery"
    return PreparedExternalSelfDeliveryPolicyTermsV1(
        principal_id="hermetic-principal",
        channel_id="hermetic-local",
        recipient=ProviderRecipient(
            provider_id="telegram",
            account_id="account",
            recipient_id="recipient",
            endpoint=_head("endpoint"),
            canonical_address=b"telegram://account/recipient",
            credential_binding=_head("credential"),
        ),
        communication_permission="PREPARED_EXTERNAL_SEND",
        disclosure_permission="EXACT_RENDERED_PAYLOAD",
        self_recipient_semantics="OPERATOR_ATTESTED_SELF",
        payload_class="NonAuthoritativeText",
        payload_digest=hashlib.sha256(payload).hexdigest(),
        payload_byte_length=len(payload),
        source_classes=("CLI",),
        selection_modes=("ORIGIN_EXACT",),
        external_delivery=True,
        max_calls=1,
        clock_contract="clock",
        clock_epoch="epoch",
        not_before_ns=10,
        expires_at_ns=20,
    )


def _owner_call(
    *,
    request_observation: HermeticTrustObservationV1 | None = None,
    signing_key: Ed25519PrivateKey = _OPERATOR_KEY,
) -> AuthorizePreparedSelfDeliveryPolicyCallV1:
    envelope = {"kind": "J7_TEST", "payload": {}, "predecessor": None}
    envelope_bytes = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
    logical_head = hashlib.sha256(b"GENESIS\x00" + envelope_bytes).hexdigest()
    snapshot_bytes = json.dumps(
        [[logical_head, None, base64.b64encode(envelope_bytes).decode()]],
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    observation = HermeticTrustObservationV1(
        physical_journal_head=_head("j7-owner-wire", envelope_bytes),
        logical_snapshot_head=logical_head,
    )
    request = IssuePreparedExternalSelfDeliveryPolicyRequestV1(
        operation="ISSUE_PREPARED_EXTERNAL_SELF_DELIVERY_POLICY",
        command_id="j7-owner-wire",
        tenant_id=TENANT,
        database_id=DATABASE,
        policy_id="policy",
        expected_trust_observation=request_observation or observation,
        expected_policy=None,
        terms=_terms(),
    )
    return AuthorizePreparedSelfDeliveryPolicyCallV1(
        canonical_signed_source_bytes=_signed_source(request, signing_key=signing_key),
        snapshot_bytes=snapshot_bytes,
        expected_trust_observation=observation,
        latest_policy_anchor=None,
        latest_policy_bytes=None,
    )


def _owner_result(
    call: AuthorizePreparedSelfDeliveryPolicyCallV1, *, binding_bytes: bytes | None = None
) -> PreparedSelfDeliveryPolicyResultV1:
    response = make_dispatch(binding_bytes)(
        "deployment_trust.authorize_prepared_external_self_delivery_policy", call.canonical_bytes()
    )
    assert response["schema_id"] == "chiplog.deployment-trust.self-delivery-policy-result.v1"
    raw = base64.b64decode(str(response["payload"]), validate=True)
    result = TypeAdapter(PreparedSelfDeliveryPolicyResultV1).validate_json(raw)
    assert result.canonical_bytes() == raw
    assert result.schema_id == response["schema_id"]
    assert result.call_sha256 == hashlib.sha256(call.canonical_bytes()).hexdigest()
    return result


def _active_binding_bytes() -> bytes:
    digest = hashlib.sha256(b"j7-test-binding").hexdigest()
    return OperatorPolicyKeyBindingV1(
        ref=ExactHead(identity="operator-key", head=digest, fingerprint=digest),
        tenant_id=TENANT,
        database_id=DATABASE,
        operator_key_id="operator-key",
        policy_id="policy",
        allowed_operations=(
            "ISSUE_PREPARED_EXTERNAL_SELF_DELIVERY_POLICY",
            "REVOKE_PREPARED_EXTERNAL_SELF_DELIVERY_POLICY",
        ),
        status="ACTIVE",
        public_key=_OPERATOR_KEY.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        ),
    ).canonical_bytes()


def test_owner_denial_raw_wire_is_canonical_schema_bound_and_call_bound() -> None:
    result = _owner_result(_owner_call(signing_key=_FOREIGN_KEY))
    assert isinstance(result, PreparedSelfDeliveryPolicyRejectedV1)
    assert result.disposition == "DENIED"


def test_owner_uses_canonical_startup_binding_for_a_valid_signed_call() -> None:
    result = _owner_result(_owner_call(), binding_bytes=_active_binding_bytes())
    assert isinstance(result, PreparedSelfDeliveryPolicyProposalV1)
    assert result.policy.status == "ACTIVE"


def test_j7_manifest_admits_the_single_discriminated_result_schema() -> None:
    route = next(
        item
        for item in R14_R17_H1_LOCAL_EFFECTS_J7_PRODUCTION_MANIFEST.routes
        if item.operation_id == "deployment_trust.authorize_prepared_external_self_delivery_policy"
    )
    assert route.result_schema_id == "chiplog.deployment-trust.self-delivery-policy-result.v1"


def test_owner_stale_raw_wire_is_canonical_schema_bound_and_call_bound() -> None:
    stale_observation = HermeticTrustObservationV1(
        physical_journal_head=_head("stale-owner-wire"), logical_snapshot_head="stale-logical"
    )
    result = _owner_result(_owner_call(request_observation=stale_observation))
    assert isinstance(result, PreparedSelfDeliveryPolicyRejectedV1)
    assert result.disposition == "STALE"
