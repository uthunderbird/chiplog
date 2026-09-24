"""Selected delivery source bytes, not proof of journal selection or SEND authority."""

import hashlib

import pytest
from pydantic import ValidationError
from tests.support.scoped_intent_records import delivery_publication_fixture, head

from chiplog.capabilities.effects.contracts import ExactHead
from chiplog.capabilities.effects.lifecycle_transition_contracts import SelectedEffectsSource
from chiplog.capabilities.effects.scoped_delivery_source_contracts import (
    FIRST_SEND_SCHEMA_ID,
    SCOPED_DELIVERY_ROUTE_V3,
    ScopedDeliveryRouteV3,
    ScopedDeliverySourceIntegrityError,
    decode_selected_delivery_first_send,
    decode_selected_delivery_intent,
    scoped_first_send_head,
)
from chiplog.capabilities.effects.scoped_dispatch_contracts import ScopedFirstSendDecision


async def sources() -> tuple[SelectedEffectsSource, SelectedEffectsSource]:
    fixture = await delivery_publication_fixture()
    raw = fixture.intent.canonical_bytes()
    ref = ExactHead(
        subject_id=fixture.intent.intent_id,
        head=fixture.intent.intent_id,
        fingerprint=hashlib.sha256(raw).hexdigest(),
    )
    intent = SelectedEffectsSource(
        owner="effects",
        subject=ref,
        physical_record=ref,
        schema_id=fixture.intent.schema_id,
        canonical_record_bytes=raw,
        selected_decision=head("intent-selected"),
    )
    decision = ScopedFirstSendDecision(
        identity=fixture.request.identity,
        source_request_fingerprint="a" * 64,
        original_intent=ref,
        authorization=head("authorization"),
        prior_parent=head("parent"),
        current_inputs_fingerprint="b" * 64,
        complete_current_origin_sources=fixture.request.complete_current_origin_sources,
        fence=fixture.request.fence,
    )
    ref = scoped_first_send_head(decision)
    send = SelectedEffectsSource(
        owner="effects",
        subject=ref,
        physical_record=ref,
        schema_id=FIRST_SEND_SCHEMA_ID,
        canonical_record_bytes=decision.canonical_bytes(),
        selected_decision=head("send-selected"),
    )
    return intent, send


async def test_selected_delivery_v3_and_first_send_decode_exact_sources() -> None:
    intent, send = await sources()
    assert (
        decode_selected_delivery_intent(intent).canonical_bytes() == intent.canonical_record_bytes
    )
    decision = decode_selected_delivery_first_send(send, selected_intent=intent)
    assert decision.original_intent == intent.subject
    assert scoped_first_send_head(decision) == send.physical_record


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_id", "chiplog.effects.external-action-intent.v2"),
        ("owner", "agent_loop"),
        ("canonical_record_bytes", b"{}"),
        ("subject", head("other")),
        ("physical_record", head("other")),
    ],
)
async def test_selected_delivery_rejects_source_substitution(field: str, value: object) -> None:
    intent, _ = await sources()
    with pytest.raises(ScopedDeliverySourceIntegrityError):
        decode_selected_delivery_intent(intent.model_copy(update={field: value}))


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_id", "chiplog.effects.commit-first-send.v2"),
        ("owner", "agent_loop"),
        ("canonical_record_bytes", b"{}"),
        ("subject", head("other")),
        ("physical_record", head("other")),
    ],
)
async def test_selected_first_send_rejects_source_substitution(field: str, value: object) -> None:
    intent, send = await sources()
    with pytest.raises(ScopedDeliverySourceIntegrityError):
        decode_selected_delivery_first_send(
            send.model_copy(update={field: value}), selected_intent=intent
        )


async def test_first_send_rejects_rehashed_wrong_original_intent() -> None:
    intent, send = await sources()
    decision = ScopedFirstSendDecision.model_validate_json(send.canonical_record_bytes)
    changed = decision.model_copy(update={"original_intent": head("other")})
    ref = scoped_first_send_head(changed)
    wrong = send.model_copy(
        update={
            "subject": ref,
            "physical_record": ref,
            "canonical_record_bytes": changed.canonical_bytes(),
        }
    )
    with pytest.raises(ScopedDeliverySourceIntegrityError, match="original intent"):
        decode_selected_delivery_first_send(wrong, selected_intent=intent)


async def test_first_send_rejects_noncanonical_bytes_with_updated_physical_digest() -> None:
    intent, send = await sources()
    raw = send.canonical_record_bytes + b"\n"
    digest = hashlib.sha256(raw).hexdigest()
    ref = send.subject.model_copy(
        update={
            "head": "scoped-first-send:" + digest,
            "fingerprint": digest,
        }
    )
    wrong = send.model_copy(
        update={
            "subject": ref,
            "physical_record": ref,
            "canonical_record_bytes": raw,
        }
    )
    with pytest.raises(ScopedDeliverySourceIntegrityError):
        decode_selected_delivery_first_send(wrong, selected_intent=intent)


def test_delivery_route_rejects_v2_alias() -> None:
    route = SCOPED_DELIVERY_ROUTE_V3.model_dump()
    route["first_send_schema"] = "chiplog.effects.commit-first-send.v2"
    with pytest.raises(ValidationError):
        ScopedDeliveryRouteV3.model_validate(route)
