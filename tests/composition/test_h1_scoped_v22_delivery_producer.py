"""The installed v22 profile selects the V2 H1 owner route."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from chiplog.capabilities.effects.h1_scoped_dispatch_semantics import (
    H1_SCOPED_DISPATCH_V2_SEMANTICS,
    h1_scoped_dispatch_v2_semantic_registry_capture,
)
from chiplog.capabilities.effects.h1_scoped_preparation_v2_contracts import (
    H1ScopedDeliveryOwnerCallV2,
    PreparedH1ScopedDeliveryV2,
)
from chiplog.composition.common_cli_execution_runtime import open_installed_h1_scoped_v22_runtime
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.h1_scoped_delivery_producer_v2 import H1ScopedDeliveryProducerV2
from chiplog.platform.broker import PublicPortRejected, PublicPortSuccess
from tests.support.h1_installed_launch import installed_slot, prepare_installed_slot
from tests.support.h1_scoped_first_b import (
    issue_real_h1_scoped_authority,
    scoped_resources,
    write_scoped_pins,
)


@pytest.mark.asyncio
async def test_mounted_v22_owner_derives_v2_mandate_and_rejects_v1_wire(tmp_path: Path) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    write_scoped_pins(slot.database_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_scoped_v22_runtime(
            launch, resources=scoped_resources(tmp_path)
        ) as runtime:
            _, authority = await issue_real_h1_scoped_authority(runtime)
            exchange = await H1ScopedDeliveryProducerV2(
                runtime, runtime._h1_producer_inventory_reader
            ).prepare(authority_capture=authority)
            call = H1ScopedDeliveryOwnerCallV2.model_validate_json(exchange.sent.canonical_payload)
            result = PreparedH1ScopedDeliveryV2.model_validate_json(
                exchange.returned.canonical_payload
            )
            assert call.route.operation == "effects.prepare_h1_scoped_delivery_v2"
            assert call.request.current.supported_semantics == H1_SCOPED_DISPATCH_V2_SEMANTICS
            assert call.request.original_sources.semantic_registry == (
                h1_scoped_dispatch_v2_semantic_registry_capture(
                    clock_contract=call.request.current.clock_contract,
                    clock_epoch=call.request.current.clock_epoch,
                    valid_until_ns=call.request.original_sources.semantic_registry.valid_until_ns,
                )
            )
            assert result.mandate_bytes == result.intent_request.intent.mandate.canonical_bytes()
            assert result.intent_request.intent.mandate.semantics == H1_SCOPED_DISPATCH_V2_SEMANTICS
            assert (
                result.precursor_result.mandate_fingerprint
                == hashlib.sha256(result.mandate_bytes).hexdigest()
            )
            assert result.source_request_fingerprint == call.request_digest
            assert result.canonical_bytes() == exchange.returned.canonical_payload

            def forged(request):
                forged_call = call.model_copy(
                    update={
                        "request": request,
                        "request_digest": hashlib.sha256(request.canonical_bytes()).hexdigest(),
                    }
                )
                return exchange.sent.model_copy(
                    update={"canonical_payload": forged_call.canonical_bytes()}
                )

            forged_candidate = call.request.current.immutable_mandate_candidate.model_copy(
                update={
                    "canonical_mandate_bytes": b"forged",
                    "mandate_fingerprint": hashlib.sha256(b"forged").hexdigest(),
                }
            )
            forged_sources = call.request.original_sources.model_copy(
                update={
                    "planning": call.request.original_sources.planning.model_copy(
                        update={"candidate": forged_candidate}
                    )
                }
            )
            forged_request = call.request.model_copy(update={"original_sources": forged_sources})
            candidate_reply = await runtime._supervisor.runtime().call(forged(forged_request))
            assert isinstance(candidate_reply, PublicPortSuccess)
            assert json.loads(candidate_reply.canonical_payload)["disposition"] == "DENIED"

            forged_current_sources = call.request.current.sources.model_copy(
                update={
                    "original_adoption": call.request.current.sources.original_adoption.model_copy(
                        update={"candidate": forged_candidate}
                    )
                }
            )
            current_request = call.request.model_copy(
                update={
                    "current": call.request.current.model_copy(
                        update={"sources": forged_current_sources}
                    )
                }
            )
            current_reply = await runtime._supervisor.runtime().call(forged(current_request))
            assert isinstance(current_reply, PublicPortSuccess)
            assert json.loads(current_reply.canonical_payload)["disposition"] == "DENIED"

            forged_retained = call.request.retained_sources[0].model_copy(
                update={"canonical_record_bytes": b"forged"}
            )
            retained_request = call.request.model_copy(
                update={"retained_sources": (forged_retained, *call.request.retained_sources[1:])}
            )
            retained_reply = await runtime._supervisor.runtime().call(forged(retained_request))
            assert isinstance(retained_reply, PublicPortSuccess)
            assert json.loads(retained_reply.canonical_payload)["disposition"] == "DENIED"

            v1_wire = call.model_copy(
                update={"schema_id": "chiplog.effects.h1-scoped-delivery-owner-call.v1"}
            ).canonical_bytes()
            rejected = await runtime._supervisor.runtime().call(
                exchange.sent.model_copy(update={"canonical_payload": v1_wire})
            )
            assert isinstance(rejected, PublicPortRejected)
            assert rejected.failure.kind == "PROTOCOL_REJECTED"
