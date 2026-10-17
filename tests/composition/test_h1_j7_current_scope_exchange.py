"""The J7 current-scope exchange export is inert and copied at its boundary."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

import chiplog.composition.h1_scoped_delivery_authority as authority
from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead
from chiplog.capabilities.deployment_trust import TrustReference
from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
    CurrentHermeticExecutionScopeV1,
    HermeticOutputScopeAnchorV1,
    HermeticTrustObservationV1,
    ReadCurrentHermeticExecutionScopeV1,
    SelectedHermeticResourceObservationRefV1,
)
from chiplog.composition.common_cli_execution_runtime import open_installed_h1_scoped_runtime
from chiplog.composition.h1_j7_current_scope_exchange import H1J7CurrentScopeExchangeV1
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.h1_scoped_delivery_authority import (
    H1ScopedDeliveryAuthorityCapture,
    H1ScopedDeliveryAuthorityReader,
    H1ScopedDeliveryAuthorityViolation,
)
from chiplog.domain_primitives import PrincipalId, TenantId
from chiplog.platform.broker import BrokerSession, CallBudget, PublicPortCall, PublicPortSuccess
from tests.support.h1_installed_launch import installed_slot, prepare_installed_slot
from tests.support.h1_scoped_first_b import (
    issue_real_h1_scoped_authority,
    scoped_resources,
    write_scoped_pins,
)


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


@pytest.mark.asyncio
async def test_mounted_ordinary_capture_exports_the_exact_retained_scope_wire(
    tmp_path: Path,
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    write_scoped_pins(slot.database_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_scoped_runtime(
            launch, resources=scoped_resources(tmp_path)
        ) as runtime:
            reader, capture = await issue_real_h1_scoped_authority(runtime)
            exported = reader.export_current_scope_exchange(capture)
            record = capture._record
            wire = record.fresh_scope_wire

            assert exported.request == record.fresh_scope_request
            assert exported.result == record.fresh_scope_result
            assert exported.sent == wire.sent
            assert exported.returned == wire.returned
            assert (exported.sent_at_ns, exported.returned_at_ns) == (
                wire.sent_at_ns,
                wire.returned_at_ns,
            )
            assert reader.export_current_scope_exchange(capture).canonical_bytes() == (
                exported.canonical_bytes()
            )


@pytest.mark.asyncio
async def test_mounted_export_rechecks_current_resource_and_trust_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    write_scoped_pins(slot.database_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_scoped_runtime(
            launch, resources=scoped_resources(tmp_path)
        ) as runtime:
            reader, capture = await issue_real_h1_scoped_authority(runtime)
            assert reader.export_current_scope_exchange(capture).request
            runtime._require_dispatch_resources().revoke()
            with pytest.raises(H1ScopedDeliveryAuthorityViolation):
                reader.export_current_scope_exchange(capture)

    trust_source_tmp_path = tmp_path / "trust-source-change"
    trust_source_tmp_path.mkdir()
    slot, expected = installed_slot(trust_source_tmp_path)
    await prepare_installed_slot(slot, expected, trust_source_tmp_path)
    write_scoped_pins(slot.database_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_scoped_runtime(
            launch, resources=scoped_resources(trust_source_tmp_path)
        ) as runtime:
            reader, capture = await issue_real_h1_scoped_authority(runtime)
            original = authority.H1ScopedDeliveryAuthorityReader._evidence

            def changed_evidence(
                issuer: authority.H1ScopedDeliveryAuthorityReader, grant: object, policy: object
            ) -> object:
                evidence = original(issuer, grant, policy)
                rebuilt = evidence.model_dump(mode="python")
                rebuilt["trust_observation"]["logical_snapshot_head"] = "advanced-trust-head"
                return type(evidence).model_validate(rebuilt)

            monkeypatch.setattr(
                authority.H1ScopedDeliveryAuthorityReader, "_evidence", changed_evidence
            )
            with pytest.raises(
                H1ScopedDeliveryAuthorityViolation, match="authority sources changed"
            ):
                reader.export_current_scope_exchange(capture)


@pytest.mark.asyncio
async def test_mounted_export_refreshes_to_a_new_current_scope_exchange(
    tmp_path: Path,
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    write_scoped_pins(slot.database_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_scoped_runtime(
            launch, resources=scoped_resources(tmp_path)
        ) as runtime:
            reader, capture = await issue_real_h1_scoped_authority(runtime)
            original = reader.export_current_scope_exchange(capture)
            refreshed = await reader.refresh(capture)
            renewed = reader.export_current_scope_exchange(refreshed)

            assert refreshed is not capture
            assert renewed.request == refreshed._record.fresh_scope_request
            assert renewed.returned.request_id == renewed.sent.request_id
            assert renewed.canonical_bytes() != original.canonical_bytes()
