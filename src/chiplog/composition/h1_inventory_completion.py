"""Closed H1 completion-member inventory leaf.

Rows in this family carry opaque post-terminal heads.  The inventory owner
therefore supplies the already reconciled V2 cohort that authenticates their
membership and gives every member its original Run scope.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from chiplog.capabilities.agent_loop.completion_owner_record_contracts import (
    DELIVERY_ACCEPTANCE_SCHEMA,
    CompletionCanonicalMember,
    CompletionRecordIntegrityError,
    decode_completion_canonical_member,
)
from chiplog.capabilities.agent_loop.delivery_preparation import DeliveryAcceptanceProposal
from chiplog.capabilities.agent_loop.execution_history_contracts import ExecutionRunRecordV3
from chiplog.capabilities.agent_loop.execution_recovery_observations import (
    ExecutionTerminalManifest,
)
from chiplog.capabilities.agent_loop.post_terminal_contracts import WorkCanonicalMember
from chiplog.capabilities.agent_loop.post_terminal_record_contracts import (
    EDGE_SCHEMA,
    EPOCH_SCHEMA,
    LEASE_SCHEMA,
    ROLLOVER_SCHEMA,
    SELECTOR_SCHEMA,
    SUBJECT_SCHEMA,
    PostTerminalRecordIntegrityError,
    decode_work_canonical_member,
)
from chiplog.capabilities.effects.h1_local_preparation_record_contracts import (
    SCHEMA_ID as H1_LOCAL_INTENT_SCHEMA,
)
from chiplog.capabilities.effects.h1_local_preparation_record_contracts import (
    H1LocalPreparationRecordIntegrityError,
    H1LocalPreparedCommentaryCanonicalMemberV1,
    decode_h1_local_prepared_commentary_member,
)
from chiplog.capabilities.projections.conversation_preparation_contracts import (
    ACCEPTED_ENTRY_SCHEMA,
    ConversationCanonicalMemberV2,
    ConversationPreparationIntegrityError,
    decode_conversation_canonical_member,
)
from chiplog.capabilities.projections.conversation_preparation_contracts import (
    OWNER as CONVERSATION_OWNER,
)
from chiplog.composition.h1_inventory_leaf_contracts import (
    H1DecodedInventoryItem,
    H1LeafRegistration,
    H1OwnerInventoryFailure,
    H1RawInventoryItem,
    H1ScopeKey,
    H1ScopeRelation,
)

_AGENT_LOOP = "agent_loop"
_TERMINAL_MANIFEST_SCHEMA = "chiplog.execution.terminal-manifest.v1"
_TERMINAL_RUN_V3_SCHEMA = "chiplog.agent-loop.execution-record.v3"
_WORK_SCHEMAS = (
    SUBJECT_SCHEMA,
    EPOCH_SCHEMA,
    SELECTOR_SCHEMA,
    LEASE_SCHEMA,
    ROLLOVER_SCHEMA,
    EDGE_SCHEMA,
)

REGISTRATIONS: tuple[H1LeafRegistration, ...] = (
    H1LeafRegistration("PHYSICAL", _AGENT_LOOP, DELIVERY_ACCEPTANCE_SCHEMA, None, "completion-v2"),
    H1LeafRegistration("PHYSICAL", _AGENT_LOOP, _TERMINAL_MANIFEST_SCHEMA, None, "completion-v2"),
    H1LeafRegistration("PHYSICAL", _AGENT_LOOP, _TERMINAL_RUN_V3_SCHEMA, None, "completion-v3"),
    *(
        H1LeafRegistration("PHYSICAL", _AGENT_LOOP, schema, None, "post-terminal-v1")
        for schema in _WORK_SCHEMAS
    ),
    H1LeafRegistration(
        "PHYSICAL", CONVERSATION_OWNER, ACCEPTED_ENTRY_SCHEMA, None, "conversation-v2"
    ),
    H1LeafRegistration("PHYSICAL", "effects", H1_LOCAL_INTENT_SCHEMA, None, "h1-local-effects-v1"),
)


@dataclass(frozen=True, slots=True)
class H1CompletionInventoryCohort:
    """One exact materialized V2 batch already reconciled with SQL."""

    run_id: str
    members: dict[str, tuple[str, str, bytes, str]]


def _failure(item: H1RawInventoryItem, code: str) -> H1OwnerInventoryFailure:
    factory = (
        H1OwnerInventoryFailure.unsupported
        if code == "UNSUPPORTED"
        else H1OwnerInventoryFailure.corrupt
    )
    return factory(family="COMPLETION", owner=item.owner, schema=item.schema, locator=item.locator)


def _cohort_member(item: H1RawInventoryItem, cohort: H1CompletionInventoryCohort) -> None:
    if item.surface != "PHYSICAL" or item.record_id is None or item.fingerprint is None:
        raise _failure(item, "CORRUPT")
    expected = cohort.members.get(item.record_id)
    if expected != (item.owner, item.schema, item.raw, item.fingerprint):
        raise _failure(item, "CORRUPT")
    if hashlib.sha256(item.raw).hexdigest() != item.fingerprint:
        raise _failure(item, "CORRUPT")


def _completion(item: H1RawInventoryItem, cohort: H1CompletionInventoryCohort) -> None:
    kind = (
        "DELIVERY_ACCEPTANCE"
        if item.schema == DELIVERY_ACCEPTANCE_SCHEMA
        else "TERMINAL_MANIFEST"
        if item.schema == _TERMINAL_MANIFEST_SCHEMA
        else "Run"
    )
    try:
        decoded = decode_completion_canonical_member(
            CompletionCanonicalMember(
                record_kind=kind,
                schema_id=item.schema,
                record_id=item.record_id or "",
                canonical_record_bytes=item.raw,
                fingerprint=item.fingerprint or "",
            )
        )
    except (CompletionRecordIntegrityError, ValueError) as error:
        raise _failure(item, "CORRUPT") from error
    expected = (
        DeliveryAcceptanceProposal
        if kind == "DELIVERY_ACCEPTANCE"
        else ExecutionTerminalManifest
        if kind == "TERMINAL_MANIFEST"
        else ExecutionRunRecordV3
    )
    if not isinstance(decoded.record, expected):
        raise _failure(item, "CORRUPT")
    if item.schema == _TERMINAL_RUN_V3_SCHEMA and (
        not isinstance(decoded.record, ExecutionRunRecordV3)
        or decoded.record.run_id != cohort.run_id
    ):
        raise _failure(item, "CORRUPT")


def _work(item: H1RawInventoryItem) -> None:
    try:
        decoded = decode_work_canonical_member(
            WorkCanonicalMember(
                record_kind=item.record_kind,  # type: ignore[arg-type]
                record_id=item.record_id or "",
                schema_id=item.schema,
                canonical_record_bytes=item.raw,
                fingerprint=item.fingerprint or "",
            )
        )
    except (PostTerminalRecordIntegrityError, ValueError, TypeError) as error:
        raise _failure(item, "CORRUPT") from error
    if decoded.record.schema_id != item.schema:
        raise _failure(item, "CORRUPT")


def _conversation(item: H1RawInventoryItem) -> None:
    try:
        decode_conversation_canonical_member(
            ConversationCanonicalMemberV2(
                record_id=item.record_id or "",
                canonical_bytes=item.raw,
                fingerprint=item.fingerprint or "",
            )
        )
    except (ConversationPreparationIntegrityError, ValueError) as error:
        raise _failure(item, "CORRUPT") from error


def _effects(item: H1RawInventoryItem) -> None:
    try:
        decode_h1_local_prepared_commentary_member(
            H1LocalPreparedCommentaryCanonicalMemberV1(
                record_id=item.record_id or "",
                canonical_record_bytes=item.raw,
                fingerprint=item.fingerprint or "",
            )
        )
    except (H1LocalPreparationRecordIntegrityError, ValueError) as error:
        raise _failure(item, "CORRUPT") from error


def decode_h1_completion_scope(
    item: H1RawInventoryItem, cohort: H1CompletionInventoryCohort | None
) -> H1DecodedInventoryItem:
    """Decode one cohort-authenticated completion member into the original Run scope."""
    if cohort is None or (item.owner, item.schema) not in {
        (entry.owner, entry.schema) for entry in REGISTRATIONS
    }:
        raise _failure(item, "UNSUPPORTED")
    _cohort_member(item, cohort)
    if item.schema in (
        DELIVERY_ACCEPTANCE_SCHEMA,
        _TERMINAL_MANIFEST_SCHEMA,
        _TERMINAL_RUN_V3_SCHEMA,
    ):
        _completion(item, cohort)
    elif item.schema in _WORK_SCHEMAS:
        _work(item)
    elif item.schema == ACCEPTED_ENTRY_SCHEMA:
        _conversation(item)
    elif item.schema == H1_LOCAL_INTENT_SCHEMA:
        _effects(item)
    else:  # pragma: no cover - closed registrations above
        raise _failure(item, "UNSUPPORTED")
    record = H1ScopeKey(item.tenant, "record", item.record_id or "", item.fingerprint)
    run = H1ScopeKey(item.tenant, "run", cohort.run_id, None)
    return H1DecodedInventoryItem(
        item.locator,
        (record, run),
        (H1ScopeRelation("RECORD_RUN", record, run),),
        (),
        ("RECORD", "RUN"),
    )
