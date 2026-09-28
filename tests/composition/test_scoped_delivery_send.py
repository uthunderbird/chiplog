"""Public H1 scoped-delivery SEND boundary.

The retained V3 fixture is deliberately kept outside the legacy R16 dispatch
graph.  This test becomes executable once composition mounts the selected
source reader and scoped authorization/first-SEND route.
"""

import hashlib

import pytest

from chiplog.capabilities.effects.contracts import ExactHead
from chiplog.capabilities.effects.lifecycle_transition_contracts import SelectedEffectsSource
from chiplog.capabilities.effects.scoped_delivery_source_contracts import (
    decode_selected_delivery_intent,
)
from chiplog.capabilities.effects.scoped_intent_contracts import (
    PreparedDeliveryAuthority,
    PreparedDeliveryOriginV3,
)
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.composition.r16_dispatch_runtime import R16DispatchRuntime
from tests.support.scoped_intent_records import delivery_publication_fixture, head


async def _selected_prepared_delivery() -> SelectedEffectsSource:
    fixture = await delivery_publication_fixture()
    raw = fixture.intent.canonical_bytes()
    reference = ExactHead(
        subject_id=fixture.intent.intent_id,
        head=fixture.intent.intent_id,
        fingerprint=hashlib.sha256(raw).hexdigest(),
    )
    return SelectedEffectsSource(
        owner="effects",
        subject=reference,
        schema_id=fixture.intent.schema_id,
        canonical_record_bytes=raw,
        selected_decision=head("selected-prepared-delivery"),
        physical_record=reference,
    )


@pytest.mark.asyncio
async def test_public_scoped_v3_send_boundary_requires_selected_delivery_route() -> None:
    """A selected prepared delivery must reach CONFIRM once, or reject before I/O.

    The mounted route must consume this selected V3 source, authenticate the
    prepared-delivery origin and authority, recheck the current signed endpoint
    and exact recipient, then prepare authorization and first SEND.  Under
    CONFIRM/cap=1 it must produce one exact-byte provider transfer.  A stale
    endpoint or no selected source must produce no transfer.
    """
    selected = await _selected_prepared_delivery()
    intent = decode_selected_delivery_intent(selected)
    assert isinstance(intent.mandate.origin, PreparedDeliveryOriginV3)
    assert isinstance(intent.acquisition.authority, PreparedDeliveryAuthority)
    assert intent.mandate.origin.binding.selection.recipient == intent.mandate.recipient

    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1)
    provider = resources.require_original_provider()
    # No legacy DispatchRecordV2 is constructed here: the source is a
    # prepared-delivery ExternalActionIntentV3 and has its own V3 route.
    boundary = getattr(R16DispatchRuntime, "send_selected_scoped_delivery_v3", None)
    try:
        assert callable(boundary), (
            "missing public scoped-v3 delivery SEND boundary: mount a selected "
            "ExternalActionIntentV3 reader plus prepared scoped authorization/first-SEND "
            "and broker one-shot provider-consumption route"
        )
    finally:
        assert provider.transfers == ()


def test_h1_v3_delivery_producer_has_a_distinct_authenticated_issuance() -> None:
    """Historical H1/V2 local commentary cannot become a V3 SEND source.

    The fresh producer reuses the existing generic V3-capable completion
    assembly, but its issuance must remain distinct from the selected H1/V2
    local-only issuance.  The issuance carries the authenticated owner
    exchanges that an installed witness will later follow to the selected V3
    effects command and physical ``ExternalActionIntentV3`` record.
    """
    from chiplog.composition import completion_publication_contracts as publication
    from chiplog.composition import h1_completion_issuance as issuance

    v3 = getattr(issuance, "H1CompletionIssuanceV3", None)
    assert v3 is not None, "missing versioned H1 V3 completion issuance"
    assert v3 is not issuance.H1CompletionIssuanceV2

    fields = v3.model_fields
    assert {"assembly", "owner_exchanges", "recovery"} <= set(fields)
    assert fields["assembly"].annotation is publication.PrepareCompleteAcceptanceAssemblyV1
