"""Focused raw-member checks for the H1 effects/delivery inventory leaf."""

from __future__ import annotations

import hashlib

import pytest

from chiplog.capabilities.effects.dispatch_v2 import reference
from chiplog.composition.h1_inventory_effect_delivery import (
    REGISTRATIONS,
    decode_h1_effect_delivery_scope,
)
from chiplog.composition.h1_inventory_leaf_contracts import (
    H1OwnerInventoryFailure,
    H1RawInventoryItem,
)
from chiplog.composition.r16_dispatch_outbox import ConsumeRequest, ConsumptionRecord
from tests.capabilities.effects.support.dispatch_outcomes import original_send
from tests.support.effects_denial import make_denial_request


def _dispatch_item(
    *, raw: bytes | None = None, record_kind: str = "effects.SEND_COMMITTED"
) -> H1RawInventoryItem:
    record = original_send()
    body = record.canonical_bytes() if raw is None else raw
    return H1RawInventoryItem(
        "PHYSICAL",
        "records/dispatch-send",
        "tenant",
        "effects",
        "chiplog.effects.dispatch-record.v2",
        record_kind,
        record.record.head,
        hashlib.sha256(body).hexdigest(),
        body,
    )


def test_dispatch_member_decodes_all_cross_scope_frontier_before_filtering() -> None:
    decoded = decode_h1_effect_delivery_scope(_dispatch_item())

    assert decoded.locator == "records/dispatch-send"
    assert {(key.namespace, key.identity) for key in decoded.identities} == {
        ("dispatch", "send"),
        ("effect", "send"),
        ("intent", "intent"),
        ("mandate", "mandate"),
    }
    assert {
        (edge.relation, edge.subject.namespace, edge.target.namespace) for edge in decoded.relations
    } == {
        ("DISPATCH_EFFECT", "dispatch", "effect"),
        ("EFFECT_INTENT", "effect", "intent"),
        ("INTENT_MANDATE", "intent", "mandate"),
    }
    assert decoded.source_refs == ()
    assert decoded.families == ("DISPATCH", "EFFECT", "INTENT", "MANDATE")


@pytest.mark.parametrize(
    "item",
    (
        H1RawInventoryItem(
            "PHYSICAL",
            "records/unknown",
            "tenant",
            "effects",
            "future.effects.v9",
            None,
            None,
            None,
            b"{}",
        ),
        H1RawInventoryItem(
            "OWNER_COMMAND",
            "commands/known-schema",
            "tenant",
            "effects",
            "chiplog.effects.dispatch-record.v2",
            None,
            None,
            None,
            b"{}",
        ),
    ),
)
def test_unregistered_pair_is_unsupported_without_payload_interpretation(
    item: H1RawInventoryItem,
) -> None:
    with pytest.raises(H1OwnerInventoryFailure, match="UNSUPPORTED") as raised:
        decode_h1_effect_delivery_scope(item)

    assert raised.value.locator == item.locator


def test_registered_malformed_or_envelope_mismatched_member_is_corrupt() -> None:
    malformed = _dispatch_item(raw=b"{}")
    with pytest.raises(H1OwnerInventoryFailure, match="CORRUPT"):
        decode_h1_effect_delivery_scope(malformed)

    with pytest.raises(H1OwnerInventoryFailure, match="CORRUPT"):
        decode_h1_effect_delivery_scope(_dispatch_item(record_kind="effects.UNRELATED"))


def test_zero_record_denial_command_retains_its_intent_before_scope_filtering() -> None:
    request = make_denial_request()
    decoded = decode_h1_effect_delivery_scope(
        H1RawInventoryItem(
            "OWNER_COMMAND",
            "owner/decision/denial",
            "tenant",
            "effects",
            request.schema_id,
            None,
            None,
            None,
            request.canonical_bytes(),
        )
    )

    assert decoded.locator == "owner/decision/denial"
    assert decoded.identities[0].namespace == "intent"
    assert decoded.identities[0].identity == "intent"
    assert decoded.families == ("INTENT",)


def test_transfer_only_consumption_retains_dispatch_and_intent() -> None:
    send = original_send()
    request = ConsumeRequest(
        schema_id="chiplog.broker-dispatch.consume-effect-send.v1",
        tenant_id="tenant",
        command_id="consume-send/1",
        selected_send=send.record,
        send_record=send.record,
        intent=reference(send.snapshot.intent.intent_id, send.snapshot.intent.canonical_bytes()),
        transmission=send.snapshot.transmissions[0].transmission,
        ticket_bytes=b"ticket",
        ticket_digest=hashlib.sha256(b"ticket").hexdigest(),
        registry=reference("broker-dispatch.registry.v1", b"registry"),
    )
    record = ConsumptionRecord(
        schema_id="chiplog.broker-dispatch.send-consumption.v1", state="CONSUMED", request=request
    )
    raw = record.canonical_bytes()
    decoded = decode_h1_effect_delivery_scope(
        H1RawInventoryItem(
            "PHYSICAL",
            "records/consumption",
            "tenant",
            "broker_dispatch",
            record.schema_id,
            "send-consumption",
            request.command_id,
            hashlib.sha256(raw).hexdigest(),
            raw,
        )
    )

    assert {(key.namespace, key.identity) for key in decoded.identities} == {
        ("dispatch", "send"),
        ("intent", "intent"),
    }


def test_registrations_are_explicitly_closed_to_actual_physical_effects_members() -> None:
    assert {
        (entry.surface, entry.owner, entry.schema, entry.record_kind) for entry in REGISTRATIONS
    } == {
        ("PHYSICAL", "effects", "chiplog.effects.record.v1", None),
        ("PHYSICAL", "effects", "chiplog.effects.dispatch-record.v2", None),
        ("PHYSICAL", "effects", "chiplog.effects.dispatch-outcome-record.v2", None),
        (
            "PHYSICAL",
            "broker_dispatch",
            "chiplog.broker-dispatch.send-consumption.v1",
            "send-consumption",
        ),
        ("OWNER_COMMAND", "effects", "chiplog.effects.dispatch-preparation.v2", None),
        ("OWNER_COMMAND", "effects", "chiplog.effects.dispatch-outcome-preparation.v2", None),
        ("OWNER_COMMAND", "effects", "chiplog.effects.before-send-preparation.v2", None),
        (
            "OWNER_COMMAND",
            "broker_dispatch",
            "chiplog.broker-dispatch.consume-effect-send.v1",
            None,
        ),
    }
