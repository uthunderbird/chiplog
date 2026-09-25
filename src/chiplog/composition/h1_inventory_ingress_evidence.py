"""Pure H1 scope extraction for retained ingress and evidence occurrences.

The orchestrator authenticates enumeration, owner membership, custody history,
and any current or historical cut.  This module only canonical-decodes one
already enumerated occurrence and projects its typed identities and edges.
"""

from __future__ import annotations

import hashlib
import json

from chiplog.adapters.driven.journal_sqlite import OWNER as EVIDENCE_JOURNAL_OWNER
from chiplog.adapters.driven.journal_sqlite import SCHEMA as EVIDENCE_JOURNAL_SCHEMA
from chiplog.capabilities.evidence_journal.commands import JournalRecord
from chiplog.platform.ingress_authenticated_contracts import (
    AuthenticatedCustodyCommand,
    AuthenticatedCustodyRecord,
)
from chiplog.platform.ingress_custody_records import (
    CustodyCommand,
    CustodyRecord,
    canonical,
    reference,
)

from .h1_inventory_leaf_contracts import (
    H1DecodedInventoryItem,
    H1LeafRegistration,
    H1OwnerInventoryFailure,
    H1RawInventoryItem,
    H1ScopeKey,
    H1ScopeRelation,
)

_INBOX_OWNER = "evidence_inbox"
_INBOX_SCHEMA = "chiplog.evidence-inbox.row.v1"
_INBOX_KIND = "evidence.inbox"
_CUSTODY_OWNER = "broker_ingress"
CUSTODY_COMMAND_SCHEMA = "chiplog.ingress.retained-command.v1"
CUSTODY_RECORD_SCHEMA = "chiplog.ingress.custody-record.v1"
AUTHENTICATED_COMMAND_SCHEMA = "chiplog.ingress.authenticated-command.v2"
AUTHENTICATED_RECORD_SCHEMA = "chiplog.ingress.authenticated-record.v2"
_INBOX_STATES: dict[str, frozenset[str]] = {
    "PUSH": frozenset(
        {
            "LOCAL_ACK_AUTHORIZED",
            "PUSH_RESPONSE_ATTEMPT_ISSUED",
            "PUSH_RESPONSE_LOCAL_COMPLETION_OBSERVED",
            "PROVIDER_RECEIPT_OBSERVED",
        }
    ),
    "POLL": frozenset(
        {
            "LOCAL_ACK_AUTHORIZED",
            "POLL_CURSOR_ADVANCE_AUTHORIZED",
            "POLL_CURSOR_APPLIED",
        }
    ),
    "RECONCILIATION": frozenset(
        {
            "LOCAL_ACK_AUTHORIZED",
            "RECONCILIATION_RELEASE_AUTHORIZED",
            "RECONCILIATION_OBLIGATION_RELEASED",
        }
    ),
}

REGISTRATIONS: tuple[H1LeafRegistration, ...] = (
    H1LeafRegistration("PHYSICAL", _CUSTODY_OWNER, CUSTODY_RECORD_SCHEMA, None, "v1"),
    H1LeafRegistration(
        "PHYSICAL",
        _CUSTODY_OWNER,
        AUTHENTICATED_RECORD_SCHEMA,
        None,
        "v2",
    ),
    H1LeafRegistration("OWNER_COMMAND", _CUSTODY_OWNER, CUSTODY_COMMAND_SCHEMA, None, "v1"),
    H1LeafRegistration("OWNER_COMMAND", _CUSTODY_OWNER, AUTHENTICATED_COMMAND_SCHEMA, None, "v2"),
    H1LeafRegistration("EVIDENCE_INBOX", _INBOX_OWNER, _INBOX_SCHEMA, _INBOX_KIND, "v1"),
    H1LeafRegistration("PHYSICAL", EVIDENCE_JOURNAL_OWNER, EVIDENCE_JOURNAL_SCHEMA, None, "v1"),
)

_REGISTRATION_KEYS = {
    (entry.surface, entry.owner, entry.schema, entry.record_kind) for entry in REGISTRATIONS
}


def _failure(item: H1RawInventoryItem, code: str) -> H1OwnerInventoryFailure:
    kwargs = {
        "family": "EVIDENCE",
        "owner": item.owner,
        "schema": item.schema,
        "locator": item.locator,
    }
    if code == "UNSUPPORTED":
        return H1OwnerInventoryFailure.unsupported(**kwargs)
    if code == "CORRUPT":
        return H1OwnerInventoryFailure.corrupt(**kwargs)
    return H1OwnerInventoryFailure.incomplete(family="EVIDENCE", locator=item.locator)


def _require_registered(item: H1RawInventoryItem) -> None:
    if (item.surface, item.owner, item.schema, item.record_kind) not in _REGISTRATION_KEYS:
        raise _failure(item, "UNSUPPORTED")


def _require_exact_bytes(item: H1RawInventoryItem, expected: bytes) -> None:
    if item.raw != expected:
        raise _failure(item, "CORRUPT")
    if item.fingerprint != hashlib.sha256(item.raw).hexdigest():
        raise _failure(item, "CORRUPT")


def _key(
    item: H1RawInventoryItem, namespace: str, identity: str, head: str | None = None
) -> H1ScopeKey:
    return H1ScopeKey(item.tenant, namespace, identity, head)  # type: ignore[arg-type]


