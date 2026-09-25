"""Pure H1 inventory decoding for planning rows and retained workspace sources."""

from __future__ import annotations

import hashlib
import json

from chiplog.adapters.driven.calendar_reads import (
    CalendarReadFailure,
    decode_h1_original_calendar_read,
)
from chiplog.capabilities.planning._planning import _VERSIONS, PLANNING_RECORD_TYPES
from chiplog.capabilities.projections.batch_boundary import ProposalContext
from chiplog.capabilities.projections.workspace_boundary import SourceReference
from chiplog.composition.h1_inventory_leaf_contracts import (
    H1DecodedInventoryItem,
    H1InventoryFamily,
    H1LeafRegistration,
    H1OwnerInventoryFailure,
    H1RawInventoryItem,
    H1ScopeKey,
    H1ScopeRelation,
)
from chiplog.composition.r14_h1_workspace_issuance_contracts import H1OriginalWorkspaceIssuanceV1
from chiplog.domain_primitives.codec import canonical_record_bytes
from chiplog.domain_primitives.identity import RecordId, RecordTypeId, SchemaId
from chiplog.domain_primitives.tenant import TenantId
from chiplog.domain_primitives.versions import (
    CanonicalizationVersion,
    CodecVersion,
    OwnerTag,
    ProducingVersions,
)

_PLANNING_OWNER = "planning"
_PLANNING_SCHEMA = "chiplog.planning.record.v1"
_WORKSPACE_OWNER = "workspace_issuance"
_WORKSPACE_SCHEMA = "chiplog.execution.h1-original-workspace-issuance.v1"

REGISTRATIONS: tuple[H1LeafRegistration, ...] = (
    *(
        H1LeafRegistration("PHYSICAL", _PLANNING_OWNER, _PLANNING_SCHEMA, kind, "v1")
        for kind in PLANNING_RECORD_TYPES
    ),
    H1LeafRegistration("WORKSPACE_SOURCE", _WORKSPACE_OWNER, _WORKSPACE_SCHEMA, None, "v1"),
)


def _failure(item: H1RawInventoryItem, code: str) -> H1OwnerInventoryFailure:
    if code == "UNSUPPORTED":
        return H1OwnerInventoryFailure.unsupported(
            family="WORKSPACE", owner=item.owner, schema=item.schema, locator=item.locator
        )
    if code == "INCOMPLETE":
        return H1OwnerInventoryFailure.incomplete(family="WORKSPACE", locator=item.locator)
    return H1OwnerInventoryFailure.corrupt(
        family="WORKSPACE", owner=item.owner, schema=item.schema, locator=item.locator
    )


def _require_registered(item: H1RawInventoryItem) -> None:
    matches = tuple(
        registration
        for registration in REGISTRATIONS
        if (registration.surface, registration.owner, registration.schema)
        == (item.surface, item.owner, item.schema)
        and (registration.record_kind is None or registration.record_kind == item.record_kind)
    )
    if len(matches) != 1:
        raise _failure(item, "UNSUPPORTED")


def _require_raw_fingerprint(item: H1RawInventoryItem) -> None:
    if not item.raw:
        raise _failure(item, "INCOMPLETE")
    if item.fingerprint is not None and item.fingerprint != hashlib.sha256(item.raw).hexdigest():
        raise _failure(item, "CORRUPT")


def _source_key(tenant: str, source: SourceReference) -> H1ScopeKey:
    return H1ScopeKey(
        tenant,
        "source",
        source.owner + ":" + source.record_id,
        source.record_version,
    )


def _workspace_source(raw: str, item: H1RawInventoryItem) -> SourceReference:
    try:
        decoded = SourceReference.model_validate_json(raw)
    except (TypeError, ValueError) as error:
        raise _failure(item, "UNSUPPORTED") from error
    if decoded.model_dump_json() != raw:
        raise _failure(item, "CORRUPT")
    if decoded.tenant_id != item.tenant:
        raise _failure(item, "CORRUPT")
    return decoded


