"""Contract checks for inert operator-policy authorization DTOs."""

import hashlib
import json

import pytest
from pydantic import ValidationError

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
from chiplog.capabilities.deployment_trust.prepared_external_delivery_policy_contracts import (
    IssuePreparedExternalSelfDeliveryPolicyRequestV1,
    PreparedExternalSelfDeliveryPolicyAnchorV1,
    PreparedExternalSelfDeliveryPolicyTermsV1,
    RevokePreparedExternalSelfDeliveryPolicyRequestV1,
)


def head(identity: str, body: bytes = b"inert") -> ExactHead:
    return ExactHead(
        identity=identity,
        head=f"{identity}:head",
        fingerprint=hashlib.sha256(body).hexdigest(),
    )


def terms() -> PreparedExternalSelfDeliveryPolicyTermsV1:
    body = b"prepared payload"
    return PreparedExternalSelfDeliveryPolicyTermsV1(
        principal_id="principal",
        channel_id="telegram-channel",
        recipient=ProviderRecipient(
            provider_id="telegram",
            account_id="self-account",
            recipient_id="self-recipient",
            endpoint=head("endpoint"),
            canonical_address=b"telegram://self-account/self-recipient",
            credential_binding=head("credential"),
        ),
        communication_permission="PREPARED_EXTERNAL_SEND",
        disclosure_permission="EXACT_RENDERED_PAYLOAD",
        self_recipient_semantics="OPERATOR_ATTESTED_SELF",
        payload_class="NonAuthoritativeText",
        payload_digest=hashlib.sha256(body).hexdigest(),
        payload_byte_length=len(body),
        source_classes=("CLI", "TELEGRAM_PUSH"),
        selection_modes=("ORIGIN_EXACT", "MODEL_SELECTED_EXACT"),
        external_delivery=True,
        max_calls=1,
        clock_contract="clock-contract",
        clock_epoch="clock-epoch",
        not_before_ns=100,
        expires_at_ns=200,
    )


def observation() -> HermeticTrustObservationV1:
    return HermeticTrustObservationV1(
        physical_journal_head=head("trust-journal"), logical_snapshot_head="trust-snapshot"
    )


def anchor() -> PreparedExternalSelfDeliveryPolicyAnchorV1:
    return PreparedExternalSelfDeliveryPolicyAnchorV1(
        owner_id="deployment_trust",
        decision=head("trust-decision"),
        record_ordinal=0,
        record_type_id="chiplog.deployment_trust.prepared_self_delivery_policy",
        schema_id="chiplog.deployment_trust.record.v1",
        record=head("trust-record"),
        policy=head("policy"),
        revision=0,
    )


def issue_request() -> IssuePreparedExternalSelfDeliveryPolicyRequestV1:
    return IssuePreparedExternalSelfDeliveryPolicyRequestV1(
        operation="ISSUE_PREPARED_EXTERNAL_SELF_DELIVERY_POLICY",
        command_id="issue-command",
        tenant_id="tenant",
        database_id="database",
        policy_id="policy",
        expected_trust_observation=observation(),
        expected_policy=None,
        terms=terms(),
    )


def revoke_request() -> RevokePreparedExternalSelfDeliveryPolicyRequestV1:
    return RevokePreparedExternalSelfDeliveryPolicyRequestV1(
        operation="REVOKE_PREPARED_EXTERNAL_SELF_DELIVERY_POLICY",
        command_id="revoke-command",
        tenant_id="tenant",
        database_id="database",
        policy_id="policy",
        expected_trust_observation=observation(),
        expected_policy=anchor(),
    )


def payload_for(
    request: IssuePreparedExternalSelfDeliveryPolicyRequestV1
    | RevokePreparedExternalSelfDeliveryPolicyRequestV1,
) -> OperatorPolicyAuthorizationPayloadV1:
    request_bytes = request.canonical_bytes()
    return OperatorPolicyAuthorizationPayloadV1(
        algorithm="Ed25519",
        source_id="operator-source",
        operator_key_id="operator-key",
        tenant_id=request.tenant_id,
        database_id=request.database_id,
        policy_id=request.policy_id,
        command_id=request.command_id,
        operation=request.operation,
        request_sha256=hashlib.sha256(request_bytes).hexdigest(),
        canonical_request_bytes=request_bytes,
    )


@pytest.mark.parametrize("policy_request", [issue_request(), revoke_request()])
def test_all_operator_authorization_dtos_roundtrip_canonical_json(
    policy_request: IssuePreparedExternalSelfDeliveryPolicyRequestV1
    | RevokePreparedExternalSelfDeliveryPolicyRequestV1,
) -> None:
    payload = payload_for(policy_request)
    # This is deliberately arbitrary 64-byte data: DTO construction is not authorization.
    signed = SignedOperatorPolicyAuthorizationV1(payload=payload, signature=bytes(range(64)))
    retained = RetainedOperatorPolicyAuthorizationSourceV1(
        ref=ExactHead(
            identity=payload.source_id,
            head=operator_policy_source_content_head(signed.canonical_bytes()),
            fingerprint=hashlib.sha256(signed.canonical_bytes()).hexdigest(),
        ),
        canonical_source_bytes=signed.canonical_bytes(),
    )

    for value in (payload, signed, retained):
        restored = type(value).model_validate_json(value.canonical_bytes())
        assert restored == value
        assert restored.canonical_bytes() == value.canonical_bytes()


