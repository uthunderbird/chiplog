"""Pure decoding for legacy local baseline physical inventory rows.

This leaf only proves that a single already-enumerated physical row has the
writer's exact shape.  Selection, authority, ancestry replay, and relevance
belong to the owner inventory reader.
"""

from __future__ import annotations

import hashlib
import json

from pydantic import ValidationError

from chiplog.capabilities.agent_loop.contracts import LoopRejected, RunRecord
from chiplog.capabilities.agent_loop.domain import validate_record
from chiplog.capabilities.projections.r9_boundary import ConversationEntry
from chiplog.capabilities.projections.workspace_boundary import SourceReference
from chiplog.composition.r13_workspace_provenance import accepted_sources

from .h1_inventory_leaf_contracts import (
    H1DecodedInventoryItem,
    H1InventoryFamily,
    H1LeafRegistration,
    H1OwnerInventoryFailure,
    H1RawInventoryItem,
    H1ScopeKey,
    H1ScopeRelation,
)

_LOOP_OWNER = "agent_loop"
_LOOP_SCHEMA = "chiplog.agent-loop.record.v1"
_CONVERSATION_OWNER = "conversation"
_CONVERSATION_SCHEMA = "chiplog.conversation.v1"
_POLICY_OWNER = "workspace_policy"
_POLICY_SCHEMA = "chiplog.workspace.policy.v1"

REGISTRATIONS: tuple[H1LeafRegistration, ...] = (
    H1LeafRegistration("PHYSICAL", _LOOP_OWNER, _LOOP_SCHEMA, None, "baseline-v1"),
    H1LeafRegistration("PHYSICAL", _CONVERSATION_OWNER, _CONVERSATION_SCHEMA, None, "baseline-v1"),
    H1LeafRegistration("PHYSICAL", _POLICY_OWNER, _POLICY_SCHEMA, None, "baseline-v1"),
)
_REGISTRATION_KEYS = {
    (row.surface, row.owner, row.schema, row.record_kind) for row in REGISTRATIONS
}


def _failure(item: H1RawInventoryItem, code: str) -> H1OwnerInventoryFailure:
    factory = (
        H1OwnerInventoryFailure.unsupported
        if code == "UNSUPPORTED"
        else H1OwnerInventoryFailure.corrupt
    )
    return factory(family="RECORD", owner=item.owner, schema=item.schema, locator=item.locator)


def _registered(item: H1RawInventoryItem) -> None:
    if (item.surface, item.owner, item.schema, item.record_kind) not in _REGISTRATION_KEYS:
        raise _failure(item, "UNSUPPORTED")


def _physical(item: H1RawInventoryItem, expected_id: str) -> H1ScopeKey:
    if item.record_id != expected_id or item.fingerprint != hashlib.sha256(item.raw).hexdigest():
        raise _failure(item, "CORRUPT")
    return H1ScopeKey(item.tenant, "record", expected_id, item.fingerprint)


def _source_key(item: H1RawInventoryItem, source: SourceReference) -> H1ScopeKey:
    if source.tenant_id != item.tenant:
        raise _failure(item, "CORRUPT")
    return H1ScopeKey(
        item.tenant, "source", source.owner + ":" + source.record_id, source.record_version
    )


def _with_sources(
    item: H1RawInventoryItem,
    record: H1ScopeKey,
    sources: tuple[SourceReference, ...],
    identities: tuple[H1ScopeKey, ...],
    relations: tuple[H1ScopeRelation, ...],
    families: tuple[H1InventoryFamily, ...],
) -> H1DecodedInventoryItem:
    source_keys = tuple(_source_key(item, source) for source in sources)
    return H1DecodedInventoryItem(
        item.locator,
        (*identities, *source_keys),
        (*relations, *(H1ScopeRelation("RECORD_SOURCE", record, source) for source in source_keys)),
        sources,
        families + (("SOURCE",) if sources else ()),
    )


