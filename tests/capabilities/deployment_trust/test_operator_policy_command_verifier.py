"""Red acceptance tests for the pure operator-policy command verifier."""

import hashlib
import json
from typing import Literal

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead, ProviderRecipient
from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
    HermeticTrustObservationV1,
)
from chiplog.capabilities.deployment_trust.operator_policy_authorization_contracts import (
    OperatorPolicyAuthorizationPayloadV1,
    RetainedOperatorPolicyAuthorizationSourceV1,
    SignedOperatorPolicyAuthorizationV1,
    operator_policy_source_content_head,
)
from chiplog.capabilities.deployment_trust.operator_policy_command_verifier import (
    OperatorPolicyCommand,
    OperatorPolicyKeyBindingV1,
    OperatorPolicyOperation,
    OperatorPolicyRequest,
    OperatorPolicyVerificationError,
    verify_operator_policy_command,
)
from chiplog.capabilities.deployment_trust.prepared_external_delivery_policy_contracts import (
    IssuePreparedExternalSelfDeliveryPolicyRequestV1,
    IssuePreparedExternalSelfDeliveryPolicyV1,
    PreparedExternalSelfDeliveryPolicyAnchorV1,
    PreparedExternalSelfDeliveryPolicyTermsV1,
    RevokePreparedExternalSelfDeliveryPolicyRequestV1,
    RevokePreparedExternalSelfDeliveryPolicyV1,
)

ISSUE: Literal["ISSUE_PREPARED_EXTERNAL_SELF_DELIVERY_POLICY"] = (
    "ISSUE_PREPARED_EXTERNAL_SELF_DELIVERY_POLICY"
)
REVOKE: Literal["REVOKE_PREPARED_EXTERNAL_SELF_DELIVERY_POLICY"] = (
    "REVOKE_PREPARED_EXTERNAL_SELF_DELIVERY_POLICY"
)


def head(identity: str, body: bytes = b"test") -> ExactHead:
    return ExactHead(
        identity=identity,
        head=f"{identity}:head",
        fingerprint=hashlib.sha256(body).hexdigest(),
    )


def source_ref(source_bytes: bytes) -> ExactHead:
    return ExactHead(
        identity="operator-source",
        head=operator_policy_source_content_head(source_bytes),
        fingerprint=hashlib.sha256(source_bytes).hexdigest(),
    )


def request(operation: OperatorPolicyOperation) -> OperatorPolicyRequest:
    observation = HermeticTrustObservationV1(
        physical_journal_head=head("trust-journal"), logical_snapshot_head="trust-snapshot"
    )
    if operation == ISSUE:
        return IssuePreparedExternalSelfDeliveryPolicyRequestV1(
            operation=ISSUE,
            command_id="issue-command",
            tenant_id="tenant",
            database_id="database",
            policy_id="policy",
            expected_trust_observation=observation,
            expected_policy=None,
            terms=PreparedExternalSelfDeliveryPolicyTermsV1(
                principal_id="principal",
                channel_id="channel",
                recipient=ProviderRecipient(
                    provider_id="telegram",
                    account_id="account",
                    recipient_id="recipient",
                    endpoint=head("endpoint"),
                    canonical_address=b"telegram://account/recipient",
                    credential_binding=head("credential"),
                ),
                communication_permission="PREPARED_EXTERNAL_SEND",
                disclosure_permission="EXACT_RENDERED_PAYLOAD",
                self_recipient_semantics="OPERATOR_ATTESTED_SELF",
                payload_class="NonAuthoritativeText",
                payload_digest=hashlib.sha256(b"payload").hexdigest(),
                payload_byte_length=7,
                source_classes=("CLI",),
                selection_modes=("ORIGIN_EXACT",),
                external_delivery=True,
                max_calls=1,
                clock_contract="clock-contract",
                clock_epoch="clock-epoch",
                not_before_ns=1,
                expires_at_ns=2,
            ),
        )
    return RevokePreparedExternalSelfDeliveryPolicyRequestV1(
        operation=REVOKE,
        command_id="revoke-command",
        tenant_id="tenant",
        database_id="database",
        policy_id="policy",
        expected_trust_observation=observation,
        expected_policy=PreparedExternalSelfDeliveryPolicyAnchorV1(
            owner_id="deployment_trust",
            decision=head("decision"),
            record_ordinal=0,
            record_type_id="chiplog.deployment_trust.prepared_self_delivery_policy",
            schema_id="chiplog.deployment_trust.record.v1",
            record=head("record"),
            policy=head("policy"),
            revision=0,
        ),
    )


