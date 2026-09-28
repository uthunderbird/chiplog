"""Prepared external-delivery grants are typed claims, never live permission."""

import hashlib
import json

import pytest
from pydantic import TypeAdapter, ValidationError

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead, ProviderRecipient
from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
    HermeticTrustObservationV1,
)
from chiplog.capabilities.deployment_trust.prepared_external_delivery_contracts import (
    BoundedExternalSelfSendMandateV1,
    CurrentPreparedExternalDeliveryGrantResultV1,
    CurrentPreparedExternalDeliveryGrantV1,
    ExternalDeliveryResourcesV1,
    PreparedExternalDeliveryGrantAnchorV1,
    PreparedExternalDeliveryGrantV1,
    ReadCurrentPreparedExternalDeliveryGrantV1,
    SelectedExternalDeliverySourceV1,
)


def head(identity: str, body: bytes = b"inert") -> ExactHead:
    return ExactHead(
        identity=identity,
        head=identity,
        fingerprint=hashlib.sha256(body).hexdigest(),
    )


def grant() -> PreparedExternalDeliveryGrantV1:
    return PreparedExternalDeliveryGrantV1(
        issuer="deployment_trust",
        tenant_id="tenant",
        database_id="database",
        grant_id="grant-0",
        revision=0,
        predecessor=None,
        principal_id="principal",
        worker_session_id="worker-session",
        contour_head="contour",
        authenticated_credential_head="credential",
        authenticated_session_head="session",
        selected_source=SelectedExternalDeliverySourceV1(
            source_class="CLI",
            selected_initialization=head("r17-initialization"),
            selected_admission_decision=head("r17-decision"),
            selected_admission_record=head("r17-record"),
            admitted_authentication=head("r17-authentication"),
            ingress_binding=head("r17-ingress"),
        ),
        resources=ExternalDeliveryResourcesV1(
            signature_domain="dispatch-resources.v1",
            signed_observation_fingerprint=hashlib.sha256(b"r16-observation").hexdigest(),
            resource_grant=head("r16-grant"),
            recipient=ProviderRecipient(
                provider_id="telegram",
                account_id="self-account",
                recipient_id="self-recipient",
                endpoint=head("r16-endpoint"),
                canonical_address=b"telegram://self-account/self-recipient",
                credential_binding=head("r16-credential"),
            ),
            clock_epoch="epoch-7",
        ),
        mandate=BoundedExternalSelfSendMandateV1(
            purpose="PREPARED_EXTERNAL_SELF_SEND",
            external_delivery=True,
            selection="ORIGIN_EXACT",
            payload_class="NonAuthoritativeText",
            communication_authority=head("communication-authority"),
            disclosure_authority=head("disclosure-authority"),
            self_recipient_binding=head("self-recipient-binding"),
            original_run=head("original-run"),
            captured_attempt=head("captured-attempt"),
            preparation_basis=head("preparation-basis"),
            delivery_id="delivery",
            payload_digest=hashlib.sha256(b"prepared payload").hexdigest(),
            payload_byte_length=len(b"prepared payload"),
            max_calls=1,
            clock_contract="clock-contract",
            clock_epoch="epoch-7",
            not_before_ns=100,
            expires_at_ns=200,
        ),
    )


def anchor(value: PreparedExternalDeliveryGrantV1) -> PreparedExternalDeliveryGrantAnchorV1:
    return PreparedExternalDeliveryGrantAnchorV1(
        owner_id="deployment_trust",
        decision=head("trust-decision"),
        record_ordinal=0,
        record_type_id="chiplog.deployment_trust.prepared_external_delivery_grant",
        schema_id="chiplog.deployment_trust.record.v1",
        record=head("trust-record"),
        grant=ExactHead(
            identity=value.grant_id,
            head="trust-grant-payload",
            fingerprint=hashlib.sha256(value.canonical_bytes()).hexdigest(),
        ),
        revision=value.revision,
    )


def current_request() -> ReadCurrentPreparedExternalDeliveryGrantV1:
    value = grant()
    return ReadCurrentPreparedExternalDeliveryGrantV1(
        expected_trust_observation=HermeticTrustObservationV1(
            physical_journal_head=head("trust-physical-journal"),
            logical_snapshot_head="trust-logical-snapshot",
        ),
        source_anchor=anchor(value),
        expected_grant=value,
    )