def _run(item: H1RawInventoryItem) -> H1DecodedInventoryItem:
    try:
        run = RunRecord.model_validate_json(item.raw)
    except ValidationError as error:
        raise _failure(item, "CORRUPT") from error
    if run.canonical_bytes() != item.raw or run.tenant != item.tenant:
        raise _failure(item, "CORRUPT")
    expected_head = "loop:" + run.model_copy(update={"head": "pending"}).digest()
    if run.head != expected_head:
        raise _failure(item, "CORRUPT")
    if run.predecessor is None:
        try:
            validate_record(None, run)
        except LoopRejected as error:
            raise _failure(item, "CORRUPT") from error
    record = _physical(item, run.head)
    run_key = H1ScopeKey(item.tenant, "run", run.run_id, run.head)
    identities: list[H1ScopeKey] = [record, run_key]
    relations: list[H1ScopeRelation] = [H1ScopeRelation("RECORD_RUN", record, run_key)]
    families: list[H1InventoryFamily] = ["RECORD", "RUN"]
    for turn in run.turns:
        turn_key = H1ScopeKey(item.tenant, "turn", turn.turn_id, turn.head)
        identities.append(turn_key)
        relations.extend(
            (
                H1ScopeRelation("RECORD_TURN", record, turn_key),
                H1ScopeRelation("RUN_TURN", run_key, turn_key),
            )
        )
        families.append("TURN")
        for outcome in turn.sealed_calls or ():
            call_key = H1ScopeKey(item.tenant, "call", outcome.call.call_id, None)
            identities.append(call_key)
            relations.append(H1ScopeRelation("TURN_CALL", turn_key, call_key))
            families.append("CALL")
    for delivery in run.deliveries:
        identities.append(H1ScopeKey(item.tenant, "delivery", delivery.delivery_id, None))
        families.append("DELIVERY")
    return _with_sources(
        item,
        record,
        accepted_sources(run),
        tuple(identities),
        tuple(relations),
        tuple(dict.fromkeys(families)),
    )


def _conversation(item: H1RawInventoryItem) -> H1DecodedInventoryItem:
    try:
        wrapper = json.loads(item.raw)
        if type(wrapper) is not dict or frozenset(wrapper) != {"entry_json", "fingerprint"}:
            raise ValueError("unexpected conversation wrapper")
        entry_json = wrapper["entry_json"]
        fingerprint = wrapper["fingerprint"]
        if type(entry_json) is not str or type(fingerprint) is not str:
            raise ValueError("invalid conversation wrapper fields")
        canonical_wrapper = json.dumps(wrapper, sort_keys=True, separators=(",", ":")).encode()
        entry = ConversationEntry.model_validate_json(entry_json)
    except (TypeError, ValueError, json.JSONDecodeError, ValidationError) as error:
        raise _failure(item, "CORRUPT") from error
    if (
        canonical_wrapper != item.raw
        or entry.model_dump_json() != entry_json
        or hashlib.sha256(entry_json.encode()).hexdigest() != fingerprint
        or entry.tenant_id != item.tenant
    ):
        raise _failure(item, "CORRUPT")
    record = _physical(item, entry.entry_id)
    return _with_sources(item, record, entry.envelope.sources, (record,), (), ("RECORD",))


def _policy(item: H1RawInventoryItem) -> H1DecodedInventoryItem:
    try:
        payload = json.loads(item.raw)
        expected_fields = {
            "tenant",
            "principal",
            "channel",
            "database",
            "endpoint",
            "heads",
            "sources",
        }
        if type(payload) is not dict or frozenset(payload) != expected_fields:
            raise ValueError("unexpected policy fields")
        tenant, principal, channel = payload["tenant"], payload["principal"], payload["channel"]
        if any(
            type(value) is not str or not value
            for value in (tenant, principal, channel, payload["database"], payload["endpoint"])
        ):
            raise ValueError("invalid policy scalar")
        heads = payload["heads"]
        if type(heads) is not dict or frozenset(heads) != {
            "policy",
            "credential",
            "session",
            "contour",
            "deletion",
        }:
            raise ValueError("invalid policy heads")
        if any(
            type(heads[name]) is not str or not heads[name]
            for name in ("policy", "credential", "session", "contour", "deletion")
        ):
            raise ValueError("invalid policy head value")
        if type(payload["sources"]) is not list:
            raise ValueError("invalid policy sources")
        sources = tuple(
            SourceReference.model_validate_json(
                json.dumps(source, sort_keys=True, separators=(",", ":"))
            )
            for source in payload["sources"]
        )
        if any(
            source.model_dump(mode="json") != raw
            for source, raw in zip(sources, payload["sources"], strict=True)
        ):
            raise ValueError("noncanonical policy source")
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    except (TypeError, ValueError, json.JSONDecodeError, ValidationError) as error:
        raise _failure(item, "CORRUPT") from error
    if canonical != item.raw or tenant != item.tenant:
        raise _failure(item, "CORRUPT")
    policy_identity = hashlib.sha256(json.dumps([tenant, principal, channel]).encode()).hexdigest()
    expected_id = "workspace-policy:" + policy_identity + ":" + hashlib.sha256(item.raw).hexdigest()
    record = _physical(item, expected_id)
    return _with_sources(item, record, sources, (record,), (), ("RECORD",))


def decode_h1_baseline_scope(item: H1RawInventoryItem) -> H1DecodedInventoryItem:
    """Decode one explicit native baseline row before global scope classification."""
    _registered(item)
    if item.owner == _LOOP_OWNER:
        return _run(item)
    if item.owner == _CONVERSATION_OWNER:
        return _conversation(item)
    return _policy(item)


__all__ = ["REGISTRATIONS", "decode_h1_baseline_scope"]