def _decode_planning(item: H1RawInventoryItem) -> H1DecodedInventoryItem:
    if item.record_id is None or item.record_kind is None:
        raise _failure(item, "INCOMPLETE")
    try:
        value = json.loads(item.raw)
        if not isinstance(value, dict):
            raise TypeError("planning envelope is not an object")
        fields = value["fields"]
        record = value["record_id"]
        record_type = value["record_type"]
        versions = value["versions"]
        if (
            not isinstance(fields, dict)
            or not isinstance(record, dict)
            or not isinstance(record_type, dict)
            or not isinstance(versions, dict)
            or value.get("owner") != _PLANNING_OWNER
            or record != {"tenant_id": item.tenant, "value": item.record_id}
            or record_type.get("namespace") != "chiplog.planning"
            or record_type.get("name") is None
            or item.record_kind != record_type["namespace"] + "." + record_type["name"]
        ):
            raise ValueError("planning envelope binding differs")
        expected_versions = ProducingVersions(
            SchemaId("chiplog.planning", "record", 1), CodecVersion(1), CanonicalizationVersion(1)
        )
        if versions != {
            "canonicalization": expected_versions.canonicalization.value,
            "codec": expected_versions.codec.value,
            "schema": {"namespace": "chiplog.planning", "name": "record", "version": 1},
        }:
            raise ValueError("planning versions differ")
        canonical = canonical_record_bytes(
            RecordId(TenantId(item.tenant), item.record_id),
            RecordTypeId("chiplog.planning", str(record_type["name"])),
            OwnerTag(_PLANNING_OWNER),
            _VERSIONS,
            fields,
        ).payload
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise _failure(item, "CORRUPT") from error
    if canonical != item.raw:
        raise _failure(item, "CORRUPT")
    return H1DecodedInventoryItem(
        item.locator,
        (H1ScopeKey(item.tenant, "planning", item.record_id, None),),
        (),
        (),
        (),
    )


def _sources_from_workspace(
    item: H1RawInventoryItem, issued: H1OriginalWorkspaceIssuanceV1
) -> tuple[tuple[SourceReference, ...], tuple[H1ScopeRelation, ...], tuple[H1ScopeKey, ...]]:
    try:
        context = ProposalContext.model_validate_json(issued.proposal_context_json)
        if context.model_dump_json() != issued.proposal_context_json:
            raise ValueError("workspace proposal context is noncanonical")
        decoded_calendar = decode_h1_original_calendar_read(issued.calendar)
    except (CalendarReadFailure, TypeError, ValueError) as error:
        raise _failure(item, "CORRUPT") from error
    workspace = H1ScopeKey(item.tenant, "workspace", context.batch.batch_id, None)
    references: list[SourceReference] = []
    relations: list[H1ScopeRelation] = []
    planning_keys: list[H1ScopeKey] = []
    for planning in issued.sources.planning_sources:
        planning_key = H1ScopeKey(item.tenant, "planning", planning.intention_line_id, None)
        planning_keys.append(planning_key)
        for raw in planning.source_reference_json:
            source = _workspace_source(raw, item)
            references.append(source)
            relations.append(
                H1ScopeRelation("PLANNING_SOURCE", planning_key, _source_key(item.tenant, source))
            )
    for row in decoded_calendar.result.rows:
        for calendar_source in row.envelope.sources:
            canonical = _workspace_source(calendar_source.model_dump_json(), item)
            references.append(canonical)
            relations.append(
                H1ScopeRelation("WORKSPACE_SOURCE", workspace, _source_key(item.tenant, canonical))
            )
    return tuple(references), tuple(relations), (workspace, *planning_keys)


def _decode_workspace_issuance(item: H1RawInventoryItem) -> H1DecodedInventoryItem:
    try:
        issued = H1OriginalWorkspaceIssuanceV1.model_validate_json(item.raw)
    except (TypeError, ValueError) as error:
        raise _failure(item, "CORRUPT") from error
    if issued.canonical_bytes() != item.raw or issued.tenant != item.tenant:
        raise _failure(item, "CORRUPT")
    references, relations, identities = _sources_from_workspace(item, issued)
    families: tuple[H1InventoryFamily, ...] = (
        ("WORKSPACE", "PLANNING", "SOURCE") if references else ("WORKSPACE", "PLANNING")
    )
    return H1DecodedInventoryItem(item.locator, identities, relations, references, families)


def decode_h1_workspace_planning_scope(item: H1RawInventoryItem) -> H1DecodedInventoryItem:
    """Decode one enumerated planning/workspace occurrence without global policy."""
    _require_registered(item)
    _require_raw_fingerprint(item)
    if (item.surface, item.owner, item.schema) == ("PHYSICAL", _PLANNING_OWNER, _PLANNING_SCHEMA):
        return _decode_planning(item)
    return _decode_workspace_issuance(item)


__all__ = ["REGISTRATIONS", "decode_h1_workspace_planning_scope"]
