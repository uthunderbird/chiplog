"""The J7 current-scope exchange export is inert and copied at its boundary."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead
from chiplog.capabilities.deployment_trust import TrustReference
from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
    CurrentHermeticExecutionScopeV1,
    HermeticOutputScopeAnchorV1,
    HermeticTrustObservationV1,
    ReadCurrentHermeticExecutionScopeV1,
    SelectedHermeticResourceObservationRefV1,
)
from chiplog.composition.h1_j7_current_scope_exchange import H1J7CurrentScopeExchangeV1
from chiplog.composition.h1_scoped_delivery_authority import (
    H1ScopedDeliveryAuthorityCapture,
    H1ScopedDeliveryAuthorityReader,
    H1ScopedDeliveryAuthorityViolation,
)
from chiplog.domain_primitives import PrincipalId, TenantId
from chiplog.platform.broker import BrokerSession, CallBudget, PublicPortCall, PublicPortSuccess


def _head(identity: str) -> ExactHead:
    return ExactHead(identity=identity, head=identity + "/head", fingerprint="a" * 64)


def _exchange() -> tuple[
    ReadCurrentHermeticExecutionScopeV1, CurrentHermeticExecutionScopeV1, object
]:
    resource = SelectedHermeticResourceObservationRefV1(
        signature_domain="dispatch-resources.v1",
        selected_initialization=_head("initialization"),
        signed_observation_fingerprint="b" * 64,
    )
    anchor = HermeticOutputScopeAnchorV1(
        owner_id="deployment_trust",
        decision=_head("decision"),
        record_ordinal=0,
        record_type_id="chiplog.deployment_trust.hermetic_output_scope",
        schema_id="chiplog.deployment_trust.record.v1",
        record=_head("record"),
        scope_revision=0,
        predecessor=None,
        selected_resource_observation_ref=resource,
    )
    request = ReadCurrentHermeticExecutionScopeV1(
        expected_trust_observation=HermeticTrustObservationV1(
            physical_journal_head=_head("journal"), logical_snapshot_head="logical"
        ),
        source_anchor=anchor,
        expected_revision=0,
        admitted_authentication_ref=_head("authentication"),
        authenticated_cli_ref=TrustReference(
            tenant_id=TenantId("tenant"),
            principal_id=PrincipalId("principal"),
            contour="CLI",
            credential_head="credential",
            session_head="session",
            source_head="source",
            trust_head="trust",
            materialization_head="materialization",
            freshness_sequence=0,
            peer_credential="peer",
        ),
        tenant_id="tenant",
        database_id="database",
        scope_id="scope",
        expected_scope_ref=_head("scope"),
        expected_worker_session_id="worker",
        selected_resource_observation_ref=resource,
    )
    result = CurrentHermeticExecutionScopeV1(
        disposition="CURRENT",
        scope_ref=_head("scope"),
        source_anchor=anchor,
        selector_generation=0,
        ordered_current_source_refs=(_head("source"),),
    )
    session = BrokerSession(
        tenant_id="tenant",
        broker_epoch=1,
        generation_id="generation",
        owner_id="broker",
        session_id="broker-session",
    )
    owner = BrokerSession(
        tenant_id="tenant",
        broker_epoch=1,
        generation_id="generation",
        owner_id="deployment_trust",
        session_id="owner-session",
    )
    sent = PublicPortCall(
        operation_id="deployment_trust.read_current_hermetic_output_scope",
        request_id="request",
        caller=session,
        callee=owner,
        schema_id="chiplog.deployment-trust.owner-call.v1",
        canonical_payload=b"\xffrequest",
        budget=CallBudget(
            remaining_calls=1, remaining_depth=1, absolute_deadline_ns=30, policy_version=1
        ),
    )
    returned = PublicPortSuccess(
        request_id="request",
        responder=owner,
        schema_id="chiplog.deployment-trust.owner-result.v1",
        canonical_payload=b"\xffresult",
    )
    return (
        request,
        result,
        SimpleNamespace(sent=sent, returned=returned, sent_at_ns=10, returned_at_ns=20),
    )


class _Gate:
    def require_held(self) -> None:
        return None

    def hold(self) -> _Gate:
        return self

    def __enter__(self) -> _Gate:
        return self

    def __exit__(self, *_: object) -> None:
        return None


class _Runtime:
    def _authority_gate(self) -> _Gate:
        return _Gate()


def _reader(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[H1ScopedDeliveryAuthorityReader, H1ScopedDeliveryAuthorityCapture]:
    request, result, wire = _exchange()
    reader = object.__new__(H1ScopedDeliveryAuthorityReader)
    reader._runtime = cast(Any, _Runtime())
    capture = object.__new__(H1ScopedDeliveryAuthorityCapture)
    record = cast(
        Any,
        SimpleNamespace(
            fresh_scope_request=request, fresh_scope_result=result, fresh_scope_wire=wire
        ),
    )
    capture._record = record
    reader._issued = {id(capture): (capture, record)}
    monkeypatch.setattr(H1ScopedDeliveryAuthorityReader, "_verify_record_held", lambda *_: None)
    return reader, capture


def test_export_roundtrips_binary_broker_payload_and_is_mutation_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader, capture = _reader(monkeypatch)

    exported = reader.export_current_scope_exchange(capture)

    assert H1J7CurrentScopeExchangeV1.model_validate_json(exported.canonical_bytes()) == exported
    assert exported.sent.canonical_payload == b"\xffrequest"
    assert exported.returned.canonical_payload == b"\xffresult"
    object.__setattr__(exported.sent, "canonical_payload", b"changed")
    assert reader.export_current_scope_exchange(capture).sent.canonical_payload == b"\xffrequest"
    invalid = exported.model_dump(mode="json")
    invalid["schema_id"] = "chiplog.composition.wrong.v1"
    with pytest.raises(ValueError, match="schema_id"):
        H1J7CurrentScopeExchangeV1.model_validate(invalid)


def test_export_requires_issued_capture_and_dto_cannot_authorize(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader, capture = _reader(monkeypatch)
    forged = object.__new__(H1ScopedDeliveryAuthorityCapture)
    forged._record = capture._record
    exported = reader.export_current_scope_exchange(capture)

    with pytest.raises(H1ScopedDeliveryAuthorityViolation, match="not reader-issued"):
        reader.export_current_scope_exchange(forged)
    with pytest.raises(H1ScopedDeliveryAuthorityViolation, match="not reader-issued"):
        reader.export_current_scope_exchange(exported)  # type: ignore[arg-type]


def test_export_rejects_a_non_successful_retained_response(monkeypatch: pytest.MonkeyPatch) -> None:
    reader, capture = _reader(monkeypatch)
    capture._record.fresh_scope_wire.returned = object()

    with pytest.raises(H1ScopedDeliveryAuthorityViolation, match="not successful"):
        reader.export_current_scope_exchange(capture)