def _custody_output(
    item: H1RawInventoryItem,
    *,
    source_id: str,
    evidence_id: str,
    record_id: str,
    record_head: str | None,
) -> H1DecodedInventoryItem:
    source = _key(item, "source", source_id)
    evidence = _key(item, "evidence", evidence_id)
    record = _key(item, "record", record_id, record_head)
    return H1DecodedInventoryItem(
        item.locator,
        (record, evidence, source),
        (
            H1ScopeRelation("RECORD_SOURCE", record, source),
            H1ScopeRelation("EVIDENCE_SOURCE", evidence, source),
        ),
        (),
        ("RECORD", "EVIDENCE", "SOURCE"),
    )


def _decode_custody_record(item: H1RawInventoryItem) -> H1DecodedInventoryItem:
    try:
        record = (
            CustodyRecord.model_validate_json(item.raw)
            if item.schema == CUSTODY_RECORD_SCHEMA
            else AuthenticatedCustodyRecord.model_validate_json(item.raw)
        )
        _require_exact_bytes(item, canonical(record))
        head = reference(record.command.command_id + "/record", canonical(record))
    except H1OwnerInventoryFailure:
        raise
    except (TypeError, ValueError) as error:
        raise _failure(item, "CORRUPT") from error
    if (
        item.tenant != record.command.profile.tenant_id
        or item.record_id != head.head
        or item.fingerprint != head.fingerprint
    ):
        raise _failure(item, "CORRUPT")
    evidence_id = (
        record.inbox.inbox_id
        if isinstance(record, AuthenticatedCustodyRecord)
        else record.command.token.token_id
    )
    return _custody_output(
        item,
        source_id=record.command.profile.source_identity,
        evidence_id=evidence_id,
        record_id=head.identity,
        record_head=head.head,
    )


def _decode_custody_command(item: H1RawInventoryItem) -> H1DecodedInventoryItem:
    try:
        command = (
            CustodyCommand.model_validate_json(item.raw)
            if item.schema == CUSTODY_COMMAND_SCHEMA
            else AuthenticatedCustodyCommand.model_validate_json(item.raw)
        )
        _require_exact_bytes(item, canonical(command))
    except H1OwnerInventoryFailure:
        raise
    except (TypeError, ValueError) as error:
        raise _failure(item, "CORRUPT") from error
    if item.tenant != command.profile.tenant_id or item.record_id != command.command_id:
        raise _failure(item, "CORRUPT")
    return _custody_output(
        item,
        source_id=command.profile.source_identity,
        evidence_id=command.token.token_id,
        record_id=command.command_id,
        record_head=None,
    )


def _decode_inbox(item: H1RawInventoryItem) -> H1DecodedInventoryItem:
    if item.metadata_bytes is None:
        raise _failure(item, "INCOMPLETE")
    try:
        metadata = json.loads(item.metadata_bytes)
        source_id = metadata["source_id"]
        evidence_id = metadata["evidence_id"]
        fingerprint = metadata["fingerprint"]
        states = _INBOX_STATES.get(metadata["followup_kind"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise _failure(item, "CORRUPT") from error
    if states is None or metadata["state"] not in states:
        raise _failure(item, "UNSUPPORTED")
    if (
        item.record_id != evidence_id
        or item.fingerprint != fingerprint
        or fingerprint != hashlib.sha256(item.raw).hexdigest()
    ):
        raise _failure(item, "CORRUPT")
    source = _key(item, "source", source_id)
    evidence = _key(item, "evidence", evidence_id)
    return H1DecodedInventoryItem(
        item.locator,
        (evidence, source),
        (H1ScopeRelation("EVIDENCE_SOURCE", evidence, source),),
        (),
        ("EVIDENCE", "SOURCE"),
    )


def _decode_evidence_journal(item: H1RawInventoryItem) -> H1DecodedInventoryItem:
    try:
        record = JournalRecord.model_validate_json(item.raw)
        _require_exact_bytes(item, record.canonical_bytes())
    except H1OwnerInventoryFailure:
        raise
    except (TypeError, ValueError) as error:
        raise _failure(item, "CORRUPT") from error
    if item.tenant != record.tenant or item.record_id != record.record_id:
        raise _failure(item, "CORRUPT")
    record_key = _key(item, "record", record.record_id, record.record_id)
    evidence = _key(item, "evidence", record.claim.subject.identity)
    sources = tuple(
        _key(item, "source", provenance.ingress_id) for provenance in record.claim.provenance
    )
    return H1DecodedInventoryItem(
        item.locator,
        (record_key, evidence, *sources),
        tuple(H1ScopeRelation("EVIDENCE_SOURCE", evidence, source) for source in sources)
        + tuple(H1ScopeRelation("RECORD_SOURCE", record_key, source) for source in sources),
        (),
        ("RECORD", "EVIDENCE", "SOURCE"),
    )


def decode_h1_ingress_evidence_scope(item: H1RawInventoryItem) -> H1DecodedInventoryItem:
    """Decode one registered ingress/evidence occurrence without filtering it."""
    _require_registered(item)
    if item.surface == "EVIDENCE_INBOX":
        return _decode_inbox(item)
    if item.owner == EVIDENCE_JOURNAL_OWNER:
        return _decode_evidence_journal(item)
    if item.schema in (CUSTODY_RECORD_SCHEMA, AUTHENTICATED_RECORD_SCHEMA):
        return _decode_custody_record(item)
    return _decode_custody_command(item)


__all__ = ["REGISTRATIONS", "decode_h1_ingress_evidence_scope"]
