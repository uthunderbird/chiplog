"""Physical H1 producer effects-history source boundary."""

from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

import chiplog.composition.h1_producer_sources as sources
from chiplog.adapters.driven.effects_queries import StoredEffectRow
from chiplog.capabilities.effects.contracts import ExactHead
from chiplog.capabilities.effects.h1_normative_conflict_generation import h1_effects_history_digest
from chiplog.composition.h1_producer_history import (
    H1ProducerEffectsHistory,
    H1ProducerHistoryMember,
)
from chiplog.composition.r16_effects import MaterializedEffectsCut
from chiplog.platform._owner_publication_contracts import OwnerRecordBytes


def test_conversion_preserves_selected_bytes_and_rejects_duplicate_intent() -> None:
    raw = b'{"record":"exact"}'
    member = H1ProducerHistoryMember(
        selected_decision=ExactHead(
            subject_id="selected", head="selected/head", fingerprint="a" * 64
        ),
        tenant_commit_sequence=1,
        publication_ordinal=0,
        record_id="record",
        record_kind="effects.INTENT_RECORDED",
        schema_id="chiplog.effects.record.v1",
        fingerprint=hashlib.sha256(raw).hexdigest(),
        canonical_record_bytes=raw,
        intent_id="existing-intent",
    )
    history_member = sources.H1EffectsHistoryMemberV1(
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
    digest = h1_effects_history_digest("tenant", "head", (history_member,))
    interpreted = H1ProducerEffectsHistory(
        tenant_id="tenant", owner_journal_head="head", ordered_members=(member,), digest=digest
    )
    converted = sources._effects_history(interpreted)
    assert converted.ordered_members[0].canonical_record_bytes == raw
    with pytest.raises(ValueError, match="already present"):
        converted.require_intent_absent("existing-intent")
    with pytest.raises(ValueError, match="fingerprint"):
        sources.H1EffectsHistoryMemberV1(
            selected_decision=member.selected_decision,
            tenant_commit_sequence=member.tenant_commit_sequence,
            publication_ordinal=member.publication_ordinal,
            record_id=member.record_id,
            record_kind=member.record_kind,
            schema_id=member.schema_id,
            fingerprint=member.fingerprint,
            canonical_record_bytes=raw + b" ",
            intent_id=member.intent_id,
        )


def test_physical_cut_head_commits_frontier_outside_history_digest() -> None:
    raw = b'{"record":"exact"}'
    fingerprint = hashlib.sha256(raw).hexdigest()
    selected = ExactHead(subject_id="selected", head="selected/head", fingerprint="a" * 64)
    history_member = sources.H1EffectsHistoryMemberV1(
        selected_decision=selected,
        tenant_commit_sequence=1,
        publication_ordinal=0,
        record_id="record",
        record_kind="effects.INTENT_RECORDED",
        schema_id="chiplog.effects.record.v1",
        fingerprint=fingerprint,
        canonical_record_bytes=raw,
        intent_id="intent",
    )
    history = sources.H1EffectsHistoryV1(
        tenant_id="tenant",
        owner_journal_head="owner-head",
        ordered_members=(history_member,),
        digest=h1_effects_history_digest("tenant", "owner-head", (history_member,)),
    )
    row = StoredEffectRow(
        commit_sequence=1,
        publication_ordinal=0,
        batch_record_ids=("record",),
        record=OwnerRecordBytes(
            owner="effects",
            record_kind="effects.INTENT_RECORDED",
            record_id="record",
            schema_id="chiplog.effects.record.v1",
            canonical_bytes=raw,
            fingerprint=fingerprint,
        ),
    )
    cut = MaterializedEffectsCut(
        tenant_id="tenant",
        tenant_frontier=1,
        materialization_commitment="commitment",
        owner_journal_head="owner-head",
        physical_path="/authenticated/database.sqlite3",
        physical_device=1,
        physical_inode=2,
        rows=(row,),
        worker=None,
        latest_runs=(),
    )

    head = sources._physical_cut_head(cut, history)
    assert head == sources._physical_cut_head(cut, history)
    assert head.subject_id == "chiplog.h1-producer.physical-cut.v1"
    assert head.head == head.subject_id + "/" + head.fingerprint
    assert sources._physical_cut_head(replace(cut, tenant_frontier=2), history) != head
    assert history.digest == h1_effects_history_digest("tenant", "owner-head", (history_member,))
