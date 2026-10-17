"""Narrow consumer checks for the inert V2 prepared-delivery grant lifecycle."""

import hashlib
import json

import pytest
from pydantic import TypeAdapter, ValidationError

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead
from chiplog.capabilities.deployment_trust.operator_grant_authorization_contracts import (
    OperatorGrantAuthorizationPayloadV2,
    RetainedOperatorGrantAuthorizationSourceV2,
    SignedOperatorGrantAuthorizationV2,
    operator_grant_source_content_head_v2,
)
from chiplog.capabilities.deployment_trust.operator_policy_authorization_contracts import (
    SignedOperatorPolicyAuthorizationV1,
)
from chiplog.capabilities.deployment_trust.prepared_external_delivery_contracts import (
    IssuePreparedExternalDeliveryGrantRequestV2,
    ObservedPreparedExternalDeliveryGrantLifecycleV2,
    PreparedExternalDeliveryGrantLifecycleResultV2,
    PreparedExternalDeliveryGrantV1,
    PreparedExternalDeliveryGrantV2,
    ReadPreparedExternalDeliveryGrantLifecycleV2,
    RevokePreparedExternalDeliveryGrantRequestV2,
    UnobservedPreparedExternalDeliveryGrantLifecycleV2,
)
from chiplog.capabilities.deployment_trust.prepared_external_delivery_policy_contracts import (
    PreparedExternalSelfDeliveryPolicyV1,
)
from tests.support.capabilities.deployment_trust.delivery_grant_v2 import (
    grant,
    grant_anchor,
    head,
    issue_request,
    observation,
    policy_anchor,
    policy_terms,
    revoke_request,
    scope,
)


def payload_for(
    request: IssuePreparedExternalDeliveryGrantRequestV2
    | RevokePreparedExternalDeliveryGrantRequestV2,
) -> OperatorGrantAuthorizationPayloadV2:
    raw = request.canonical_bytes()
    return OperatorGrantAuthorizationPayloadV2(
        algorithm="Ed25519",
        source_id="grant-operator-source",
        operator_key_id="operator-key",
        tenant_id=request.tenant_id,
        database_id=request.database_id,
        grant_id=request.grant_id,
        policy_id=request.policy_id,
        command_id=request.command_id,
        operation=request.operation,
        request_sha256=hashlib.sha256(raw).hexdigest(),
        canonical_request_bytes=raw,
    )


@pytest.mark.parametrize("grant_request", [issue_request(), revoke_request()])
def test_v2_requests_and_signed_sources_roundtrip_canonical_json(
    grant_request: IssuePreparedExternalDeliveryGrantRequestV2
    | RevokePreparedExternalDeliveryGrantRequestV2,
) -> None:
    payload = payload_for(grant_request)
    signed = SignedOperatorGrantAuthorizationV2(payload=payload, signature=bytes(range(64)))
    raw = signed.canonical_bytes()
    retained = RetainedOperatorGrantAuthorizationSourceV2(
        ref=ExactHead(
            identity="grant-operator-source",
            head=operator_grant_source_content_head_v2(raw),
            fingerprint=hashlib.sha256(raw).hexdigest(),
        ),
        canonical_source_bytes=raw,
    )

    assert type(grant_request).model_validate_json(grant_request.canonical_bytes()) == grant_request
    assert SignedOperatorGrantAuthorizationV2.model_validate_json(raw) == signed
    assert (
        RetainedOperatorGrantAuthorizationSourceV2.model_validate_json(retained.canonical_bytes())
        == retained
    )


@pytest.mark.parametrize(
    "authority",
    ["communication_authority", "disclosure_authority", "self_recipient_binding"],
)
def test_v2_grant_rejects_each_mixed_policy_authority(authority: str) -> None:
    data = grant().model_dump(mode="json")
    data["scope"]["mandate"][authority] = head("other-policy").model_dump(mode="json")

    with pytest.raises(ValidationError, match="authorities must equal"):
        PreparedExternalDeliveryGrantV2.model_validate_json(json.dumps(data))


