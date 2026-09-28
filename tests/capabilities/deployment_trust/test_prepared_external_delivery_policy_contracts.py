"""Structural policy wires remain inert and bounded on their JSON boundary."""

import hashlib
import json
from typing import Any, Literal

import pytest
from pydantic import ValidationError

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead, ProviderRecipient
from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
    HermeticTrustObservationV1,
)
from chiplog.capabilities.deployment_trust.prepared_external_delivery_policy_contracts import (
    IssuePreparedExternalSelfDeliveryPolicyRequestV1,
    IssuePreparedExternalSelfDeliveryPolicyV1,
    PreparedExternalSelfDeliveryPolicyAnchorV1,
    PreparedExternalSelfDeliveryPolicyTermsV1,
    PreparedExternalSelfDeliveryPolicyV1,
    RevokePreparedExternalSelfDeliveryPolicyRequestV1,
    RevokePreparedExternalSelfDeliveryPolicyV1,
)


def head(identity: str, body: bytes = b"inert") -> ExactHead:
    return ExactHead(
        identity=identity,
        head=identity + ":head",
        fingerprint=hashlib.sha256(body).hexdigest(),
    )


def recipient() -> ProviderRecipient:
    return ProviderRecipient(
        provider_id="telegram",
        account_id="self-account",
        recipient_id="self-recipient",
        endpoint=head("endpoint"),
        canonical_address=b"telegram://self-account/self-recipient",
        credential_binding=head("credential"),
    )


def terms() -> PreparedExternalSelfDeliveryPolicyTermsV1:
    payload = b"prepared payload"
    return PreparedExternalSelfDeliveryPolicyTermsV1(
        principal_id="principal",
        channel_id="telegram-channel",
        recipient=recipient(),
        communication_permission="PREPARED_EXTERNAL_SEND",
        disclosure_permission="EXACT_RENDERED_PAYLOAD",
        self_recipient_semantics="OPERATOR_ATTESTED_SELF",
        payload_class="NonAuthoritativeText",
        payload_digest=hashlib.sha256(payload).hexdigest(),
        payload_byte_length=len(payload),
        source_classes=("CLI", "TELEGRAM_PUSH"),
        selection_modes=("ORIGIN_EXACT", "MODEL_SELECTED_EXACT"),
        external_delivery=True,
        max_calls=1,
        clock_contract="clock-contract",
        clock_epoch="clock-epoch",
        not_before_ns=100,
        expires_at_ns=200,
    )


def policy(
    *,
    revision: int = 0,
    predecessor: ExactHead | None = None,
    status: Literal["ACTIVE", "REVOKED"] = "ACTIVE",
) -> PreparedExternalSelfDeliveryPolicyV1:
    return PreparedExternalSelfDeliveryPolicyV1(
        issuer="deployment_trust",
        tenant_id="tenant",
        database_id="database",
        policy_id="policy",
        revision=revision,
        predecessor=predecessor,
        status=status,
        terms=terms(),
        authorization_command=head("operator-command"),
        authorization_source=head("operator-source"),
    )


def anchor(
    *, policy_id: str = "policy", revision: int = 0
) -> PreparedExternalSelfDeliveryPolicyAnchorV1:
    return PreparedExternalSelfDeliveryPolicyAnchorV1(
        owner_id="deployment_trust",
        decision=head("trust-decision"),
        record_ordinal=0,
        record_type_id="chiplog.deployment_trust.prepared_self_delivery_policy",
        schema_id="chiplog.deployment_trust.record.v1",
        record=head("trust-record"),
        policy=head(policy_id),
        revision=revision,
    )


def observation() -> HermeticTrustObservationV1:
    return HermeticTrustObservationV1(
        physical_journal_head=head("trust-journal"),
        logical_snapshot_head="trust-snapshot",
    )


def issue_request(
    expected_policy: PreparedExternalSelfDeliveryPolicyAnchorV1 | None = None,
) -> IssuePreparedExternalSelfDeliveryPolicyRequestV1:
    return IssuePreparedExternalSelfDeliveryPolicyRequestV1(
        operation="ISSUE_PREPARED_EXTERNAL_SELF_DELIVERY_POLICY",
        command_id="issue-command",
        tenant_id="tenant",
        database_id="database",
        policy_id="policy",
        expected_trust_observation=observation(),
        expected_policy=expected_policy,
        terms=terms(),
    )


def revoke_request(
    expected_policy: PreparedExternalSelfDeliveryPolicyAnchorV1 | None = None,
) -> RevokePreparedExternalSelfDeliveryPolicyRequestV1:
    return RevokePreparedExternalSelfDeliveryPolicyRequestV1(
        operation="REVOKE_PREPARED_EXTERNAL_SELF_DELIVERY_POLICY",
        command_id="revoke-command",
        tenant_id="tenant",
        database_id="database",
        policy_id="policy",
        expected_trust_observation=observation(),
        expected_policy=expected_policy or anchor(),
    )


