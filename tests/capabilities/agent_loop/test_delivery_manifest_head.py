"""Stable logical reference for the embedded delivery manifest."""

import hashlib

from tests.support.scoped_intent_records import delivery_publication_fixture

from chiplog.capabilities.agent_loop.delivery_preparation import DeliveryAcceptanceProposal
from chiplog.capabilities.effects.scoped_intent_contracts import PreparedDeliveryAuthority


async def test_embedded_manifest_reference_is_stable_and_changes_with_contents() -> None:
    from chiplog.capabilities.agent_loop.delivery_contracts import delivery_manifest_head

    fixture = await delivery_publication_fixture()
    assert isinstance(fixture.intent.acquisition.authority, PreparedDeliveryAuthority)
    proposal = DeliveryAcceptanceProposal.model_validate_json(
        fixture.intent.acquisition.authority.basis.loop_proposal_bytes
    )
    manifest = proposal.manifest
    reference = delivery_manifest_head(manifest)
    digest = hashlib.sha256(manifest.canonical_bytes()).hexdigest()
    assert reference.identity == manifest.manifest_id
    assert reference.head == "delivery-manifest:" + digest
    assert reference.fingerprint == digest
    assert (
        delivery_manifest_head(type(manifest).model_validate_json(manifest.canonical_bytes()))
        == reference
    )
    assert delivery_manifest_head(manifest.model_copy(update={"turn_id": "different"})) != reference