def signed_source(
    policy_request: OperatorPolicyRequest, private_key: Ed25519PrivateKey
) -> RetainedOperatorPolicyAuthorizationSourceV1:
    request_bytes = policy_request.canonical_bytes()
    payload = OperatorPolicyAuthorizationPayloadV1(
        algorithm="Ed25519",
        source_id="operator-source",
        operator_key_id="operator-key",
        tenant_id=policy_request.tenant_id,
        database_id=policy_request.database_id,
        policy_id=policy_request.policy_id,
        command_id=policy_request.command_id,
        operation=policy_request.operation,
        request_sha256=hashlib.sha256(request_bytes).hexdigest(),
        canonical_request_bytes=request_bytes,
    )
    signed = SignedOperatorPolicyAuthorizationV1(
        payload=payload, signature=private_key.sign(payload.canonical_bytes())
    )
    source_bytes = signed.canonical_bytes()
    return RetainedOperatorPolicyAuthorizationSourceV1(
        ref=source_ref(source_bytes), canonical_source_bytes=source_bytes
    )


def command(
    policy_request: OperatorPolicyRequest, source: RetainedOperatorPolicyAuthorizationSourceV1
) -> OperatorPolicyCommand:
    if isinstance(policy_request, IssuePreparedExternalSelfDeliveryPolicyRequestV1):
        return IssuePreparedExternalSelfDeliveryPolicyV1(
            request=policy_request, authenticated_operator_source=source.ref
        )
    return RevokePreparedExternalSelfDeliveryPolicyV1(
        request=policy_request, authenticated_operator_source=source.ref
    )


def binding(private_key: Ed25519PrivateKey, **changes: object) -> OperatorPolicyKeyBindingV1:
    value = OperatorPolicyKeyBindingV1(
        tenant_id="tenant",
        database_id="database",
        operator_key_id="operator-key",
        policy_id="policy",
        public_key=private_key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        ),
        status="ACTIVE",
        allowed_operations=(ISSUE, REVOKE),
        ref=head("operator-key-binding"),
    )
    return value.model_copy(update=changes)


@pytest.mark.parametrize("operation", [ISSUE, REVOKE])
def test_verifies_real_ed25519_signed_issue_and_revoke(operation: OperatorPolicyOperation) -> None:
    private_key = Ed25519PrivateKey.generate()
    policy_request = request(operation)
    retained = signed_source(policy_request, private_key)

    verify_operator_policy_command(
        command(policy_request, retained),
        retained_source=retained,
        current_binding=binding(private_key),
    )


@pytest.mark.parametrize(
    "binding_change", [{"status": "REVOKED"}, {"status": "PENDING"}, {"allowed_operations": ()}]
)
def test_rejects_missing_revoked_or_operation_disallowed_binding(
    binding_change: dict[str, object],
) -> None:
    private_key = Ed25519PrivateKey.generate()
    policy_request = request(ISSUE)
    retained = signed_source(policy_request, private_key)

    with pytest.raises(OperatorPolicyVerificationError):
        verify_operator_policy_command(
            command(policy_request, retained), retained_source=retained, current_binding=None
        )
    with pytest.raises(OperatorPolicyVerificationError):
        verify_operator_policy_command(
            command(policy_request, retained),
            retained_source=retained,
            current_binding=binding(private_key, **binding_change),
        )


@pytest.mark.parametrize(
    "field", ["tenant_id", "database_id", "operator_key_id", "policy_id"]
)
def test_rejects_binding_with_one_wrong_scope_field(field: str) -> None:
    private_key = Ed25519PrivateKey.generate()
    policy_request = request(ISSUE)
    retained = signed_source(policy_request, private_key)

    with pytest.raises(OperatorPolicyVerificationError):
        verify_operator_policy_command(
            command(policy_request, retained),
            retained_source=retained,
            current_binding=binding(private_key, **{field: f"other-{field}"}),
        )


