"""H1 normative-conflict generation is a pure, physical-provenance-free contract."""

from __future__ import annotations

import hashlib

import pytest

import chiplog.composition.h1_producer_history as composition_history
from chiplog.capabilities.effects.contracts import ExactHead
from chiplog.capabilities.effects.h1_normative_conflict_generation import (
    H1EffectsHistoryMemberV1,
    H1EffectsHistoryV1,
    h1_effects_history_capture,
    h1_effects_history_digest,
    h1_effects_history_head,
    h1_normative_conflict_generation,
    h1_normative_conflict_generation_capture,
    h1_normative_conflict_generation_head,
    require_h1_history_and_normative_conflict_generation,
)


def _member(record_id: str, position: tuple[int, int]) -> H1EffectsHistoryMemberV1:
    raw = ("record:" + record_id).encode()
    return H1EffectsHistoryMemberV1(
        selected_decision=ExactHead(
            subject_id="decision-" + record_id,
            head="decision-" + record_id + "/head",
            fingerprint=hashlib.sha256(("decision:" + record_id).encode()).hexdigest(),
        ),
        tenant_commit_sequence=position[0],
        publication_ordinal=position[1],
        record_id=record_id,
        record_kind="effects.INTENT_RECORDED",
        schema_id="chiplog.effects.record.v1",
        fingerprint=hashlib.sha256(raw).hexdigest(),
        canonical_record_bytes=raw,
        intent_id="intent-" + record_id,
    )


def _history(members: tuple[H1EffectsHistoryMemberV1, ...] | None = None) -> H1EffectsHistoryV1:
    ordered = members if members is not None else (_member("one", (3, 0)), _member("two", (4, 1)))
    return H1EffectsHistoryV1(
        tenant_id="tenant",
        owner_journal_head="owner/head",
        ordered_members=ordered,
        digest=h1_effects_history_digest("tenant", "owner/head", ordered),
    )


def test_history_digest_matches_composition_h1_producer_preimage_exactly() -> None:
    history = _history()
    composition_members = tuple(
        composition_history.H1ProducerHistoryMember(
            selected_decision=member.selected_decision,
            tenant_commit_sequence=member.tenant_commit_sequence,
            publication_ordinal=member.publication_ordinal,
            record_id=member.record_id,
            record_kind=member.record_kind,
            schema_id=member.schema_id,
            fingerprint=member.fingerprint,
            canonical_record_bytes=member.canonical_record_bytes,
            intent_id=member.intent_id,
        )
        for member in history.ordered_members
    )
    assert history.digest == composition_history._digest(
        history.tenant_id, history.owner_journal_head, composition_members
    )


@pytest.mark.parametrize(
    "members",
    (
        (_member("one", (3, 0)), _member("one", (4, 0))),
        (_member("one", (4, 0)), _member("two", (3, 0))),
    ),
)
def test_history_rejects_duplicate_or_reordered_members(
    members: tuple[H1EffectsHistoryMemberV1, ...],
) -> None:
    with pytest.raises(ValueError):
        _history(members)


def test_history_rejects_fingerprint_mutation() -> None:
    member = _member("one", (3, 0))
    with pytest.raises(ValueError):
        H1EffectsHistoryMemberV1.model_validate(
            {**member.model_dump(), "fingerprint": "0" * 64}
        )


def test_generation_uses_only_versioned_tenant_and_verified_history_digest() -> None:
    generation = h1_normative_conflict_generation(_history())
    assert set(generation.model_dump()) == {"schema_id", "tenant_id", "h1_effects_history_digest"}


def test_capture_head_is_distinct_from_generation_head_and_hashes_its_value() -> None:
    generation = h1_normative_conflict_generation(_history())
    capture = h1_normative_conflict_generation_capture(
        generation, clock_contract="clock", clock_epoch="epoch", valid_until_ns=10
    )
    generation_head = h1_normative_conflict_generation_head(generation)
    assert capture.head != generation_head
    assert capture.head.fingerprint == hashlib.sha256(capture.canonical_value).hexdigest()


def test_owner_contract_accepts_self_consistent_cut_without_claiming_physical_provenance() -> None:
    # A broker must separately establish that this one-member history is complete.
    history = _history((_member("fabricated", (0, 0)),))
    generation = h1_normative_conflict_generation(history)
    history_capture = h1_effects_history_capture(
        history, clock_contract="clock", clock_epoch="epoch", valid_until_ns=10
    )
    generation_capture = h1_normative_conflict_generation_capture(
        generation, clock_contract="clock", clock_epoch="epoch", valid_until_ns=10
    )
    assert require_h1_history_and_normative_conflict_generation(
        tenant_id="tenant",
        target_intent_id="new-intent",
        original_history=history_capture,
        current_history=history_capture,
        original_generation=generation_capture,
        current_generation=generation_capture,
        history_observation=h1_effects_history_head(history),
        clock_contract="clock",
        clock_epoch="epoch",
        valid_until_ns=10,
    ) == h1_normative_conflict_generation_head(generation)


def test_owner_contract_rejects_target_intent_already_in_self_consistent_history() -> None:
    history = _history((_member("already-present", (0, 0)),))
    generation = h1_normative_conflict_generation(history)
    history_capture = h1_effects_history_capture(
        history, clock_contract="clock", clock_epoch="epoch", valid_until_ns=10
    )
    generation_capture = h1_normative_conflict_generation_capture(
        generation, clock_contract="clock", clock_epoch="epoch", valid_until_ns=10
    )
    with pytest.raises(ValueError, match="already present"):
        require_h1_history_and_normative_conflict_generation(
            tenant_id="tenant",
            target_intent_id="intent-already-present",
            original_history=history_capture,
            current_history=history_capture,
            original_generation=generation_capture,
            current_generation=generation_capture,
            history_observation=h1_effects_history_head(history),
            clock_contract="clock",
            clock_epoch="epoch",
            valid_until_ns=10,
        )


def test_owner_contract_rejects_truncated_capture_with_old_digest_or_generation() -> None:
    complete = _history()
    truncated = complete.model_copy(update={"ordered_members": complete.ordered_members[:1]})
    generation = h1_normative_conflict_generation(complete)
    with pytest.raises(ValueError):
        require_h1_history_and_normative_conflict_generation(
            tenant_id="tenant",
            target_intent_id="new-intent",
            original_history=h1_effects_history_capture(
                truncated, clock_contract="clock", clock_epoch="epoch", valid_until_ns=10
            ),
            current_history=h1_effects_history_capture(
                truncated, clock_contract="clock", clock_epoch="epoch", valid_until_ns=10
            ),
            original_generation=h1_normative_conflict_generation_capture(
                generation, clock_contract="clock", clock_epoch="epoch", valid_until_ns=10
            ),
            current_generation=h1_normative_conflict_generation_capture(
                generation, clock_contract="clock", clock_epoch="epoch", valid_until_ns=10
            ),
            history_observation=h1_effects_history_head(truncated),
            clock_contract="clock",
            clock_epoch="epoch",
            valid_until_ns=10,
        )
