"""Canonical H1 effects-history and normative-conflict generation wires.

The wires are owned by Effects so the pure scoped producer can validate their
complete selected membership without reaching into the composition reader.  A
broker still has to authenticate the physical cut that supplied these bytes.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Literal, Self

from pydantic import Field, model_validator

from .contracts import ExactHead
from .dispatch_authority_contracts import CapturedSource, Digest, DispatchObservationDTO, Identity

_HISTORY_DIGEST_DOMAIN = b"chiplog.h1-producer-effects-history.v1\x00"
H1_EFFECTS_HISTORY_SOURCE_ID = "chiplog.effects.h1-producer-effects-history"
H1_NORMATIVE_CONFLICT_GENERATION_SOURCE_ID = "chiplog.effects.h1-normative-conflict-generation"
_SOURCE_VERSION = "v1"
_OWNER_ID = "effects"
_HISTORY_READER_ID = "chiplog.effects.h1-scoped-producer-history-reader.v1"
_GENERATION_READER_ID = "chiplog.effects.h1-scoped-producer-generation-reader.v1"


class H1EffectsHistoryMemberV1(DispatchObservationDTO):
    """One complete selected member, in owner-publication order."""

    selected_decision: ExactHead
    tenant_commit_sequence: int = Field(ge=0)
    publication_ordinal: int = Field(ge=0)
    record_id: Identity
    record_kind: Identity
    schema_id: Identity
    fingerprint: Digest
    canonical_record_bytes: bytes = Field(min_length=1)
    intent_id: Identity

    @model_validator(mode="after")
    def exact_fingerprint(self) -> Self:
        if hashlib.sha256(self.canonical_record_bytes).hexdigest() != self.fingerprint:
            raise ValueError("H1 selected effects member fingerprint differs from bytes")
        return self


def h1_effects_history_digest(
    tenant_id: str,
    owner_journal_head: str | None,
    members: tuple[H1EffectsHistoryMemberV1, ...],
) -> str:
    """Use the H1 composition history digest preimage byte-for-byte."""
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
    return hashlib.sha256(_HISTORY_DIGEST_DOMAIN + raw).hexdigest()


class H1EffectsHistoryV1(DispatchObservationDTO):
    """An inert, complete H1 history cut; it makes no physical-cut claim."""

    schema_id: Literal["chiplog.effects.h1-effects-history.v1"] = (
        "chiplog.effects.h1-effects-history.v1"
    )
    tenant_id: Identity
    owner_journal_head: str | None
    ordered_members: tuple[H1EffectsHistoryMemberV1, ...]
    digest: Digest

    @model_validator(mode="after")
    def complete_ordered_members(self) -> Self:
        seen: set[str] = set()
        previous_position: tuple[int, int] | None = None
        for member in self.ordered_members:
            if member.record_id in seen:
                raise ValueError("H1 effects history has duplicate record id")
            position = (member.tenant_commit_sequence, member.publication_ordinal)
            if previous_position is not None and position <= previous_position:
                raise ValueError("H1 effects history members are not strictly ordered")
            seen.add(member.record_id)
            previous_position = position
        if self.digest != h1_effects_history_digest(
            self.tenant_id, self.owner_journal_head, self.ordered_members
        ):
            raise ValueError("H1 effects history digest differs from complete members")
        return self

    def require_intent_absent(self, target_intent_id: str) -> None:
        if type(target_intent_id) is not str or not target_intent_id:
            raise TypeError("target intent identity must be a nonempty string")
        if any(member.intent_id == target_intent_id for member in self.ordered_members):
            raise ValueError("target intent is already present in selected effects history")


def h1_effects_history_head(history: H1EffectsHistoryV1) -> ExactHead:
    raw = history.canonical_bytes()
    fingerprint = hashlib.sha256(raw).hexdigest()
    subject_id = H1_EFFECTS_HISTORY_SOURCE_ID + "/" + history.tenant_id
    return ExactHead(
        subject_id=subject_id, head=subject_id + "/" + fingerprint, fingerprint=fingerprint
    )


class H1NormativeConflictGenerationV1(DispatchObservationDTO):
    """The generation preimage intentionally contains only its schema, tenant and history digest."""

    schema_id: Literal["chiplog.effects.h1-normative-conflict-generation.v1"] = (
        "chiplog.effects.h1-normative-conflict-generation.v1"
    )
    tenant_id: Identity
    h1_effects_history_digest: Digest


def h1_normative_conflict_generation(
    history: H1EffectsHistoryV1,
) -> H1NormativeConflictGenerationV1:
    return H1NormativeConflictGenerationV1(
        tenant_id=history.tenant_id, h1_effects_history_digest=history.digest
    )


def h1_normative_conflict_generation_head(
    generation: H1NormativeConflictGenerationV1,
) -> ExactHead:
    raw = generation.canonical_bytes()
    fingerprint = hashlib.sha256(raw).hexdigest()
    subject_id = H1_NORMATIVE_CONFLICT_GENERATION_SOURCE_ID + "/" + generation.tenant_id
    return ExactHead(
        subject_id=subject_id, head=subject_id + "/" + fingerprint, fingerprint=fingerprint
    )


def _capture_head(source_id: str, raw: bytes) -> ExactHead:
    fingerprint = hashlib.sha256(raw).hexdigest()
    return ExactHead(
        subject_id=source_id, head=source_id + "/" + fingerprint, fingerprint=fingerprint
    )


def h1_effects_history_capture(
    history: H1EffectsHistoryV1, *, clock_contract: str, clock_epoch: str, valid_until_ns: int
) -> CapturedSource:
    raw = history.canonical_bytes()
    return CapturedSource(
        source_id=H1_EFFECTS_HISTORY_SOURCE_ID,
        source_version=_SOURCE_VERSION,
        owner_id=_OWNER_ID,
        reader_id=_HISTORY_READER_ID,
        invalidation_manifest=h1_effects_history_head(history),
        head=_capture_head(H1_EFFECTS_HISTORY_SOURCE_ID, raw),
        canonical_value=raw,
        clock_contract=clock_contract,
        clock_epoch=clock_epoch,
        valid_until_ns=valid_until_ns,
    )


def h1_normative_conflict_generation_capture(
    generation: H1NormativeConflictGenerationV1,
    *,
    clock_contract: str,
    clock_epoch: str,
    valid_until_ns: int,
) -> CapturedSource:
    raw = generation.canonical_bytes()
    return CapturedSource(
        source_id=H1_NORMATIVE_CONFLICT_GENERATION_SOURCE_ID,
        source_version=_SOURCE_VERSION,
        owner_id=_OWNER_ID,
        reader_id=_GENERATION_READER_ID,
        invalidation_manifest=h1_normative_conflict_generation_head(generation),
        head=_capture_head(H1_NORMATIVE_CONFLICT_GENERATION_SOURCE_ID, raw),
        canonical_value=raw,
        clock_contract=clock_contract,
        clock_epoch=clock_epoch,
        valid_until_ns=valid_until_ns,
    )


def require_h1_history_and_normative_conflict_generation(
    *,
    tenant_id: str,
    target_intent_id: str,
    original_history: CapturedSource,
    current_history: CapturedSource,
    original_generation: CapturedSource,
    current_generation: CapturedSource,
    history_observation: ExactHead,
    clock_contract: str,
    clock_epoch: str,
    valid_until_ns: int,
) -> ExactHead:
    """Validate both exact captures and return the derived generation head."""
    try:
        history = H1EffectsHistoryV1.model_validate_json(current_history.canonical_value)
    except (TypeError, ValueError) as error:
        raise ValueError("H1 effects history capture is not canonical history bytes") from error
    if (
        history.canonical_bytes() != current_history.canonical_value
        or history.tenant_id != tenant_id
    ):
        raise ValueError("H1 effects history capture differs from canonical tenant history")
    history.require_intent_absent(target_intent_id)
    expected_history = h1_effects_history_capture(
        history,
        clock_contract=clock_contract,
        clock_epoch=clock_epoch,
        valid_until_ns=valid_until_ns,
    )
    if original_history != expected_history or current_history != expected_history:
        raise ValueError("H1 original or current effects history capture differs")
    history_head = h1_effects_history_head(history)
    if history_observation != history_head:
        raise ValueError("H1 current history observation differs from selected history")
    generation = h1_normative_conflict_generation(history)
    expected_generation = h1_normative_conflict_generation_capture(
        generation,
        clock_contract=clock_contract,
        clock_epoch=clock_epoch,
        valid_until_ns=valid_until_ns,
    )
    if original_generation != expected_generation or current_generation != expected_generation:
        raise ValueError("H1 original or current normative conflict generation differs")
    generation_head = h1_normative_conflict_generation_head(generation)
    if expected_generation.head == generation_head:
        raise AssertionError("H1 captured source and generation head must remain distinct")
    return generation_head


__all__ = [
    "H1EffectsHistoryMemberV1",
    "H1EffectsHistoryV1",
    "H1NormativeConflictGenerationV1",
    "h1_effects_history_capture",
    "h1_effects_history_digest",
    "h1_effects_history_head",
    "h1_normative_conflict_generation",
    "h1_normative_conflict_generation_capture",
    "h1_normative_conflict_generation_head",
    "require_h1_history_and_normative_conflict_generation",
]