def test_rejects_forged_signature_and_changed_signed_request_bytes() -> None:
    private_key = Ed25519PrivateKey.generate()
    policy_request = request(ISSUE)
    retained = signed_source(policy_request, private_key)
    signed = SignedOperatorPolicyAuthorizationV1.model_validate_json(
        retained.canonical_source_bytes
    )

    forged_signature = bytes([signed.signature[0] ^ 1]) + signed.signature[1:]
    forged = signed.model_copy(update={"signature": forged_signature})
    forged_bytes = forged.canonical_bytes()
    forged_retained = RetainedOperatorPolicyAuthorizationSourceV1(
        ref=source_ref(forged_bytes), canonical_source_bytes=forged_bytes
    )
    with pytest.raises(OperatorPolicyVerificationError):
        verify_operator_policy_command(
            command(policy_request, forged_retained),
            retained_source=forged_retained,
            current_binding=binding(private_key),
        )

    with pytest.raises(OperatorPolicyVerificationError):
        verify_operator_policy_command(
            command(policy_request, retained),
            retained_source=retained,
            current_binding=binding(Ed25519PrivateKey.generate()),
        )

    changed_request = request(ISSUE).model_copy(update={"command_id": "other-command"})
    with pytest.raises(OperatorPolicyVerificationError):
        verify_operator_policy_command(
            command(changed_request, retained),
            retained_source=retained,
            current_binding=binding(private_key),
        )


def test_rejects_source_bytes_or_full_authenticated_source_head_changed() -> None:
    private_key = Ed25519PrivateKey.generate()
    policy_request = request(ISSUE)
    retained = signed_source(policy_request, private_key)
    noncanonical_source = retained.canonical_source_bytes + b" "
    bypassed_source = RetainedOperatorPolicyAuthorizationSourceV1.model_construct(
        ref=source_ref(noncanonical_source), canonical_source_bytes=noncanonical_source
    )

    with pytest.raises(OperatorPolicyVerificationError):
        verify_operator_policy_command(
            command(policy_request, bypassed_source),
            retained_source=bypassed_source,
            current_binding=binding(private_key),
        )

    wrong_head = ExactHead(
        identity=retained.ref.identity,
        head="other-retained-revision",
        fingerprint=retained.ref.fingerprint,
    )
    bypassed_command = IssuePreparedExternalSelfDeliveryPolicyV1.model_construct(
        request=policy_request, authenticated_operator_source=wrong_head
    )
    with pytest.raises(OperatorPolicyVerificationError):
        verify_operator_policy_command(
            bypassed_command, retained_source=retained, current_binding=binding(private_key)
        )


def test_rejects_noncanonical_request_bytes_and_model_construct_bypasses() -> None:
    private_key = Ed25519PrivateKey.generate()
    policy_request = request(ISSUE)
    noncanonical_request = json.dumps(
        json.loads(policy_request.canonical_bytes()), separators=(",", ": ")
    ).encode()
    bypassed_payload = OperatorPolicyAuthorizationPayloadV1.model_construct(
        schema_id="chiplog.deployment-trust.operator-policy-authorization.v1",
        algorithm="Ed25519",
        source_id="operator-source",
        operator_key_id="operator-key",
        tenant_id="tenant",
        database_id="database",
        policy_id="policy",
        command_id="issue-command",
        operation=ISSUE,
        request_sha256=hashlib.sha256(noncanonical_request).hexdigest(),
        canonical_request_bytes=noncanonical_request,
    )
    signed = SignedOperatorPolicyAuthorizationV1.model_construct(
        payload=bypassed_payload, signature=private_key.sign(bypassed_payload.canonical_bytes())
    )
    source_bytes = signed.canonical_bytes()
    bypassed_source = RetainedOperatorPolicyAuthorizationSourceV1.model_construct(
        ref=source_ref(source_bytes), canonical_source_bytes=source_bytes
    )
    with pytest.raises(OperatorPolicyVerificationError):
        verify_operator_policy_command(
            command(policy_request, bypassed_source),
            retained_source=bypassed_source,
            current_binding=binding(private_key),
        )

    valid_binding = binding(private_key)
    malformed_binding_data = valid_binding.model_dump()
    malformed_binding_data["ref"] = valid_binding.ref
    malformed_binding_data["public_key"] = b"too short"
    malformed_binding = OperatorPolicyKeyBindingV1.model_construct(**malformed_binding_data)
    valid_source = signed_source(policy_request, private_key)
    with pytest.raises(OperatorPolicyVerificationError):
        verify_operator_policy_command(
            command(policy_request, valid_source),
            retained_source=valid_source,
            current_binding=malformed_binding,
        )