def replace_json_field(data: dict[str, object], path: tuple[str, ...], replacement: object) -> None:
    parent = data
    for field in path[:-1]:
        child = parent[field]
        assert isinstance(child, dict)
        parent = child
    parent[path[-1]] = replacement


def test_canonical_grant_anchor_and_current_request_roundtrip() -> None:
    request = current_request()
    restored = ReadCurrentPreparedExternalDeliveryGrantV1.model_validate_json(
        request.canonical_bytes()
    )

    assert restored == request
    assert restored.canonical_bytes() == request.canonical_bytes()
    assert restored.expected_grant.resources.recipient == request.expected_grant.resources.recipient
    assert (
        restored.expected_grant.mandate.payload_digest
        == request.expected_grant.mandate.payload_digest
    )


def test_grant_represents_both_exact_recipient_modes() -> None:
    for selection in ("ORIGIN_EXACT", "MODEL_SELECTED_EXACT"):
        data = grant().model_dump(mode="json")
        data["mandate"]["selection"] = selection
        restored = PreparedExternalDeliveryGrantV1.model_validate_json(json.dumps(data))
        assert restored.mandate.selection == selection


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("external_delivery", False),
        ("purpose", "H1_LOCAL_COMMENTARY"),
    ],
)
def test_hermetic_or_local_policy_cannot_be_recast_as_external_grant(
    field: str, replacement: object
) -> None:
    data = grant().model_dump(mode="json")
    data["mandate"][field] = replacement

    with pytest.raises(ValidationError):
        PreparedExternalDeliveryGrantV1.model_validate_json(json.dumps(data))


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("source_anchor", "grant", "identity"), "other-grant"),
        (("source_anchor", "grant", "fingerprint"), "0" * 64),
        (("source_anchor", "revision"), 1),
    ],
)
def test_current_read_rejects_anchor_that_is_not_the_exact_grant(
    path: tuple[str, ...], replacement: object
) -> None:
    data = current_request().model_dump(mode="json")
    replace_json_field(data, path, replacement)

    with pytest.raises(ValidationError, match="exact selected anchor"):
        ReadCurrentPreparedExternalDeliveryGrantV1.model_validate_json(json.dumps(data))


def test_grant_rejects_empty_mandate_horizon() -> None:
    data = grant().model_dump(mode="json")
    data["mandate"]["expires_at_ns"] = data["mandate"]["not_before_ns"]

    with pytest.raises(ValidationError, match="nonempty horizon"):
        PreparedExternalDeliveryGrantV1.model_validate_json(json.dumps(data))


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("resources", "clock_epoch"), "other-epoch"),
        (("resources", "recipient", "canonical_address"), ""),
    ],
)
def test_grant_rejects_resource_claims_that_cannot_bound_external_delivery(
    path: tuple[str, ...], replacement: str
) -> None:
    data = grant().model_dump(mode="json")
    replace_json_field(data, path, replacement)

    with pytest.raises(ValidationError):
        PreparedExternalDeliveryGrantV1.model_validate_json(json.dumps(data))


@pytest.mark.parametrize(
    ("revision", "predecessor"),
    [(0, head("previous").model_dump(mode="json")), (1, None)],
)
def test_grant_rejects_broken_selected_history_lineage(
    revision: int, predecessor: dict[str, str] | None
) -> None:
    data = grant().model_dump(mode="json")
    data["revision"] = revision
    data["predecessor"] = predecessor

    with pytest.raises(ValidationError, match="genesis grant"):
        PreparedExternalDeliveryGrantV1.model_validate_json(json.dumps(data))


def test_current_result_union_is_closed_and_requires_current_locator() -> None:
    adapter: TypeAdapter[CurrentPreparedExternalDeliveryGrantResultV1] = TypeAdapter(
        CurrentPreparedExternalDeliveryGrantResultV1
    )
    request = current_request()
    current = CurrentPreparedExternalDeliveryGrantV1(
        disposition="CURRENT",
        source_anchor=request.source_anchor,
        trust_observation=request.expected_trust_observation,
        selector_generation=0,
    )

    assert adapter.validate_json(adapter.dump_json(current)) == current
    for disposition in ("STALE", "DENIED", "UNSUPPORTED"):
        result = adapter.validate_json(json.dumps({"disposition": disposition}))
        assert result.disposition == disposition
    for invalid in ({"disposition": "CURRENT"}, {"disposition": "VALID"}):
        with pytest.raises(ValidationError):
            adapter.validate_json(json.dumps(invalid))