@pytest.mark.parametrize(
    ("revision", "predecessor"),
    [
        (0, head("grant").model_dump(mode="json")),
        (1, None),
        (1, head("other-grant").model_dump(mode="json")),
    ],
)
def test_v2_grant_rejects_malformed_predecessor_lineage(
    revision: int, predecessor: dict[str, str] | None
) -> None:
    data = grant().model_dump(mode="json")
    data["revision"] = revision
    data["predecessor"] = predecessor

    with pytest.raises(ValidationError, match=r"genesis grant|predecessor belongs"):
        PreparedExternalDeliveryGrantV2.model_validate_json(json.dumps(data))


def test_v2_grant_rejects_genesis_revocation() -> None:
    data = grant().model_dump(mode="json")
    data["status"] = "REVOKED"

    with pytest.raises(ValidationError, match="existing grant revision"):
        PreparedExternalDeliveryGrantV2.model_validate_json(json.dumps(data))


@pytest.mark.parametrize(
    "path",
    [
        ("revision",),
        ("policy", "identity"),
        ("policy", "head"),
        ("policy", "fingerprint"),
    ],
)
def test_issue_rejects_each_mismatched_selected_policy_anchor(path: tuple[str, ...]) -> None:
    data = issue_request().model_dump(mode="json")
    target = data["selected_policy_anchor"]
    for field in path[:-1]:
        target = target[field]
    last = path[-1]
    target[last] = 1 if last == "revision" else ("0" * 64 if last == "fingerprint" else "wrong")

    with pytest.raises(ValidationError, match="selected policy anchor differs"):
        IssuePreparedExternalDeliveryGrantRequestV2.model_validate_json(json.dumps(data))


def test_issue_requires_canonical_active_policy_and_separate_operation_type() -> None:
    request = issue_request()
    revoked = PreparedExternalSelfDeliveryPolicyV1(
        issuer="deployment_trust",
        tenant_id="tenant",
        database_id="database",
        policy_id="policy",
        revision=1,
        predecessor=head("policy"),
        status="REVOKED",
        terms=policy_terms(),
        authorization_command=head("policy-command-2"),
        authorization_source=head("policy-source-2"),
    )
    revoked_bytes = revoked.canonical_bytes()

    with pytest.raises(ValidationError, match="ACTIVE selected policy"):
        IssuePreparedExternalDeliveryGrantRequestV2(
            operation=request.operation,
            command_id=request.command_id,
            tenant_id=request.tenant_id,
            database_id=request.database_id,
            grant_id=request.grant_id,
            policy_id=request.policy_id,
            expected_trust_observation=request.expected_trust_observation,
            expected_grant=request.expected_grant,
            selected_policy_anchor=policy_anchor(revoked, revoked_bytes),
            canonical_selected_policy_bytes=revoked_bytes,
            proposed_scope=scope(policy_anchor(revoked, revoked_bytes).policy),
        )

    data = issue_request().model_dump(mode="json")
    data["operation"] = "REVOKE_PREPARED_EXTERNAL_DELIVERY_GRANT"
    with pytest.raises(ValidationError):
        IssuePreparedExternalDeliveryGrantRequestV2.model_validate(data)


