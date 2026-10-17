"""Prepared external-delivery grants are typed claims, never live permission."""

import hashlib
import json

import pytest
from pydantic import TypeAdapter, ValidationError

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead
from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
    HermeticTrustObservationV1,
)
from chiplog.capabilities.deployment_trust.prepared_external_delivery_contracts import (
    CurrentPreparedExternalDeliveryGrantResultV1,
    CurrentPreparedExternalDeliveryGrantV1,
    PreparedExternalDeliveryGrantAnchorV1,
    PreparedExternalDeliveryGrantV1,
    ReadCurrentPreparedExternalDeliveryGrantV1,
)
from tests.support.capabilities.deployment_trust.prepared_external_delivery_contracts import (
    grant,
    head,
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
