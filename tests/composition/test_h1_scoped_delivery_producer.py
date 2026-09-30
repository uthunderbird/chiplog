"""Mounted first-B H1 scoped Effects producer boundary."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from chiplog.capabilities.effects.h1_scoped_preparation_contracts import (
    H1ScopedDeliveryOwnerCallV1,
    PreparedH1ScopedDeliveryV1,
)
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
async def test_real_resource_revoke_after_inventory_read_prevents_owner_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    write_scoped_pins(slot.database_path)
    resources = scoped_resources(tmp_path)

    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_scoped_runtime(
            launch, resources=resources
        ) as runtime:
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
