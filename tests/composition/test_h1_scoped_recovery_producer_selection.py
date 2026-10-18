"""Unit routing checks; these do not authenticate a producer inventory."""

from dataclasses import replace
from unittest.mock import create_autospec

import pytest

from chiplog.architecture.r7_runtime import (
    R14_R17_H1_SCOPED_EFFECTS_J7_PRODUCTION_MANIFEST,
    R14_R17_H1_SCOPED_EFFECTS_V2_J7_PRODUCTION_MANIFEST,
    R14_R17_H1_SCOPED_EFFECTS_V3_J7_PRODUCTION_MANIFEST,
    RuntimeAssemblyManifest,
    bind_execution_manifest,
)
from chiplog.architecture.r8_execution_identity import (
    ResolvedRuntimeIdentity,
    RuntimeSourceIdentity,
)
from chiplog.capabilities.effects.h1_scoped_preparation_v2_contracts import (
    PrepareH1ScopedDeliveryV2,
)
from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
from chiplog.composition.h1_completion_preparation_session import H1CompletionPreparationSession
from chiplog.composition.h1_producer_inventory import H1ProducerInventoryReader
from chiplog.composition.h1_scoped_delivery_producer import H1ScopedDeliveryProducer
from chiplog.composition.h1_scoped_delivery_producer_v2 import H1ScopedDeliveryProducerV2
from chiplog.composition.r7_supervisor import R7RuntimeSupervisor
from tests.support.capabilities.effects.h1_scoped_preparation import _call


@pytest.mark.parametrize(
    "template,tampered,expected",
    [
        (R14_R17_H1_SCOPED_EFFECTS_J7_PRODUCTION_MANIFEST, False, H1ScopedDeliveryProducer),
        (R14_R17_H1_SCOPED_EFFECTS_V3_J7_PRODUCTION_MANIFEST, False, H1ScopedDeliveryProducer),
        (R14_R17_H1_SCOPED_EFFECTS_V2_J7_PRODUCTION_MANIFEST, False, H1ScopedDeliveryProducerV2),
        (R14_R17_H1_SCOPED_EFFECTS_V2_J7_PRODUCTION_MANIFEST, True, H1ScopedDeliveryProducer),
    ],
)
async def test_bound_profile_selects_new_producer_and_retained_wire_keeps_its_version(
    template: RuntimeAssemblyManifest,
    tampered: bool,
    expected: type[H1ScopedDeliveryProducer],
) -> None:
    identity = ResolvedRuntimeIdentity(
        RuntimeSourceIdentity("development", "0" * 64),
        "r8-development-v1:" + "0" * 64,
    )
    runtime = object.__new__(CommonCliExecutionRuntime)
    runtime._supervisor = create_autospec(R7RuntimeSupervisor, instance=True)
    runtime._supervisor._execution_identity = identity
    manifest = bind_execution_manifest(template, identity)
    runtime._supervisor._manifest = (
        replace(manifest, broker_version="foreign-broker-version") if tampered else manifest
    )
    inventory = object.__new__(H1ProducerInventoryReader)
    inventory._runtime = runtime
    runtime._h1_producer_inventory_reader = inventory

    selected = H1CompletionPreparationSession._scoped_recovery_producer(
        runtime, semantic_input=None
    )
    assert type(selected) is expected
    assert selected._inventory_reader is inventory
    retained_request = (await _call()).request
    retained = retained_request.canonical_bytes()
    replay = H1CompletionPreparationSession._scoped_recovery_producer(
        runtime, semantic_input=retained
    )
    assert type(replay) is H1ScopedDeliveryProducer
    assert replay._inventory_reader is inventory
    v2_fields = {
        name: getattr(retained_request, name) for name in type(retained_request).model_fields
    }
    v2_fields["schema_id"] = "chiplog.effects.prepare-h1-scoped-delivery.v2"
    retained_v2 = PrepareH1ScopedDeliveryV2.model_validate(v2_fields).canonical_bytes()
    replay_v2 = H1CompletionPreparationSession._scoped_recovery_producer(
        runtime, semantic_input=retained_v2
    )
    assert type(replay_v2) is H1ScopedDeliveryProducerV2
    assert replay_v2._inventory_reader is inventory
