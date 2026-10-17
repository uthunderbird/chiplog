"""Physical three-record codec for an already-prepared scoped V3 first send.

This codec verifies immutable bytes against an independently supplied preparation
request.  It does not select any source, authenticate a journal decision, or
grant permission to SEND.
"""

from __future__ import annotations

import hashlib

from chiplog.platform._owner_publication_contracts import OwnerRecordBytes

from .contracts import ExactHead, TransmissionAttempt
from .dispatch_v2 import reference
from .h1_scoped_dispatch_semantics import PREPARE_SCOPED_FIRST_SEND_V3
from .scoped_authorization_record_contracts import (
    REVISION_SCHEMA,
    scoped_dispatch_authorization_head,
    scoped_pre_send_revision_head,
)
from .scoped_dispatch_contracts import (
    PreparedScopedFirstSend,
    PrepareScopedFirstSend,
    ScopedFirstSendDecision,
    ScopedFirstSendParent,
    ScopedPreSendRevision,
    SelectedScopedAuthorization,
)
from .scoped_dispatch_preparation import _require_authorities, _require_current
from .scoped_intent_contracts import ScopedDispatchAuthorizationRecord
from .scoped_intent_record_contracts import make_scoped_intent_member

DECISION_KIND = "ScopedFirstSendDecision"
DECISION_SCHEMA = "chiplog.effects.scoped-first-send-decision.v3"
CHILD_KIND = "ScopedFirstSendChild"
CHILD_SCHEMA = "chiplog.effects.scoped-first-send-child.v3"
PARENT_KIND = "ScopedFirstSendParent"
PARENT_SCHEMA = "chiplog.effects.scoped-first-send-parent.v3"

_DECISION_HEAD_DOMAIN = b"chiplog.effects.scoped-first-send.v3\x00"
_BATCH_DOMAIN = b"chiplog.effects.scoped-first-send-batch.v3\x00"


class ScopedFirstSendRecordIntegrityError(ValueError):
    """The three owner rows do not prove one exact first-send preparation."""


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def scoped_first_send_decision_head(decision: ScopedFirstSendDecision) -> ExactHead:
    """The semantic SEND-commit head, deliberately distinct from row storage."""
    fingerprint = _sha256(_DECISION_HEAD_DOMAIN + decision.canonical_bytes())
    return ExactHead(
        subject_id=decision.identity.command_id,
        head="scoped-first-send:" + fingerprint,
        fingerprint=fingerprint,
    )


def scoped_first_send_decision_record_head(decision: ScopedFirstSendDecision) -> ExactHead:
    """The physical decision-row head, derived from undecorated canonical bytes."""
    return reference(decision.identity.command_id, decision.canonical_bytes())


def scoped_first_send_child_record_head(child: TransmissionAttempt) -> ExactHead:
    """The physical child-row head; ``child.transmission`` remains a decision reference."""
    return reference(child.transmission.subject_id, child.canonical_bytes())


def scoped_first_send_parent_record_head(parent: ScopedFirstSendParent) -> ExactHead:
    """The physical parent-row head under the stable original-intent subject."""
    return reference(parent.original_intent.subject_id, parent.canonical_bytes())


def make_scoped_first_send_owner_records(
    result: PreparedScopedFirstSend,
) -> tuple[OwnerRecordBytes, OwnerRecordBytes, OwnerRecordBytes]:
    """Return decision, child, parent rows in the fixed physical publication order."""
    decision, child, parent = result.decision, result.child, result.parent
    return (
        _row(
            DECISION_KIND,
            DECISION_SCHEMA,
            scoped_first_send_decision_record_head(decision),
            decision.canonical_bytes(),
        ),
        _row(
            CHILD_KIND,
            CHILD_SCHEMA,
            scoped_first_send_child_record_head(child),
            child.canonical_bytes(),
        ),
        _row(
            PARENT_KIND,
            PARENT_SCHEMA,
            scoped_first_send_parent_record_head(parent),
            parent.canonical_bytes(),
        ),
    )


def decode_scoped_first_send_owner_records(
    records: tuple[OwnerRecordBytes, ...], *, request: PrepareScopedFirstSend
) -> PreparedScopedFirstSend:
    """Rebuild an exact prepared value from rows bound to an observed request.

    The returned DTO is only a decoded record aggregate.  In particular it has
    no ``SelectedEffectsSource`` and is not a SEND capability.
    """
    if len(records) != 3:
        raise ScopedFirstSendRecordIntegrityError("first-send batch must have three members")
    decision = _decode_decision(records[0])
    child = _decode_child(records[1])
    parent = _decode_parent(records[2])
    _require_request(request, decision, child, parent)
    batch_fingerprint = _batch_fingerprint(decision, child, parent)
    decoded = PreparedScopedFirstSend(
        source_request_fingerprint=_sha256(request.canonical_bytes()),
        decision=decision,
        child=child,
        parent=parent,
        complete_batch_fingerprint=batch_fingerprint,
    )
    return decoded


