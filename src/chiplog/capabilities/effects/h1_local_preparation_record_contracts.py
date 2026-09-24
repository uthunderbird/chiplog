"""Closed physical codec for H1 local Commentary preparation records."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .h1_local_preparation_contracts import INTENT_SCHEMA, H1LocalPreparedCommentaryIntentV1

RECORD_KIND: Literal["H1LocalPreparedCommentaryIntent"] = "H1LocalPreparedCommentaryIntent"
SCHEMA_ID: Literal["chiplog.effects.h1-local-prepared-commentary-intent.v1"] = INTENT_SCHEMA


def _json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class H1LocalPreparationRecordIntegrityError(ValueError):
    """A bounded local-record integrity failure."""


class H1LocalPreparedCommentaryCanonicalMemberV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    owner: Literal["effects"] = "effects"
    record_kind: Literal["H1LocalPreparedCommentaryIntent"] = RECORD_KIND
    schema_id: Literal["chiplog.effects.h1-local-prepared-commentary-intent.v1"] = SCHEMA_ID
    record_id: str = Field(min_length=1)
    canonical_record_bytes: bytes = Field(min_length=1)
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


def h1_local_intent_fingerprint(intent: H1LocalPreparedCommentaryIntentV1) -> str:
    """Hash every semantic field except the intent's self fingerprint."""
    return _sha256(_json(intent.model_dump(mode="json", exclude={"fingerprint"})))


def make_h1_local_prepared_commentary_member(
    intent: H1LocalPreparedCommentaryIntentV1,
) -> H1LocalPreparedCommentaryCanonicalMemberV1:
    raw = intent.canonical_bytes()
    return H1LocalPreparedCommentaryCanonicalMemberV1(
        record_id=intent.intent_id,
        canonical_record_bytes=raw,
        fingerprint=_sha256(raw),
    )


def decode_h1_local_prepared_commentary_member(
    member: H1LocalPreparedCommentaryCanonicalMemberV1,
) -> H1LocalPreparedCommentaryIntentV1:
    if (
        member.owner != "effects"
        or member.record_kind != RECORD_KIND
        or member.schema_id != SCHEMA_ID
    ):
        raise H1LocalPreparationRecordIntegrityError("unexpected local preparation member envelope")
    if _sha256(member.canonical_record_bytes) != member.fingerprint:
        raise H1LocalPreparationRecordIntegrityError("member fingerprint differs from bytes")
    try:
        intent = H1LocalPreparedCommentaryIntentV1.model_validate_json(
            member.canonical_record_bytes
        )
    except Exception as error:
        raise H1LocalPreparationRecordIntegrityError(
            "malformed local preparation intent bytes"
        ) from error
    if intent.canonical_bytes() != member.canonical_record_bytes:
        raise H1LocalPreparationRecordIntegrityError("intent bytes are not canonical")
    if intent.intent_id != member.record_id or intent.schema_id != member.schema_id:
        raise H1LocalPreparationRecordIntegrityError("member envelope differs from intent identity")
    if h1_local_intent_fingerprint(intent) != intent.fingerprint:
        raise H1LocalPreparationRecordIntegrityError("semantic intent fingerprint differs")
    return intent


def h1_local_complete_owner_commitment(
    member: H1LocalPreparedCommentaryCanonicalMemberV1,
) -> str:
    """Bind the sole local effects member; this creates no publication authority."""
    decode_h1_local_prepared_commentary_member(member)
    return _sha256(
        _json([[member.record_kind, member.record_id, member.schema_id, member.fingerprint]])
    )