def test_revoke_requires_exact_active_predecessor_but_allows_stale_scope() -> None:
    request = revoke_request()
    stale = request.expected_grant.model_copy(
        update={
            "scope": request.expected_grant.scope.model_copy(
                update={
                    "resources": request.expected_grant.scope.resources.model_copy(
                        update={"clock_epoch": "stale-resource-epoch"}
                    ),
                    "mandate": request.expected_grant.scope.mandate.model_copy(
                        update={"clock_epoch": "stale-resource-epoch"}
                    ),
                }
            )
        }
    )
    # Scope is intentionally historical: it may be stale, but is retained exactly.
    stale_data = request.model_dump(mode="json")
    stale_data["expected_grant"]["scope"]["resources"]["clock_epoch"] = "stale-resource-epoch"
    stale_data["expected_grant"]["scope"]["mandate"]["clock_epoch"] = "stale-resource-epoch"
    stale_value = PreparedExternalDeliveryGrantV2.model_validate_json(
        json.dumps(stale_data["expected_grant"])
    )
    stale_data["expected_grant_anchor"] = grant_anchor(stale_value).model_dump(mode="json")
    restored = RevokePreparedExternalDeliveryGrantRequestV2.model_validate_json(
        json.dumps(stale_data)
    )
    assert restored.expected_grant == stale

    data = request.model_dump(mode="json")
    data["expected_grant"]["status"] = "REVOKED"
    data["expected_grant"]["revision"] = 1
    data["expected_grant"]["predecessor"] = head("grant").model_dump(mode="json")
    revoked_predecessor = PreparedExternalDeliveryGrantV2.model_validate_json(
        json.dumps(data["expected_grant"])
    )
    data["expected_grant_anchor"] = grant_anchor(revoked_predecessor).model_dump(mode="json")
    with pytest.raises(ValidationError, match="ACTIVE predecessor"):
        RevokePreparedExternalDeliveryGrantRequestV2.model_validate_json(json.dumps(data))

    data = request.model_dump(mode="json")
    data["expected_grant_anchor"]["grant"]["fingerprint"] = "0" * 64
    with pytest.raises(ValidationError, match="exact selected grant"):
        RevokePreparedExternalDeliveryGrantRequestV2.model_validate_json(json.dumps(data))


def test_lifecycle_read_does_not_claim_currentness_or_permission() -> None:
    value = grant()
    request = ReadPreparedExternalDeliveryGrantLifecycleV2(
        expected_trust_observation=observation(),
        source_anchor=grant_anchor(value),
        expected_grant=value,
    )
    adapter: TypeAdapter[PreparedExternalDeliveryGrantLifecycleResultV2] = TypeAdapter(
        PreparedExternalDeliveryGrantLifecycleResultV2
    )
    observed = ObservedPreparedExternalDeliveryGrantLifecycleV2(
        status="ACTIVE",
        source_anchor=request.source_anchor,
        trust_observation=request.expected_trust_observation,
        selector_generation=0,
    )

    assert (
        ReadPreparedExternalDeliveryGrantLifecycleV2.model_validate_json(request.canonical_bytes())
        == request
    )
    assert adapter.validate_json(adapter.dump_json(observed)) == observed
    assert isinstance(
        adapter.validate_json(b'{"disposition":"STALE"}'),
        UnobservedPreparedExternalDeliveryGrantLifecycleV2,
    )


def test_v2_signed_source_rejects_v1_wrapper_and_request_mismatch() -> None:
    raw = SignedOperatorPolicyAuthorizationV1.model_construct().model_dump_json().encode()
    with pytest.raises(ValidationError):
        RetainedOperatorGrantAuthorizationSourceV2(
            ref=head("grant-operator-source"), canonical_source_bytes=raw
        )

    data = payload_for(issue_request()).model_dump(mode="json")
    data["operation"] = "REVOKE_PREPARED_EXTERNAL_DELIVERY_GRANT"
    with pytest.raises(ValidationError, match="request"):
        OperatorGrantAuthorizationPayloadV2.model_validate(data)


def test_v1_canonical_grant_bytes_are_stable() -> None:
    # V2 fields and imports must not alter the registered V1 JSON canonicalization.
    from tests.support.capabilities.deployment_trust.prepared_external_delivery_contracts import (
        grant as v1_grant,
    )

    value: PreparedExternalDeliveryGrantV1 = v1_grant()
    assert hashlib.sha256(value.canonical_bytes()).hexdigest() == (
        "646aa154568672ba26825824670555310964f5fe0a4aebf46e8703414159ba78"
    )
