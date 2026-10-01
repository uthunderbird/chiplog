"""Exact two-member owner codec for a V3 scoped dispatch authorization.

The codec only proves that already-observed inputs and two immutable owner
records agree.  It does not create a journal selection or grant SEND.
"""

from __future__ import annotations

import hashlib

from chiplog.platform._owner_publication_contracts import OwnerRecordBytes

from .contracts import ExactHead
from .dispatch_v2 import reference
from .dispatch_v2_contracts import CurrentDispatchInputsV2
from .fences import Absent
from .lifecycle_transition_contracts import SelectedEffectsSource
from .scoped_dispatch_contracts import ScopedPreSendCut, ScopedPreSendRevision
from .scoped_intent_contracts import ScopedAuthorityRecord, ScopedDispatchAuthorizationRecord
from .scoped_intent_record_contracts import make_scoped_intent_member

AUTHORIZATION_KIND = "ScopedDispatchAuthorization"
AUTHORIZATION_SCHEMA = "chiplog.effects.scoped-authorization.v3"
REVISION_KIND = "ScopedPreSendRevision"
REVISION_SCHEMA = "chiplog.effects.scoped-pre-send-revision.v3"


class ScopedAuthorizationRecordIntegrityError(ValueError):
    """A V3 authorization owner-record pair does not prove its required join."""


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def scoped_dispatch_authorization_head(
    authorization: ScopedDispatchAuthorizationRecord,
) -> ExactHead:
    """Return the authorization's normal reference head."""
    return reference(authorization.authorization_id, authorization.canonical_bytes())


def scoped_pre_send_revision_head(parent: ScopedPreSendRevision) -> ExactHead:
    """Return the revision head whose subject remains the original intent."""
    raw = parent.canonical_bytes()
    fingerprint = _sha256(raw)
    return ExactHead(
        subject_id=parent.original_intent.subject_id,
        head="scoped-pre-send:" + fingerprint,
        fingerprint=fingerprint,
    )


def make_scoped_authorization_owner_records(
    authorization: ScopedDispatchAuthorizationRecord,
    parent: ScopedPreSendRevision,
) -> tuple[OwnerRecordBytes, OwnerRecordBytes]:
    """Construct the exact ordinal-zero authorization and ordinal-one revision rows."""
    authorization_raw = authorization.canonical_bytes()
    parent_raw = parent.canonical_bytes()
    authorization_head = scoped_dispatch_authorization_head(authorization)
    parent_head = scoped_pre_send_revision_head(parent)
    return (
        OwnerRecordBytes(
            owner="effects",
            record_kind=AUTHORIZATION_KIND,
            record_id=authorization_head.head,
            schema_id=AUTHORIZATION_SCHEMA,
            canonical_bytes=authorization_raw,
            fingerprint=_sha256(authorization_raw),
        ),
        OwnerRecordBytes(
            owner="effects",
            record_kind=REVISION_KIND,
            record_id=parent_head.head,
            schema_id=REVISION_SCHEMA,
            canonical_bytes=parent_raw,
            fingerprint=_sha256(parent_raw),
        ),
    )


def decode_scoped_authorization_owner_records(
    records: tuple[OwnerRecordBytes, ...],
    *,
    observed: ScopedPreSendCut,
    current: CurrentDispatchInputsV2,
    complete_current_origin_sources: tuple[ScopedAuthorityRecord, ...],
) -> tuple[ScopedDispatchAuthorizationRecord, ScopedPreSendRevision]:
    """Decode an exact pair and bind it to independently observed current inputs.

    ``observed`` supplies existing selected sources.  This function neither
    constructs nor substitutes a selected decision.
    """
    if len(records) != 2:
        raise ScopedAuthorizationRecordIntegrityError("authorization pair must have two members")
    authorization_row, parent_row = records[0], records[1]
    authorization = _decode_authorization(authorization_row)
    parent = _decode_parent(parent_row)
    authorization_head = scoped_dispatch_authorization_head(authorization)
    parent_head = scoped_pre_send_revision_head(parent)

    _require_selected_intent(observed.selected_intent, observed)
    _require_initial_authorization_cut(observed)
    _require_selected_parent(observed.current_parent)
    selected_intent = observed.selected_intent.subject
    if (
        authorization.original_intent != selected_intent
        or parent.original_intent != selected_intent
    ):
        raise ScopedAuthorizationRecordIntegrityError(
            "authorization or parent original intent differs"
        )
    if (
        authorization.expected_parent != selected_intent
        or parent.predecessor != selected_intent
    ):
        raise ScopedAuthorizationRecordIntegrityError("authorization or parent predecessor differs")
    if parent.predecessor == authorization_head:
        raise ScopedAuthorizationRecordIntegrityError("parent predecessor is the new authorization")
    if parent.authorization != authorization_head:
        raise ScopedAuthorizationRecordIntegrityError("parent authorization differs")
    if parent.state != "DISPATCH_AUTHORIZED" or parent.retired_authorizations != ():
        raise ScopedAuthorizationRecordIntegrityError(
            "parent is not the initial authorized revision"
        )
    current_fingerprint = _sha256(current.canonical_bytes())
    if (
        authorization.current_inputs_fingerprint != current_fingerprint
        or parent.current_inputs_fingerprint != current_fingerprint
    ):
        raise ScopedAuthorizationRecordIntegrityError("current inputs fingerprint differs")
    if (
        authorization.complete_current_origin_sources != complete_current_origin_sources
        or parent.complete_current_origin_sources != complete_current_origin_sources
    ):
        raise ScopedAuthorizationRecordIntegrityError("current origin sources differ")
    if parent_head.subject_id != observed.selected_intent.subject.subject_id:
        raise ScopedAuthorizationRecordIntegrityError("parent head subject differs")
    return authorization, parent