def json_bytes(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode()


@pytest.mark.parametrize(
    "value",
    [
        terms(),
        policy(),
        anchor(),
        issue_request(),
        revoke_request(),
        IssuePreparedExternalSelfDeliveryPolicyV1(
            request=issue_request(), authenticated_operator_source=head("issue-source")
        ),
        RevokePreparedExternalSelfDeliveryPolicyV1(
            request=revoke_request(), authenticated_operator_source=head("revoke-source")
        ),
    ],
)
def test_public_policy_dtos_roundtrip_canonical_json(value: Any) -> None:
    restored = type(value).model_validate_json(value.canonical_bytes())

    assert restored == value
    assert restored.canonical_bytes() == value.canonical_bytes()


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("recipient", "canonical_address"), ""),
        (("payload_byte_length",), 0),
        (("payload_byte_length",), 65_537),
        (("source_classes",), []),
        (("source_classes",), ["CLI", "TELEGRAM_PUSH", "TELEGRAM_POLL", "CLI"]),
        (("selection_modes",), []),
        (("selection_modes",), ["ORIGIN_EXACT", "MODEL_SELECTED_EXACT", "ORIGIN_EXACT"]),
    ],
)
def test_terms_reject_unbounded_recipient_payload_or_scope(
    path: tuple[str, ...], replacement: object
) -> None:
    payload = terms().model_dump(mode="json")
    target: dict[str, Any] = payload
    for field in path[:-1]:
        target = target[field]
    target[path[-1]] = replacement

    with pytest.raises(ValidationError):
        PreparedExternalSelfDeliveryPolicyTermsV1.model_validate_json(json_bytes(payload))


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("source_classes", ["CLI", "CLI"], "source classes must be unique"),
        ("selection_modes", ["ORIGIN_EXACT", "ORIGIN_EXACT"], "selection modes must be unique"),
    ],
)
def test_terms_reject_duplicate_exact_source_or_selection_mode(
    field: str, replacement: list[str], message: str
) -> None:
    payload = terms().model_dump(mode="json")
    payload[field] = replacement

    with pytest.raises(ValidationError, match=message):
        PreparedExternalSelfDeliveryPolicyTermsV1.model_validate_json(json_bytes(payload))


def test_terms_reject_empty_validity_horizon() -> None:
    payload = terms().model_dump(mode="json")
    payload["expires_at_ns"] = payload["not_before_ns"]

    with pytest.raises(ValidationError, match="nonempty validity horizon"):
        PreparedExternalSelfDeliveryPolicyTermsV1.model_validate_json(json_bytes(payload))


@pytest.mark.parametrize(
    ("revision", "predecessor", "status", "message"),
    [
        (0, head("policy"), "ACTIVE", "genesis requires revision zero"),
        (1, None, "ACTIVE", "genesis requires revision zero"),
        (1, head("other-policy"), "ACTIVE", "belongs to another policy"),
        (0, None, "REVOKED", "revocation requires an existing policy revision"),
    ],
)
def test_policy_rejects_invalid_revision_predecessor_or_status_lineage(
    revision: int, predecessor: ExactHead | None, status: str, message: str
) -> None:
    payload = policy().model_dump(mode="json")
    payload["revision"] = revision
    payload["predecessor"] = None if predecessor is None else predecessor.model_dump(mode="json")
    payload["status"] = status

    with pytest.raises(ValidationError, match=message):
        PreparedExternalSelfDeliveryPolicyV1.model_validate_json(json_bytes(payload))


def test_issue_permits_create_only_but_revoke_requires_a_policy_anchor() -> None:
    assert issue_request().expected_policy is None
    payload = revoke_request().model_dump(mode="json")
    payload["expected_policy"] = None

    with pytest.raises(ValidationError):
        RevokePreparedExternalSelfDeliveryPolicyRequestV1.model_validate_json(json_bytes(payload))


@pytest.mark.parametrize("request_type", ["issue", "revoke"])
@pytest.mark.parametrize(
    ("policy_id", "revision", "message"),
    [
        ("other-policy", 0, "expected head belongs to another policy"),
        ("policy", 2**64 - 1, "policy revision is exhausted"),
    ],
)
def test_issue_and_revoke_reject_wrong_or_exhausted_policy_anchor(
    request_type: str, policy_id: str, revision: int, message: str
) -> None:
    request: (
        IssuePreparedExternalSelfDeliveryPolicyRequestV1
        | RevokePreparedExternalSelfDeliveryPolicyRequestV1
    ) = issue_request()
    model: type[
        IssuePreparedExternalSelfDeliveryPolicyRequestV1
        | RevokePreparedExternalSelfDeliveryPolicyRequestV1
    ]
    if request_type == "revoke":
        request = revoke_request()
        model = RevokePreparedExternalSelfDeliveryPolicyRequestV1
    else:
        model = IssuePreparedExternalSelfDeliveryPolicyRequestV1
    payload = request.model_dump(mode="json")
    payload["expected_policy"] = anchor(policy_id=policy_id, revision=revision).model_dump(
        mode="json"
    )

    with pytest.raises(ValidationError, match=message):
        model.model_validate_json(json_bytes(payload))


@pytest.mark.parametrize(
    ("wrapper", "other_request"),
    [
        (IssuePreparedExternalSelfDeliveryPolicyV1, revoke_request()),
        (RevokePreparedExternalSelfDeliveryPolicyV1, issue_request()),
    ],
)
def test_command_wrappers_bind_their_concrete_request_payload(
    wrapper: type[
        IssuePreparedExternalSelfDeliveryPolicyV1 | RevokePreparedExternalSelfDeliveryPolicyV1
    ],
    other_request: IssuePreparedExternalSelfDeliveryPolicyRequestV1
    | RevokePreparedExternalSelfDeliveryPolicyRequestV1,
) -> None:
    payload = {
        "request": other_request.model_dump(mode="json"),
        "authenticated_operator_source": head("operator-source").model_dump(mode="json"),
    }

    with pytest.raises(ValidationError):
        wrapper.model_validate_json(json_bytes(payload))
