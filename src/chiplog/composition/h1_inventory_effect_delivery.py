"""Pure H1 scope extraction for durable effects and dispatch members.

The caller supplies one already-enumerated occurrence.  This module validates
only the member's closed wire shape and reports its typed frontier; it neither
authenticates the occurrence nor decides whether that frontier is relevant.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable

from chiplog.capabilities.effects.contracts import EffectRecord
from chiplog.capabilities.effects.denial_contracts import DenialPreparationRequest
from chiplog.capabilities.effects.dispatch_outcome_contracts import DispatchOutcomeRecordV2
from chiplog.capabilities.effects.dispatch_v2 import DispatchPreparationV2, DispatchRecordV2
from chiplog.capabilities.effects.dispatch_v2_contracts import PublishDispatchIntentV2
from chiplog.composition.h1_inventory_leaf_contracts import (
    H1DecodedInventoryItem,
    H1InventoryFamily,
    H1LeafRegistration,
    H1OwnerInventoryFailure,
    H1RawInventoryItem,
    H1ScopeKey,
    H1ScopeRelation,
    ScopeNamespace,
)
from chiplog.composition.r16_dispatch_outbox import (
    CONSUME_SCHEMA,
    CONSUMPTION_SCHEMA,
    ConsumeRequest,
    ConsumptionRecord,
)

_EFFECT_SCHEMA = "chiplog.effects.record.v1"
_DISPATCH_SCHEMA = "chiplog.effects.dispatch-record.v2"
_OUTCOME_SCHEMA = "chiplog.effects.dispatch-outcome-record.v2"

REGISTRATIONS: tuple[H1LeafRegistration, ...] = (
    H1LeafRegistration("PHYSICAL", "effects", _EFFECT_SCHEMA, None, "effects-v1"),
    H1LeafRegistration("PHYSICAL", "effects", _DISPATCH_SCHEMA, None, "dispatch-v2"),
    H1LeafRegistration("PHYSICAL", "effects", _OUTCOME_SCHEMA, None, "outcome-v2"),
    H1LeafRegistration(
        "PHYSICAL", "broker_dispatch", CONSUMPTION_SCHEMA, "send-consumption", "consumption-v1"
    ),
    H1LeafRegistration(
        "OWNER_COMMAND", "effects", "chiplog.effects.dispatch-preparation.v2", None, "dispatch-v2"
    ),
    H1LeafRegistration(
        "OWNER_COMMAND",
        "effects",
        "chiplog.effects.dispatch-outcome-preparation.v2",
        None,
        "outcome-v2",
    ),
    H1LeafRegistration(
        "OWNER_COMMAND", "effects", "chiplog.effects.before-send-preparation.v2", None, "denial-v2"
    ),
    H1LeafRegistration("OWNER_COMMAND", "broker_dispatch", CONSUME_SCHEMA, None, "consumption-v1"),
)


def _failure(item: H1RawInventoryItem, code: str) -> H1OwnerInventoryFailure:
    factory = (
        H1OwnerInventoryFailure.unsupported
        if code == "UNSUPPORTED"
        else H1OwnerInventoryFailure.corrupt
    )
    return factory(
        family="EFFECT_DELIVERY", owner=item.owner, schema=item.schema, locator=item.locator
    )


def _registration(item: H1RawInventoryItem) -> H1LeafRegistration:
    matches = tuple(
        registration
        for registration in REGISTRATIONS
        if (registration.surface, registration.owner, registration.schema)
        == (item.surface, item.owner, item.schema)
        and (registration.record_kind is None or registration.record_kind == item.record_kind)
    )
    if len(matches) != 1:
        raise _failure(item, "UNSUPPORTED")
    return matches[0]


def _physical_envelope(item: H1RawInventoryItem, *, record_id: str, raw: bytes, kind: str) -> None:
    if (
        item.record_id is None
        or item.fingerprint is None
        or item.record_kind is None
        or item.record_id != record_id
        or item.fingerprint != hashlib.sha256(raw).hexdigest()
        or item.record_kind != kind
    ):
        raise _failure(item, "CORRUPT")


def _command_envelope(item: H1RawInventoryItem) -> None:
    if (
        item.record_id is not None
        or item.record_kind is not None
        or (
            item.fingerprint is not None
            and item.fingerprint != hashlib.sha256(item.raw).hexdigest()
        )
    ):
        raise _failure(item, "CORRUPT")


def _key(tenant: str, namespace: ScopeNamespace, identity: str, head: str | None) -> H1ScopeKey:
    return H1ScopeKey(tenant, namespace, identity, head)


def _legacy(item: H1RawInventoryItem) -> H1DecodedInventoryItem:
    record = EffectRecord.model_validate_json(item.raw)
    if record.canonical_bytes() != item.raw:
        raise ValueError("noncanonical effects bytes")
    _physical_envelope(
        item, record_id=record.record.head, raw=item.raw, kind="effects." + record.kind
    )
    effect = _key(item.tenant, "effect", record.record.subject_id, record.record.head)
    intent = _key(
        item.tenant,
        "intent",
        record.snapshot.intent.intent_id,
        record.snapshot.intent.fingerprint,
    )
    return H1DecodedInventoryItem(
        item.locator,
        (effect, intent),
        (H1ScopeRelation("EFFECT_INTENT", effect, intent),),
        (),
        ("EFFECT", "INTENT"),
    )


def _dispatch(item: H1RawInventoryItem) -> H1DecodedInventoryItem:
    record = DispatchRecordV2.model_validate_json(item.raw)
    if record.canonical_bytes() != item.raw:
        raise ValueError("noncanonical dispatch bytes")
    _physical_envelope(
        item, record_id=record.record.head, raw=item.raw, kind="effects." + record.kind
    )
    dispatch = _key(item.tenant, "dispatch", record.record.subject_id, record.record.head)
    effect = _key(item.tenant, "effect", record.record.subject_id, record.record.head)
    intent_body = record.snapshot.intent
    intent = _key(item.tenant, "intent", intent_body.intent_id, intent_body.fingerprint)
    mandate = _key(item.tenant, "mandate", intent_body.mandate.mandate_id, None)
    return H1DecodedInventoryItem(
        item.locator,
        (dispatch, effect, intent, mandate),
        (
            H1ScopeRelation("DISPATCH_EFFECT", dispatch, effect),
            H1ScopeRelation("EFFECT_INTENT", effect, intent),
            H1ScopeRelation("INTENT_MANDATE", intent, mandate),
        ),
        (),
        ("DISPATCH", "EFFECT", "INTENT", "MANDATE"),
    )


def _outcome(item: H1RawInventoryItem) -> H1DecodedInventoryItem:
    record = DispatchOutcomeRecordV2.model_validate_json(item.raw)
    if record.canonical_bytes() != item.raw:
        raise ValueError("noncanonical outcome bytes")
    _physical_envelope(
        item, record_id=record.record.head, raw=item.raw, kind="effects." + record.kind
    )
    outcome = _key(item.tenant, "outcome", record.record.subject_id, record.record.head)
    intent_body = record.snapshot.intent
    intent = _key(item.tenant, "intent", intent_body.intent_id, intent_body.fingerprint)
    mandate = _key(item.tenant, "mandate", intent_body.mandate.mandate_id, None)
    obligation = _key(
        item.tenant,
        "obligation",
        record.snapshot.obligation.obligation.subject_id,
        record.snapshot.obligation.obligation.head,
    )
    return H1DecodedInventoryItem(
        item.locator,
        (outcome, intent, mandate, obligation),
        (
            H1ScopeRelation("INTENT_MANDATE", intent, mandate),
            H1ScopeRelation("MANDATE_OBLIGATION", mandate, obligation),
        ),
        (),
        ("OUTCOME", "INTENT", "MANDATE", "OBLIGATION"),
    )


def _consumption(item: H1RawInventoryItem) -> H1DecodedInventoryItem:
    record = ConsumptionRecord.model_validate_json(item.raw)
    if record.canonical_bytes() != item.raw:
        raise ValueError("noncanonical consumption bytes")
    _physical_envelope(
        item, record_id=record.request.command_id, raw=item.raw, kind="send-consumption"
    )
    dispatch = _key(
        item.tenant,
        "dispatch",
        record.request.send_record.subject_id,
        record.request.send_record.head,
    )
    intent = _key(
        item.tenant, "intent", record.request.intent.subject_id, record.request.intent.head
    )
    return H1DecodedInventoryItem(
        item.locator,
        (dispatch, intent),
        (),
        (),
        ("DISPATCH", "INTENT"),
    )


def _command_scope(
    item: H1RawInventoryItem, *, intent_id: str, intent_head: str, mandate_id: str | None = None
) -> H1DecodedInventoryItem:
    _command_envelope(item)
    intent = _key(item.tenant, "intent", intent_id, intent_head)
    identities: tuple[H1ScopeKey, ...] = (intent,)
    relations: tuple[H1ScopeRelation, ...] = ()
    families: tuple[H1InventoryFamily, ...] = ("INTENT",)
    if mandate_id is not None:
        mandate = _key(item.tenant, "mandate", mandate_id, None)
        identities = (intent, mandate)
        relations = (H1ScopeRelation("INTENT_MANDATE", intent, mandate),)
        families = ("INTENT", "MANDATE")
    return H1DecodedInventoryItem(item.locator, identities, relations, (), families)


def _dispatch_command(item: H1RawInventoryItem) -> H1DecodedInventoryItem:
    request = DispatchPreparationV2.model_validate_json(item.raw)
    if request.canonical_bytes() != item.raw:
        raise ValueError("noncanonical dispatch preparation bytes")
    command = request.command
    if isinstance(command, PublishDispatchIntentV2):
        return _command_scope(
            item,
            intent_id=command.intent.intent_id,
            intent_head=command.intent.fingerprint,
            mandate_id=command.intent.mandate.mandate_id,
        )
    return _command_scope(
        item,
        intent_id=command.intent.subject_id,
        intent_head=command.intent.head,
        mandate_id=command.immutable_mandate.subject_id,
    )


def _outcome_command(item: H1RawInventoryItem) -> H1DecodedInventoryItem:
    from chiplog.capabilities.effects.dispatch_outcome_contracts import DispatchOutcomePreparationV2

    request = DispatchOutcomePreparationV2.model_validate_json(item.raw)
    if request.canonical_bytes() != item.raw:
        raise ValueError("noncanonical outcome preparation bytes")
    return _command_scope(
        item,
        intent_id=request.command.intent.subject_id,
        intent_head=request.command.intent.head,
    )


def _denial_command(item: H1RawInventoryItem) -> H1DecodedInventoryItem:
    request = DenialPreparationRequest.model_validate_json(item.raw)
    if request.canonical_bytes() != item.raw:
        raise ValueError("noncanonical denial preparation bytes")
    return _command_scope(
        item,
        intent_id=request.command.intent.subject_id,
        intent_head=request.command.intent.head,
    )


def _consumption_command(item: H1RawInventoryItem) -> H1DecodedInventoryItem:
    request = ConsumeRequest.model_validate_json(item.raw)
    if request.canonical_bytes() != item.raw:
        raise ValueError("noncanonical consumption request bytes")
    return _command_scope(
        item,
        intent_id=request.intent.subject_id,
        intent_head=request.intent.head,
    )


def decode_h1_effect_delivery_scope(item: H1RawInventoryItem) -> H1DecodedInventoryItem:
    """Decode one explicitly registered raw member, before any scope filtering."""
    _registration(item)
    decoder: Callable[[H1RawInventoryItem], H1DecodedInventoryItem]
    decoder = {
        ("PHYSICAL", _EFFECT_SCHEMA): _legacy,
        ("PHYSICAL", _DISPATCH_SCHEMA): _dispatch,
        ("PHYSICAL", _OUTCOME_SCHEMA): _outcome,
        ("PHYSICAL", CONSUMPTION_SCHEMA): _consumption,
        ("OWNER_COMMAND", "chiplog.effects.dispatch-preparation.v2"): _dispatch_command,
        ("OWNER_COMMAND", "chiplog.effects.dispatch-outcome-preparation.v2"): _outcome_command,
        ("OWNER_COMMAND", "chiplog.effects.before-send-preparation.v2"): _denial_command,
        ("OWNER_COMMAND", CONSUME_SCHEMA): _consumption_command,
    }[(item.surface, item.schema)]
    try:
        return decoder(item)
    except H1OwnerInventoryFailure:
        raise
    except (TypeError, ValueError) as error:
        raise _failure(item, "CORRUPT") from error


__all__ = ["REGISTRATIONS", "decode_h1_effect_delivery_scope"]