def _row(kind: str, schema: str, head: ExactHead, raw: bytes) -> OwnerRecordBytes:
    return OwnerRecordBytes(
        owner="effects",
        record_kind=kind,
        record_id=head.head,
        schema_id=schema,
        canonical_bytes=raw,
        fingerprint=_sha256(raw),
    )


def _decode_decision(row: OwnerRecordBytes) -> ScopedFirstSendDecision:
    _require_row(row, DECISION_KIND, DECISION_SCHEMA)
    try:
        value = ScopedFirstSendDecision.model_validate_json(row.canonical_bytes)
    except ValueError as error:
        raise ScopedFirstSendRecordIntegrityError("malformed first-send decision bytes") from error
    if (
        value.canonical_bytes() != row.canonical_bytes
        or row.record_id != scoped_first_send_decision_record_head(value).head
    ):
        raise ScopedFirstSendRecordIntegrityError("decision bytes or physical head differs")
    return value


def _decode_child(row: OwnerRecordBytes) -> TransmissionAttempt:
    _require_row(row, CHILD_KIND, CHILD_SCHEMA)
    try:
        value = TransmissionAttempt.model_validate_json(row.canonical_bytes)
    except ValueError as error:
        raise ScopedFirstSendRecordIntegrityError("malformed first-send child bytes") from error
    if (
        value.canonical_bytes() != row.canonical_bytes
        or row.record_id != scoped_first_send_child_record_head(value).head
    ):
        raise ScopedFirstSendRecordIntegrityError("child bytes or physical head differs")
    return value


def _decode_parent(row: OwnerRecordBytes) -> ScopedFirstSendParent:
    _require_row(row, PARENT_KIND, PARENT_SCHEMA)
    try:
        value = ScopedFirstSendParent.model_validate_json(row.canonical_bytes)
    except ValueError as error:
        raise ScopedFirstSendRecordIntegrityError("malformed first-send parent bytes") from error
    if (
        value.canonical_bytes() != row.canonical_bytes
        or row.record_id != scoped_first_send_parent_record_head(value).head
    ):
        raise ScopedFirstSendRecordIntegrityError("parent bytes or physical head differs")
    return value


def _require_row(row: OwnerRecordBytes, kind: str, schema: str) -> None:
    if row.owner != "effects" or row.record_kind != kind or row.schema_id != schema:
        raise ScopedFirstSendRecordIntegrityError("first-send row envelope differs")
    if _sha256(row.canonical_bytes) != row.fingerprint:
        raise ScopedFirstSendRecordIntegrityError("first-send row fingerprint differs")


def _require_request(
    request: PrepareScopedFirstSend,
    decision: ScopedFirstSendDecision,
    child: TransmissionAttempt,
    parent: ScopedFirstSendParent,
) -> None:
    if request.ordinal != 0 or request.observed.state != "DISPATCH_AUTHORIZED":
        raise ScopedFirstSendRecordIntegrityError("request is not an ordinal-zero authorized cut")
    try:
        _require_current(
            request, request.observed.original_intent, operation=PREPARE_SCOPED_FIRST_SEND_V3
        )
    except ValueError as error:
        raise ScopedFirstSendRecordIntegrityError("current first-send request differs") from error
    intent_head = _require_selected_intent(request)
    authorization, prior_parent = _require_selected_authorization_and_parent(request, intent_head)
    request_fingerprint = _sha256(request.canonical_bytes())
    current_fingerprint = _sha256(request.current.canonical_bytes())
    if (
        decision.identity != request.identity
        or decision.source_request_fingerprint != request_fingerprint
        or decision.original_intent != intent_head
        or decision.authorization != request.selected_authorization
        or decision.prior_parent != prior_parent
        or decision.ordinal != 0
        or decision.current_inputs_fingerprint != current_fingerprint
        or decision.complete_current_origin_sources != request.complete_current_origin_sources
        or decision.fence != request.fence
    ):
        raise ScopedFirstSendRecordIntegrityError("decision does not join the exact request")
    semantic_head = scoped_first_send_decision_head(decision)
    mandate = request.observed.original_intent.mandate
    expected_transmission = reference(
        intent_head.subject_id + "/transmission/0", decision.canonical_bytes()
    )
    if (
        child.transmission != expected_transmission
        or child.intent != intent_head
        or child.ordinal != 0
        or child.semantics != mandate.semantics
        or child.dispatch_time_ns != request.current.observed_time_ns
        or child.payload_fingerprint != mandate.effect_fingerprint
        or child.recipient != mandate.recipient
        or child.idempotency_fence_key != mandate.idempotency_fence_key
        or child.send_commit != semantic_head
        or child.coverage_proof is not None
    ):
        raise ScopedFirstSendRecordIntegrityError("child does not join the decision and mandate")
    if (
        parent.original_intent != intent_head
        or parent.predecessor != prior_parent
        or parent.state != "SEND_COMMITTED"
        or parent.send_decision != semantic_head
        or parent.first_child != scoped_first_send_child_record_head(child)
    ):
        raise ScopedFirstSendRecordIntegrityError("parent does not join the first child")
    if authorization.original_intent != intent_head:
        raise ScopedFirstSendRecordIntegrityError("selected authorization intent differs")