def test_payload_binds_exact_canonical_issue_request_bytes_and_hash() -> None:
    request = issue_request()
    payload = payload_for(request)

    assert payload.canonical_request_bytes == request.canonical_bytes()
    assert payload.request_sha256 == (
        "354b6727248a0b8c86747ce70dc18a90eb4499ce99f5418fdc41cf9189b94076"
    )
    assert payload.canonical_request_bytes.startswith(b'{"command_id":"issue-command",')
    assert payload.canonical_request_bytes.endswith(b'"source_classes":["CLI","TELEGRAM_PUSH"]}}')


def test_payload_binds_exact_canonical_revoke_request_bytes_and_hash() -> None:
    request = revoke_request()
    payload = payload_for(request)

    assert payload.canonical_request_bytes == request.canonical_bytes()
    assert payload.request_sha256 == (
        "265327e35732efb7ca1564885337543e5c15b7258b8916823236eb8ca3380100"
    )
    assert payload.canonical_request_bytes.startswith(b'{"command_id":"revoke-command",')
    assert b'"operation":"REVOKE_PREPARED_EXTERNAL_SELF_DELIVERY_POLICY"' in (
        payload.canonical_request_bytes
    )


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("tenant_id", "other-tenant"),
        ("database_id", "other-database"),
        ("policy_id", "other-policy"),
        ("command_id", "other-command"),
        ("operation", "ISSUE_PREPARED_EXTERNAL_SELF_DELIVERY_POLICY"),
    ],
)
def test_payload_rejects_scope_operation_or_command_mismatch(
    field: str, replacement: str
) -> None:
    request = revoke_request() if field == "operation" else issue_request()
    payload = payload_for(request).model_dump(mode="json")
    payload[field] = replacement

    expected_message = None if field == "operation" else f"request {field} mismatch"
    with pytest.raises(ValidationError, match=expected_message):
        OperatorPolicyAuthorizationPayloadV1.model_validate_json(
            json.dumps(payload, separators=(",", ":")).encode()
        )


def test_payload_rejects_hash_and_noncanonical_request_bytes() -> None:
    payload = payload_for(issue_request()).model_dump(mode="json")
    payload["request_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="SHA256 mismatch"):
        OperatorPolicyAuthorizationPayloadV1.model_validate_json(
            json.dumps(payload, separators=(",", ":")).encode()
        )

    payload = payload_for(issue_request()).model_dump()
    payload["canonical_request_bytes"] = json.dumps(
        json.loads(payload["canonical_request_bytes"]), separators=(",", ": ")
    ).encode()
    payload["request_sha256"] = hashlib.sha256(payload["canonical_request_bytes"]).hexdigest()
    with pytest.raises(ValidationError, match="request bytes must be canonical"):
        OperatorPolicyAuthorizationPayloadV1.model_validate(payload)


def test_signed_wrapper_requires_exact_signature_length_but_does_not_authorize() -> None:
    payload = payload_for(issue_request())

    for signature in (b"x" * 63, b"x" * 65):
        with pytest.raises(ValidationError):
            SignedOperatorPolicyAuthorizationV1(payload=payload, signature=signature)

    cryptographically_invalid_signature = b"\x00" * 64
    signed = SignedOperatorPolicyAuthorizationV1(
        payload=payload, signature=cryptographically_invalid_signature
    )
    assert signed.signature == cryptographically_invalid_signature
    assert len(signed.signature) == 64


def test_retained_source_binds_hash_identity_content_head_and_canonical_bytes() -> None:
    payload = payload_for(issue_request())
    signed = SignedOperatorPolicyAuthorizationV1(payload=payload, signature=bytes(range(64)))
    source_bytes = signed.canonical_bytes()
    fingerprint = hashlib.sha256(source_bytes).hexdigest()
    content_head = operator_policy_source_content_head(source_bytes)

    retained = RetainedOperatorPolicyAuthorizationSourceV1(
        ref=ExactHead(
            identity="operator-source", head=content_head, fingerprint=fingerprint
        ),
        canonical_source_bytes=source_bytes,
    )
    assert retained.ref.identity == signed.payload.source_id
    assert retained.ref.fingerprint == (
        "c7e3f34c57385dd07a7fce9e3cf596a134477b134f283c5681e5dc1e4b76da26"
    )
    assert retained.ref.head == content_head

    for ref in (
        ExactHead(identity="other-source", head=content_head, fingerprint=fingerprint),
        ExactHead(identity="operator-source", head=content_head, fingerprint="0" * 64),
    ):
        with pytest.raises(ValidationError):
            RetainedOperatorPolicyAuthorizationSourceV1(
                ref=ref, canonical_source_bytes=source_bytes
            )

    with pytest.raises(ValidationError, match="source content head mismatch"):
        RetainedOperatorPolicyAuthorizationSourceV1(
            ref=ExactHead(
                identity="operator-source", head="wrong-content-head", fingerprint=fingerprint
            ),
            canonical_source_bytes=source_bytes,
        )

    noncanonical = json.dumps(json.loads(source_bytes), separators=(",", ": ")).encode()
    with pytest.raises(ValidationError, match="source bytes must be canonical"):
        RetainedOperatorPolicyAuthorizationSourceV1(
            ref=ExactHead(
                identity="operator-source",
                head=operator_policy_source_content_head(noncanonical),
                fingerprint=hashlib.sha256(noncanonical).hexdigest(),
            ),
            canonical_source_bytes=noncanonical,
        )
