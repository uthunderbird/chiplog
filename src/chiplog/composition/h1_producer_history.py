"""Fail-closed pure interpreter for a supplied H1 effects-history cut.

``MaterializedEffectsCut`` is deliberately an input, not evidence minted here.
The caller must acquire it through the authenticated R16 reader.  This module
checks that the supplied cut and owner snapshot describe one complete selected
effects history, then preserves every selected row for H1 source capture.

Native V3 effects members are deliberately on hold.  Their selected issuance
and owner-exchange validator has not been mounted, so this interpreter never
turns a V3 row into absence evidence.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass

from chiplog.capabilities.effects.contracts import EffectRecord, ExactHead
from chiplog.capabilities.effects.dispatch_outcome_contracts import DispatchOutcomeRecordV2
from chiplog.capabilities.effects.dispatch_v2 import DispatchRecordV2
from chiplog.capabilities.effects.h1_local_preparation_record_contracts import (
    H1LocalPreparedCommentaryCanonicalMemberV1,
    decode_h1_local_prepared_commentary_member,
)
from chiplog.composition.r16_dispatch_history import (
    H1_LOCAL_PREPARED_COMMENTARY_SCHEMA,
    RECORD_SCHEMA,
    verify_dispatch_history,
)
from chiplog.composition.r16_effects import MaterializedEffectsCut
from chiplog.platform._owner_publication_contracts import CompleteDeliveryBatchV2, OwnerRecordBytes
from chiplog.platform.owner_decision_journal import OwnerJournalSnapshot
from chiplog.platform.owner_publications import SelectedOwnerDecision

_OUTCOME_SCHEMA = "chiplog.effects.dispatch-outcome-record.v2"
_LEGACY_SCHEMA = "chiplog.effects.record.v1"
_V3_SUFFIX = ".v3"
_DIGEST_DOMAIN = b"chiplog.h1-producer-effects-history.v1\x00"


class H1ProducerHistoryError(ValueError):
    """The supplied selected effects history is incomplete or inconsistent."""


class H1ProducerHistoryHold(H1ProducerHistoryError):
    """A selected member needs a validator that this H1 slice does not mount."""


@dataclass(frozen=True, slots=True)
class H1ProducerHistoryMember:
    """One exact selected effect envelope, in owner-publication order."""

    selected_decision: ExactHead
    tenant_commit_sequence: int
    publication_ordinal: int
    record_id: str
    record_kind: str
    schema_id: str
    fingerprint: str
    canonical_record_bytes: bytes
    intent_id: str


@dataclass(frozen=True, slots=True)
class H1ProducerEffectsHistory:
    """All selected effects rows plus a stable source-capture commitment."""

    tenant_id: str
    owner_journal_head: str | None
    ordered_members: tuple[H1ProducerHistoryMember, ...]
    digest: str

    def require_intent_absent(self, target_intent_id: str) -> None:
        """Reject when the exact cross-version intent identifier is present.

        The identity namespace is the effects intent-id namespace.  Tenant and
        selected-cut binding live on this history object, so callers cannot
        compare an identifier taken from another tenant without first selecting
        that tenant's history.
        """
        if type(target_intent_id) is not str or not target_intent_id:
            raise TypeError("target intent identity must be a nonempty string")
        if any(member.intent_id == target_intent_id for member in self.ordered_members):
            raise H1ProducerHistoryError("target intent is already present in selected effects")


def _record_fingerprint(record: OwnerRecordBytes) -> None:
    if hashlib.sha256(record.canonical_bytes).hexdigest() != record.fingerprint:
        raise H1ProducerHistoryError("selected effect envelope fingerprint differs from bytes")


def _known_schema(schema_id: str) -> bool:
    return schema_id in {
        _LEGACY_SCHEMA,
        RECORD_SCHEMA,
        _OUTCOME_SCHEMA,
        H1_LOCAL_PREPARED_COMMENTARY_SCHEMA,
    }


def _preflight_schema(record: OwnerRecordBytes) -> None:
    if record.schema_id.endswith(_V3_SUFFIX):
        raise H1ProducerHistoryHold(
            "selected V3 effects member is on hold pending its issuance/owner-exchange validator"
        )
    if not _known_schema(record.schema_id):
        raise H1ProducerHistoryError("unregistered selected effects history schema")


def _selected_effects(
    journal: OwnerJournalSnapshot,
) -> dict[str, tuple[SelectedOwnerDecision, int, OwnerRecordBytes]]:
    selected: dict[str, tuple[SelectedOwnerDecision, int, OwnerRecordBytes]] = {}
    for decision in journal.decisions:
        for ordinal, record in enumerate(decision.prepared.request.complete_records):
            if record.owner != "effects":
                continue
            _preflight_schema(record)
            if record.record_id in selected:
                raise H1ProducerHistoryError("duplicate effect record across selected batches")
            selected[record.record_id] = (decision, ordinal, record)
    return selected


def _decode_intent(record: OwnerRecordBytes, decision: SelectedOwnerDecision) -> str:
    """Canonical-decode every supported wire without projecting it as dispatch history."""
    _record_fingerprint(record)
    if record.schema_id == _LEGACY_SCHEMA:
        legacy = EffectRecord.model_validate_json(record.canonical_bytes)
        if (
            legacy.canonical_bytes() != record.canonical_bytes
            or legacy.record.head != record.record_id
            or record.record_kind != "effects." + legacy.kind
        ):
            raise H1ProducerHistoryError("legacy selected effect envelope differs from bytes")
        return legacy.snapshot.intent.intent_id
    if record.schema_id == RECORD_SCHEMA:
        dispatch = DispatchRecordV2.model_validate_json(record.canonical_bytes)
        if (
            dispatch.canonical_bytes() != record.canonical_bytes
            or dispatch.record.head != record.record_id
            or record.record_kind != "effects." + dispatch.kind
        ):
            raise H1ProducerHistoryError("V2 dispatch selected effect envelope differs from bytes")
        return dispatch.snapshot.intent.intent_id
    if record.schema_id == _OUTCOME_SCHEMA:
        outcome = DispatchOutcomeRecordV2.model_validate_json(record.canonical_bytes)
        if (
            outcome.canonical_bytes() != record.canonical_bytes
            or outcome.record.head != record.record_id
            or record.record_kind != "effects." + outcome.kind
        ):
            raise H1ProducerHistoryError("V2 outcome selected effect envelope differs from bytes")
        return outcome.snapshot.intent.intent_id
    if record.schema_id == H1_LOCAL_PREPARED_COMMENTARY_SCHEMA:
        if type(decision.prepared.request) is not CompleteDeliveryBatchV2:
            raise H1ProducerHistoryError(
                "H1 local selected effect has another publication envelope"
            )
        member = H1LocalPreparedCommentaryCanonicalMemberV1(
            record_id=record.record_id,
            canonical_record_bytes=record.canonical_bytes,
            fingerprint=record.fingerprint,
        )
        local = decode_h1_local_prepared_commentary_member(member)
        return local.intent_id
    raise AssertionError("schema preflight must have rejected this record")


def _digest(
    tenant_id: str, owner_journal_head: str | None, members: tuple[H1ProducerHistoryMember, ...]
) -> str:
    preimage = {
        "version": 1,
        "tenant_id": tenant_id,
        "owner_journal_head": owner_journal_head,
        "members": [
            {
                "decision": {
                    "subject_id": member.selected_decision.subject_id,
                    "head": member.selected_decision.head,
                    "fingerprint": member.selected_decision.fingerprint,
                },
                "tenant_commit_sequence": member.tenant_commit_sequence,
                "publication_ordinal": member.publication_ordinal,
                "record_id": member.record_id,
                "record_kind": member.record_kind,
                "schema_id": member.schema_id,
                "fingerprint": member.fingerprint,
                "canonical_record_bytes": base64.b64encode(member.canonical_record_bytes).decode(
                    "ascii"
                ),
                "intent_id": member.intent_id,
            }
            for member in members
        ],
    }
    raw = json.dumps(preimage, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(_DIGEST_DOMAIN + raw).hexdigest()


def interpret_h1_producer_effects_history(
    cut: MaterializedEffectsCut, journal: OwnerJournalSnapshot
) -> H1ProducerEffectsHistory:
    """Interpret one complete authenticated effects cut without authenticating it.

    ``read_materialized_effects`` / ``_read_materialized_effects_with_history``
    remains responsible for binding this supplied cut to physical storage.  A
    V3 selected row raises :class:`H1ProducerHistoryHold` even when its bytes
    decode, because this first slice has no selected-issuance exchange validator.
    """
    if cut.owner_journal_head != journal.head or cut.tenant_id != journal.tenant_id:
        raise H1ProducerHistoryError("effects history and owner selection cuts differ")
    selected = _selected_effects(journal)
    for row in cut.rows:
        _preflight_schema(row.record)

    # Preserve the old semantic V1/V2 verifier.  Its dispatch projection omits
    # H1-local members, so the independent ledger below remains authoritative
    # for complete raw selected membership.
    try:
        verify_dispatch_history(cut, journal)
    except H1ProducerHistoryError:
        raise
    except ValueError as error:
        raise H1ProducerHistoryError("legacy dispatch-history verification failed") from error

    members: list[H1ProducerHistoryMember] = []
    seen: set[str] = set()
    previous_position: tuple[int, int] | None = None
    for row in cut.rows:
        record = row.record
        entry = selected.get(record.record_id)
        if entry is None or record.record_id in seen:
            raise H1ProducerHistoryError(
                "effect record omitted, duplicated or outside selected history"
            )
        decision, ordinal, selected_record = entry
        position = (decision.tenant_commit_sequence, ordinal)
        if previous_position is not None and position <= previous_position:
            raise H1ProducerHistoryError("selected effects history reordered")
        previous_position = position
        seen.add(record.record_id)
        if record != selected_record:
            raise H1ProducerHistoryError("selected effect exact bytes or envelope substituted")
        intent_id = _decode_intent(record, decision)
        members.append(
            H1ProducerHistoryMember(
                selected_decision=ExactHead(
                    subject_id=decision.decision_id,
                    head=decision.decision_head,
                    fingerprint=decision.decision_fingerprint,
                ),
                tenant_commit_sequence=decision.tenant_commit_sequence,
                publication_ordinal=ordinal,
                record_id=record.record_id,
                record_kind=record.record_kind,
                schema_id=record.schema_id,
                fingerprint=record.fingerprint,
                canonical_record_bytes=record.canonical_bytes,
                intent_id=intent_id,
            )
        )
    if seen != set(selected):
        raise H1ProducerHistoryError("complete selected effects history omits selected members")
    frozen = tuple(members)
    return H1ProducerEffectsHistory(
        tenant_id=cut.tenant_id,
        owner_journal_head=cut.owner_journal_head,
        ordered_members=frozen,
        digest=_digest(cut.tenant_id, cut.owner_journal_head, frozen),
    )


__all__ = [
    "H1ProducerEffectsHistory",
    "H1ProducerHistoryError",
    "H1ProducerHistoryHold",
    "H1ProducerHistoryMember",
    "interpret_h1_producer_effects_history",
]
