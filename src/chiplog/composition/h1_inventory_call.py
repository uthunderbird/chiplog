"""Pure raw H1 inventory decoding for call lifecycle and recovery records."""

from __future__ import annotations

import hashlib

from pydantic import ValidationError

from chiplog.capabilities.agent_loop.call_acceptance_contracts import (
    CancelledBeforeAcceptRecord,
    InitializedCallRecord,
    NotExecutedCallResultRecord,
    ToolCallAcceptedRecord,
    ToolExecutionIntentRecord,
)
from chiplog.capabilities.agent_loop.original_recovery_contracts import (
    LoopSemanticReductionRecord,
    OriginalObligationClosureRecord,
    OriginalResolutionBasis,
    OriginalResolverBatchRecord,
    RecoveredCallOutcomeRecord,
)
from chiplog.capabilities.agent_loop.recovery_contracts import RecoveryDTO
from chiplog.capabilities.agent_loop.recovery_record_contracts import (
    AGENT_LOOP_OWNER,
    RECOVERY_RECORD_ROWS,
    RecoveryRecordIntegrityError,
    RecoveryRecordMember,
    decode_recovery_record_member,
)
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
from chiplog.composition.r14_acceptance_contracts import ACCEPTED_SCHEMA, EXECUTION_SCHEMA
from chiplog.composition.r14_cancellation_contracts import CANCELLATION_SCHEMA, NOT_EXECUTED_SCHEMA
from chiplog.composition.r14_fanout_contracts import INITIALIZED_SCHEMA

_LIFECYCLE: dict[str, type[RecoveryDTO]] = {
    INITIALIZED_SCHEMA: InitializedCallRecord,
    ACCEPTED_SCHEMA: ToolCallAcceptedRecord,
    EXECUTION_SCHEMA: ToolExecutionIntentRecord,
    CANCELLATION_SCHEMA: CancelledBeforeAcceptRecord,
    NOT_EXECUTED_SCHEMA: NotExecutedCallResultRecord,
}
_CALL_RECOVERY_KINDS = frozenset(
    {
        "ORIGINAL_RESOLUTION_BASIS",
        "ORIGINAL_OBLIGATION_CLOSURE",
        "RECOVERED_CALL_OUTCOME",
        "ORIGINAL_RESOLVER_BATCH",
        "LOOP_SEMANTIC_REDUCTION",
    }
)
_RECOVERY = {
    (row.record_kind, row.schema_id)
    for row in RECOVERY_RECORD_ROWS
    if row.record_kind in _CALL_RECOVERY_KINDS
}

REGISTRATIONS: tuple[H1LeafRegistration, ...] = (
    *(
        H1LeafRegistration("PHYSICAL", AGENT_LOOP_OWNER, schema, None, "call-v1")
        for schema in _LIFECYCLE
    ),
    *(
        H1LeafRegistration("PHYSICAL", row.owner, row.schema_id, row.record_kind, "call-v1")
        for row in RECOVERY_RECORD_ROWS
        if row.record_kind in _CALL_RECOVERY_KINDS
    ),
)


def _failure(item: H1RawInventoryItem, code: str) -> H1OwnerInventoryFailure:
    factory = (
        H1OwnerInventoryFailure.unsupported
        if code == "UNSUPPORTED"
        else H1OwnerInventoryFailure.corrupt
    )
    return factory(family="CALL", owner=item.owner, schema=item.schema, locator=item.locator)


def _key(
    item: H1RawInventoryItem,
    namespace: ScopeNamespace,
    identity: str,
    head: str | None = None,
) -> H1ScopeKey:
    return H1ScopeKey(item.tenant, namespace, identity, head)


def _physical(item: H1RawInventoryItem) -> H1ScopeKey:
    if item.record_id is None:
        raise _failure(item, "CORRUPT")
    return _key(item, "record", item.record_id)


def _canonical_lifecycle(item: H1RawInventoryItem) -> RecoveryDTO:
    record_type = _LIFECYCLE.get(item.schema)
    if record_type is None or item.surface != "PHYSICAL" or item.owner != AGENT_LOOP_OWNER:
        raise _failure(item, "UNSUPPORTED")
    if item.record_kind is not None:
        raise _failure(item, "CORRUPT")
    try:
        record = record_type.model_validate_json(item.raw)
    except ValidationError as error:
        raise _failure(item, "CORRUPT") from error
    digest = hashlib.sha256(item.raw).hexdigest()
    if (
        record.canonical_bytes() != item.raw
        or item.fingerprint != digest
        or item.record_id != "record:" + record.digest()
    ):
        raise _failure(item, "CORRUPT")
    return record


