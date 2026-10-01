"""Mounted first-B H1 scoped Effects producer boundary."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from chiplog.capabilities.effects.contracts import CommandIdentity, ExactHead
from chiplog.capabilities.effects.dispatch_v2 import reference
from chiplog.capabilities.effects.dispatch_v2_contracts import DispatchBoundaryFailureV2
from chiplog.capabilities.effects.fences import Absent
from chiplog.capabilities.effects.h1_producer_semantics import (
    H1_PRODUCER_SEMANTICS,
    h1_producer_semantic_registry_capture,
)
from chiplog.capabilities.effects.h1_scoped_preparation_contracts import (
    H1ScopedDeliveryOwnerCallV1,
    PreparedH1ScopedDeliveryV1,
)
from chiplog.capabilities.effects.lifecycle_transition_contracts import SelectedEffectsSource
from chiplog.capabilities.effects.scoped_authorization_record_contracts import (
    make_scoped_authorization_owner_records,
    scoped_dispatch_authorization_head,
    scoped_pre_send_revision_head,
)
from chiplog.capabilities.effects.scoped_dispatch_contracts import (
    PrepareScopedAuthorization,
    PrepareScopedFirstSend,
    ScopedPreSendCut,
    ScopedPreSendRevision,
    SelectedScopedAuthorization,
)
from chiplog.capabilities.effects.scoped_dispatch_preparation import (
    prepare_scoped_authorization,
    prepare_scoped_first_send,
)
from chiplog.capabilities.effects.scoped_intent_contracts import (
    ScopedAuthorityRecord,
    ScopedDispatchAuthorizationRecord,
)
from chiplog.capabilities.effects.scoped_intent_record_contracts import make_scoped_intent_member
from chiplog.composition.common_cli_execution_runtime import open_installed_h1_scoped_runtime
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.h1_producer_inventory import H1ProducerInventoryReader
from chiplog.composition.h1_scoped_delivery_producer import H1ScopedDeliveryProducer
from tests.support.h1_installed_launch import installed_slot, prepare_installed_slot
from tests.support.h1_scoped_first_b import (
    issue_real_h1_scoped_authority,
    scoped_resources,
    write_scoped_pins,
)
from tests.support.scoped_intent_records import delivery_publication_fixture


def _head(name: str) -> ExactHead:
    return reference(name, name.encode())


def _source(name: str) -> ScopedAuthorityRecord:
    return ScopedAuthorityRecord(
        owner="effects",
        head=_head(name),
        schema_id="chiplog.effects.authority.v3",
        canonical_record_bytes=name.encode(),
        selected_decision=_head("decision:" + name),
    )


def _selected_intent(intent) -> SelectedEffectsSource:
    member = make_scoped_intent_member(intent)
    head = ExactHead(
        subject_id=member.record_id,
        head=member.record_id,
        fingerprint=member.fingerprint,
    )
    return SelectedEffectsSource(
        owner="effects",
        subject=head,
        schema_id="chiplog.effects.external-action-intent.v3",
        canonical_record_bytes=member.canonical_record_bytes,
        selected_decision=_head("decision:intent"),
        physical_record=head,
    )


async def _actual_h1_old_semantics_requests(
    tmp_path: Path,
) -> tuple[PrepareScopedAuthorization, PrepareScopedFirstSend]:
    """Adapt an actual mounted H1 V1 output without altering its intent bytes."""
    fixture = await delivery_publication_fixture()
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    write_scoped_pins(slot.database_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_scoped_runtime(
            launch, resources=scoped_resources(tmp_path)
        ) as runtime:
            _, authority = await issue_real_h1_scoped_authority(runtime)
            exchange = await H1ScopedDeliveryProducer(
                runtime, runtime._h1_producer_inventory_reader
            ).prepare(authority_capture=authority)
    result = PreparedH1ScopedDeliveryV1.model_validate_json(exchange.returned.canonical_payload)
    intent = result.intent_request.intent
    horizon = intent.mandate.horizon
    identity = CommandIdentity(
        command_id="hold-actual-h1",
        fingerprint="a" * 64,
        expected_tenant_head=fixture.request.current.observation.cut.tenant_frontier,
    )
    registry = h1_producer_semantic_registry_capture(
        clock_contract=horizon.clock_contract,
        clock_epoch=horizon.clock_epoch,
        valid_until_ns=horizon.expires_at_ns,
    )
    current = fixture.request.current.model_copy(
        update={
            "command_fingerprint": identity.fingerprint,
            "immutable_mandate": reference(
                intent.mandate.mandate_id, intent.mandate.canonical_bytes()
            ),
            "supported_semantics": H1_PRODUCER_SEMANTICS,
            "clock_contract": horizon.clock_contract,
            "clock_epoch": horizon.clock_epoch,
            "observed_time_ns": horizon.not_before_ns,
            "lease_expires_at_ns": horizon.expires_at_ns,
            "observation": fixture.request.current.observation.model_copy(
                update={
                    "query_fingerprint": identity.fingerprint,
                    "cut": fixture.request.current.observation.cut.model_copy(
                        update={
                            "tenant_id": intent.mandate.tenant_id,
                            "source_role_registry": intent.mandate.operation_profile,
                        }
                    ),
                    "sources": fixture.request.current.observation.sources.model_copy(
                        update={"semantic_registry": registry}
                    ),
                }
            ),
        }
    )
    selected_intent = _selected_intent(intent)
    authorization_request = PrepareScopedAuthorization(
        identity=identity,
        observed=ScopedPreSendCut(
            original_intent=intent,
            selected_intent=selected_intent,
            current_parent=selected_intent,
            state="INTENT_RECORDED",
            authorization=Absent(),
        ),
        current=current,
        complete_current_origin_sources=(_source("actual-h1-current"),),
        fence=fixture.request.fence,
    )
    authorization = ScopedDispatchAuthorizationRecord(
        authorization_id="actual-h1-authorization",
        command=identity,
        original_intent=selected_intent.subject,
        expected_parent=selected_intent.subject,
        immutable_mandate=current.immutable_mandate,
        semantics=intent.mandate.semantics,
        complete_current_origin_sources=(_source("actual-h1-current"),),
        current_inputs_fingerprint=hashlib.sha256(current.canonical_bytes()).hexdigest(),
        fence=fixture.request.fence,
    )
    parent = ScopedPreSendRevision(
        original_intent=selected_intent.subject,
        predecessor=selected_intent.subject,
        state="DISPATCH_AUTHORIZED",
        authorization=scoped_dispatch_authorization_head(authorization),
        retired_authorizations=(),
        current_inputs_fingerprint=authorization.current_inputs_fingerprint,
        complete_current_origin_sources=authorization.complete_current_origin_sources,
    )
    row, _ = make_scoped_authorization_owner_records(authorization, parent)
    auth_head = scoped_dispatch_authorization_head(authorization)
    physical = ExactHead(subject_id=row.record_id, head=row.record_id, fingerprint=row.fingerprint)
    selected_authorization = SelectedScopedAuthorization(
        source=SelectedEffectsSource(
            owner="effects",
            subject=auth_head,
            schema_id="chiplog.effects.scoped-authorization.v3",
            canonical_record_bytes=row.canonical_bytes,
            selected_decision=_head("decision:authorization"),
            physical_record=physical,
        ),
        record=authorization,
    )
    parent_raw = parent.canonical_bytes()
    parent_head = scoped_pre_send_revision_head(parent)
    selected_parent = SelectedEffectsSource(
        owner="effects",
        subject=parent_head,
        schema_id="chiplog.effects.scoped-pre-send-revision.v3",
        canonical_record_bytes=parent_raw,
        selected_decision=_head("decision:parent"),
        physical_record=ExactHead(
            subject_id=parent_head.head, head=parent_head.head, fingerprint=parent_head.fingerprint
        ),
    )
    first_send = PrepareScopedFirstSend(
        identity=identity,
        observed=ScopedPreSendCut(
            original_intent=intent,
            selected_intent=selected_intent,
            current_parent=selected_parent,
            state="DISPATCH_AUTHORIZED",
            authorization=selected_authorization,
        ),
        current=current,
        complete_current_origin_sources=authorization.complete_current_origin_sources,
        fence=fixture.request.fence,
        selected_authorization=auth_head,
        ordinal=0,
    )
    return authorization_request, first_send


@pytest.mark.asyncio
async def test_mounted_v20_effects_owner_prepares_exact_first_b_inventory_call(
    tmp_path: Path,
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    write_scoped_pins(slot.database_path)

    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_scoped_runtime(
            launch, resources=scoped_resources(tmp_path)
        ) as runtime:
            _, authority = await issue_real_h1_scoped_authority(runtime)
            inventory_reader = runtime._h1_producer_inventory_reader

            exchange = await H1ScopedDeliveryProducer(runtime, inventory_reader).prepare(
                authority_capture=authority
            )

    assert exchange.role == "effects"
    assert exchange.sent.operation_id == "effects.prepare_h1_scoped_delivery"
    assert exchange.sent.schema_id == "chiplog.effects.h1-scoped-delivery-owner-call.v1"
    assert exchange.returned.schema_id == "chiplog.effects.prepared-h1-scoped-delivery.v1"
    assert exchange.returned.request_id == exchange.sent.request_id
    assert exchange.returned.responder == exchange.sent.callee
    assert exchange.sent_at_ns <= exchange.returned_at_ns
    call = H1ScopedDeliveryOwnerCallV1.model_validate_json(exchange.sent.canonical_payload)
    result = PreparedH1ScopedDeliveryV1.model_validate_json(exchange.returned.canonical_payload)
    assert call.canonical_bytes() == exchange.sent.canonical_payload
    assert result.canonical_bytes() == exchange.returned.canonical_payload
    assert call.request_digest == hashlib.sha256(call.request.canonical_bytes()).hexdigest()
    assert result.source_request_fingerprint == call.request_digest
    assert result.intent_request.identity == call.request.identity
    assert result.intent_request.intent.intent_id == call.request.intent_id
    assert result.retained_sources == call.request.retained_sources


@pytest.mark.asyncio
async def test_actual_h1_producer_v1_intent_holds_before_authorization_and_first_send(
    tmp_path: Path,
) -> None:
    authorization, first_send = await _actual_h1_old_semantics_requests(tmp_path)
    original_bytes = authorization.observed.selected_intent.canonical_record_bytes

    results = (prepare_scoped_authorization(authorization), prepare_scoped_first_send(first_send))
    for result in results:
        assert isinstance(result, DispatchBoundaryFailureV2)
        assert result.kind == "DISPATCH_VERSION_HOLD"
        assert result.reason == "scoped dispatch registry is malformed"

    assert authorization.observed.selected_intent.canonical_record_bytes == original_bytes


@pytest.mark.asyncio
async def test_real_resource_revoke_after_inventory_read_prevents_owner_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    write_scoped_pins(slot.database_path)
    resources = scoped_resources(tmp_path)

    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_scoped_runtime(launch, resources=resources) as runtime:
            _, authority = await issue_real_h1_scoped_authority(runtime)
            inventory_reader = runtime._h1_producer_inventory_reader
            original_read = H1ProducerInventoryReader.read
            engine = runtime._supervisor.runtime()
            original_call = type(engine).call
            owner_calls = 0
            revoked_after_read = False

            async def read_then_revoke(self, *, authority_capture):
                nonlocal revoked_after_read
                capture = await original_read(self, authority_capture=authority_capture)
                resources.revoke()
                revoked_after_read = True
                return capture

            async def count_owner_calls(self, sent):
                nonlocal owner_calls
                owner_calls += 1
                return await original_call(self, sent)

            with monkeypatch.context() as patch:
                patch.setattr(H1ProducerInventoryReader, "read", read_then_revoke)
                patch.setattr(type(engine), "call", count_owner_calls)
                with pytest.raises(PermissionError):
                    await H1ScopedDeliveryProducer(runtime, inventory_reader).prepare(
                        authority_capture=authority
                    )

            assert revoked_after_read is True
            assert owner_calls == 0