def _require_selected_intent(request: PrepareScopedFirstSend) -> ExactHead:
    member = make_scoped_intent_member(request.observed.original_intent)
    expected = ExactHead(
        subject_id=member.record_id,
        head=member.record_id,
        fingerprint=member.fingerprint,
    )
    source = request.observed.selected_intent
    if (
        source.owner != "effects"
        or source.schema_id != "chiplog.effects.external-action-intent.v3"
        or source.subject != expected
        or source.physical_record != expected
        or source.canonical_record_bytes != member.canonical_record_bytes
    ):
        raise ScopedFirstSendRecordIntegrityError("selected intent differs")
    return expected


def _require_selected_authorization_and_parent(
    request: PrepareScopedFirstSend, intent_head: ExactHead
) -> tuple[ScopedDispatchAuthorizationRecord, ExactHead]:
    selected = request.observed.authorization
    if not isinstance(selected, SelectedScopedAuthorization):
        raise ScopedFirstSendRecordIntegrityError("selected authorization is absent or malformed")
    authorization = selected.record
    authorization_head = scoped_dispatch_authorization_head(authorization)
    source = selected.source
    if (
        source.owner != "effects"
        or source.schema_id != "chiplog.effects.scoped-authorization.v3"
        or source.subject != authorization_head
        or source.physical_record
        != ExactHead(
            subject_id=authorization_head.head,
            head=authorization_head.head,
            fingerprint=_sha256(authorization.canonical_bytes()),
        )
        or source.canonical_record_bytes != authorization.canonical_bytes()
        or request.selected_authorization != authorization_head
    ):
        raise ScopedFirstSendRecordIntegrityError("selected authorization differs")
    try:
        _require_authorities(authorization.complete_current_origin_sources)
    except ValueError as error:
        raise ScopedFirstSendRecordIntegrityError(
            "selected authorization current origin sources differ"
        ) from error
    mandate = request.observed.original_intent.mandate
    if (
        authorization.original_intent != intent_head
        or authorization.immutable_mandate
        != reference(mandate.mandate_id, mandate.canonical_bytes())
        or authorization.semantics != mandate.semantics
    ):
        raise ScopedFirstSendRecordIntegrityError("selected authorization mandate differs")
    parent_source = request.observed.current_parent
    if parent_source.owner != "effects" or parent_source.schema_id != REVISION_SCHEMA:
        raise ScopedFirstSendRecordIntegrityError("prior authorized parent envelope differs")
    try:
        parent = ScopedPreSendRevision.model_validate_json(parent_source.canonical_record_bytes)
    except ValueError as error:
        raise ScopedFirstSendRecordIntegrityError(
            "prior authorized parent bytes are malformed"
        ) from error
    if (
        parent.canonical_bytes() != parent_source.canonical_record_bytes
        or parent_source.subject != scoped_pre_send_revision_head(parent)
        or parent_source.physical_record
        != _physical_row_head(parent_source.subject, parent_source.canonical_record_bytes)
        or _sha256(parent_source.canonical_record_bytes)
        != parent_source.physical_record.fingerprint
        or parent.state != "DISPATCH_AUTHORIZED"
        or parent.original_intent != intent_head
        or parent.authorization != authorization_head
        or parent.predecessor != authorization.expected_parent
        or parent.current_inputs_fingerprint != authorization.current_inputs_fingerprint
        or parent.complete_current_origin_sources != authorization.complete_current_origin_sources
    ):
        raise ScopedFirstSendRecordIntegrityError("prior authorized parent differs")
    return authorization, parent_source.subject


def _physical_row_head(semantic: ExactHead, raw: bytes) -> ExactHead:
    """Project a retained owner record id into its physical selected-source head."""
    return ExactHead(
        subject_id=semantic.head,
        head=semantic.head,
        fingerprint=_sha256(raw),
    )


def _batch_fingerprint(
    decision: ScopedFirstSendDecision, child: TransmissionAttempt, parent: ScopedFirstSendParent
) -> str:
    return _sha256(
        _BATCH_DOMAIN
        + decision.canonical_bytes()
        + child.canonical_bytes()
        + parent.canonical_bytes()
    )