def _lifecycle(item: H1RawInventoryItem, record: RecoveryDTO) -> H1DecodedInventoryItem:
    physical = _physical(item)
    identities: list[H1ScopeKey] = [physical]
    relations: list[H1ScopeRelation] = []
    if isinstance(record, InitializedCallRecord):
        original = record.call.original
        run = _key(item, "run", original.original_run_id)
        turn = _key(item, "turn", original.original_turn_id)
        call = _key(item, "call", record.original_call_id)
        identities.extend((run, turn, call))
        relations.extend(
            (H1ScopeRelation("RUN_TURN", run, turn), H1ScopeRelation("TURN_CALL", turn, call))
        )
    elif isinstance(record, ToolCallAcceptedRecord):
        call = _key(item, "call", record.binding.original_call_id)
        accepted = _key(item, "intent", record.accepted_id)
        identities.extend((call, accepted))
        relations.append(H1ScopeRelation("CALL_INTENT", call, accepted))
    elif isinstance(record, ToolExecutionIntentRecord):
        call = _key(item, "call", record.original_call_id)
        intent = _key(item, "intent", record.execution_intent_id)
        identities.extend((call, intent))
        relations.append(H1ScopeRelation("CALL_INTENT", call, intent))
    elif isinstance(record, CancelledBeforeAcceptRecord):
        call = _key(item, "call", record.original_call_id)
        terminal = _key(item, "intent", record.terminal_id)
        identities.extend((call, terminal))
        relations.append(H1ScopeRelation("CALL_INTENT", call, terminal))
    elif isinstance(record, NotExecutedCallResultRecord):
        identities.extend(
            (_key(item, "call", record.original_call_id), _key(item, "outcome", record.result_id))
        )
    else:  # pragma: no cover - closed map above
        raise AssertionError(type(record))
    return H1DecodedInventoryItem(
        item.locator, tuple(identities), tuple(relations), (), ("CALL", "RECORD")
    )


def _recovery(item: H1RawInventoryItem) -> H1DecodedInventoryItem:
    if (
        item.surface != "PHYSICAL"
        or item.record_kind is None
        or (item.record_kind, item.schema) not in _RECOVERY
    ):
        raise _failure(item, "UNSUPPORTED")
    try:
        decoded = decode_recovery_record_member(
            RecoveryRecordMember(
                owner=item.owner,
                record_kind=item.record_kind,
                schema_id=item.schema,
                record_id=item.record_id or "",
                canonical_record_bytes=item.raw,
                fingerprint=item.fingerprint or "",
            )
        )
    except (RecoveryRecordIntegrityError, ValidationError) as error:
        raise _failure(item, "CORRUPT") from error
    record = decoded.record
    physical = _physical(item)
    identities: list[H1ScopeKey] = [physical]
    relations: list[H1ScopeRelation] = []
    families: tuple[H1InventoryFamily, ...] = ("CALL", "RECORD")
    if isinstance(record, OriginalResolutionBasis):
        obligation = _key(item, "obligation", record.original.obligation_id)
        identities.extend(
            (
                _key(item, "call", record.original.original_call_id),
                obligation,
                _key(item, "reduction", record.reduction_id),
            )
        )
        families = ("CALL", "OBLIGATION", "REDUCTION", "RECORD")
    elif isinstance(record, OriginalObligationClosureRecord):
        identities.extend(
            (
                _key(item, "call", record.original.original_call_id),
                _key(item, "obligation", record.original.obligation_id),
            )
        )
        families = ("CALL", "OBLIGATION", "RECORD")
    elif isinstance(record, RecoveredCallOutcomeRecord):
        identities.extend(
            (
                _key(item, "call", record.original.original_call_id),
                _key(item, "obligation", record.original.obligation_id),
                _key(item, "outcome", record.outcome_id),
            )
        )
        families = ("CALL", "OBLIGATION", "OUTCOME", "RECORD")
    elif isinstance(record, OriginalResolverBatchRecord):
        identities.append(_key(item, "record", record.batch_id))
    elif isinstance(record, LoopSemanticReductionRecord):
        reduction = _key(item, "reduction", record.reduction_id)
        identities.append(reduction)
        if record.anchor.kind == "RESOLVED":
            obligation = _key(item, "obligation", record.anchor.original.obligation_id)
            identities.append(obligation)
            relations.append(H1ScopeRelation("OBLIGATION_REDUCTION", obligation, reduction))
        families = ("REDUCTION", "RECORD")
    else:
        raise _failure(item, "UNSUPPORTED")
    return H1DecodedInventoryItem(item.locator, tuple(identities), tuple(relations), (), families)


def decode_h1_call_scope(item: H1RawInventoryItem) -> H1DecodedInventoryItem:
    """Decode one registered raw occurrence without storage, filtering, or authority claims."""
    if item.schema in _LIFECYCLE:
        return _lifecycle(item, _canonical_lifecycle(item))
    return _recovery(item)