def _decode_authorization(row: OwnerRecordBytes) -> ScopedDispatchAuthorizationRecord:
    _require_row(row, kind=AUTHORIZATION_KIND, schema=AUTHORIZATION_SCHEMA)
    try:
        authorization = ScopedDispatchAuthorizationRecord.model_validate_json(row.canonical_bytes)
    except ValueError as error:
        raise ScopedAuthorizationRecordIntegrityError("malformed authorization bytes") from error
    if authorization.canonical_bytes() != row.canonical_bytes:
        raise ScopedAuthorizationRecordIntegrityError("authorization bytes are not canonical")
    head = scoped_dispatch_authorization_head(authorization)
    if row.record_id != head.head or row.fingerprint != head.fingerprint:
        raise ScopedAuthorizationRecordIntegrityError("authorization head differs")
    return authorization


def _decode_parent(row: OwnerRecordBytes) -> ScopedPreSendRevision:
    _require_row(row, kind=REVISION_KIND, schema=REVISION_SCHEMA)
    try:
        parent = ScopedPreSendRevision.model_validate_json(row.canonical_bytes)
    except ValueError as error:
        raise ScopedAuthorizationRecordIntegrityError(
            "malformed pre-send revision bytes"
        ) from error
    if parent.canonical_bytes() != row.canonical_bytes:
        raise ScopedAuthorizationRecordIntegrityError("pre-send revision bytes are not canonical")
    head = scoped_pre_send_revision_head(parent)
    if row.record_id != head.head or row.fingerprint != head.fingerprint:
        raise ScopedAuthorizationRecordIntegrityError("pre-send revision head or subject differs")
    return parent


def _require_row(row: OwnerRecordBytes, *, kind: str, schema: str) -> None:
    if row.owner != "effects" or row.record_kind != kind or row.schema_id != schema:
        raise ScopedAuthorizationRecordIntegrityError("owner record envelope differs")
    if _sha256(row.canonical_bytes) != row.fingerprint:
        raise ScopedAuthorizationRecordIntegrityError("owner record fingerprint differs")


def _require_initial_authorization_cut(observed: ScopedPreSendCut) -> None:
    if (
        observed.state != "INTENT_RECORDED"
        or type(observed.authorization) is not Absent
        or observed.current_parent != observed.selected_intent
    ):
        raise ScopedAuthorizationRecordIntegrityError("observed cut is not initial authorization")


def _require_selected_intent(source: SelectedEffectsSource, observed: ScopedPreSendCut) -> None:
    member = make_scoped_intent_member(observed.original_intent)
    expected = ExactHead(
        subject_id=member.record_id,
        head=member.record_id,
        fingerprint=member.fingerprint,
    )
    if (
        source.owner != "effects"
        or source.schema_id != "chiplog.effects.external-action-intent.v3"
        or source.subject != expected
        or source.physical_record != expected
        or source.canonical_record_bytes != member.canonical_record_bytes
    ):
        raise ScopedAuthorizationRecordIntegrityError("observed selected intent differs")


def _require_selected_parent(source: SelectedEffectsSource) -> None:
    if (
        source.owner != "effects"
        or source.subject != source.physical_record
        or _sha256(source.canonical_record_bytes) != source.subject.fingerprint
    ):
        raise ScopedAuthorizationRecordIntegrityError("observed selected parent differs")
