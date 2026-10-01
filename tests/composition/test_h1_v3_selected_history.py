"""Selected H1 scoped-V3 effects history is readable only as its registered member."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from chiplog.adapters.driven.effects_queries import StoredEffectRow
from chiplog.capabilities.effects.scoped_intent_record_contracts import make_scoped_intent_member
from chiplog.composition import r16_dispatch_history
from chiplog.composition.h1_completion_issuance import H1CompletionIssuanceV3
from chiplog.composition.h1_producer_history import (
    H1ProducerHistoryHold,
    interpret_h1_producer_effects_history,
)
from chiplog.composition.r16_dispatch_history import verify_dispatch_history
from chiplog.composition.r16_effects import MaterializedEffectsCut
from chiplog.platform._owner_publication_contracts import CompleteDeliveryBatchV2, OwnerRecordBytes
from chiplog.platform.owner_decision_journal import OwnerJournalSnapshot
from chiplog.platform.owner_publications import PreparedOwnerPublication, SelectedOwnerDecision
from tests.support.scoped_intent_records import delivery_publication_fixture


def _history(
    record: OwnerRecordBytes, *, batch: object | None = None
) -> tuple[MaterializedEffectsCut, OwnerJournalSnapshot]:
    selected_batch = batch or SimpleNamespace(
        identity=SimpleNamespace(command_id="scoped-v3-command"),
        expected=SimpleNamespace(model_dump=lambda *, mode: {"tenant_id": "tenant"}),
        complete_records=(record,),
    )
    decision = SelectedOwnerDecision(
        prepared=PreparedOwnerPublication(
            request=selected_batch,  # type: ignore[arg-type]
            issuance_id="issuance",
            fence_generation="generation",
            fence_frontier=0,
            predecessor_commitment="0" * 64,
        ),
        decision_id="decision",
        decision_head="decision/head",
        decision_fingerprint="1" * 64,
        resulting_commitment="2" * 64,
        tenant_commit_sequence=1,
    )
    cut = MaterializedEffectsCut(
        tenant_id="tenant",
        tenant_frontier=1,
        materialization_commitment="3" * 64,
        owner_journal_head="journal-head",
        physical_path="/structural",
        physical_device=0,
        physical_inode=0,
        rows=(StoredEffectRow(1, 0, (record.record_id,), record),),
        worker=None,
        latest_runs=(),
    )
    return cut, OwnerJournalSnapshot("tenant", "journal-head", (decision,), frozenset())


@pytest.mark.asyncio
async def test_registered_scoped_v3_member_survives_r16_and_h1_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = await delivery_publication_fixture()
    member = make_scoped_intent_member(fixture.intent)
    record = OwnerRecordBytes(
        owner="effects",
        record_kind=member.record_kind,
        record_id=member.record_id,
        schema_id=member.schema_id,
        canonical_bytes=member.canonical_record_bytes,
        fingerprint=member.fingerprint,
    )
    batch = CompleteDeliveryBatchV2.model_construct(
        identity=SimpleNamespace(command_id="scoped-v3-command"),
        authentication=SimpleNamespace(applicability_schema="scoped-v3"),
        expected=SimpleNamespace(model_dump=lambda *, mode: {"tenant_id": "tenant"}),
        complete_records=(record,),
    )
    issuance = H1CompletionIssuanceV3.model_construct(
        effects_result=SimpleNamespace(intent_result=fixture.result)
    )
    import chiplog.composition.h1_completion_issuance as completion_issuance
    import chiplog.composition.h1_historical_selected_sources as selected_sources

    monkeypatch.setattr(completion_issuance, "decode_h1_completion_issuance", lambda _: issuance)
    cut, journal = _history(record, batch=batch)
    decision = journal.decisions[0]
    runtime = object()
    monkeypatch.setattr(
        selected_sources,
        "bind_selected_h1_completion",
        lambda *_: SimpleNamespace(decision=decision, issuance=issuance),
    )

    assert verify_dispatch_history(cut, journal).members == ()
    history = interpret_h1_producer_effects_history(cut, journal, runtime=runtime)
    assert history.ordered_members[0].intent_id == fixture.intent.intent_id


@pytest.mark.asyncio
async def test_scoped_v3_member_rejects_tampered_canonical_bytes() -> None:
    fixture = await delivery_publication_fixture()
    member = make_scoped_intent_member(fixture.intent)
    bad = OwnerRecordBytes(
        owner="effects",
        record_kind=member.record_kind,
        record_id=member.record_id,
        schema_id=member.schema_id,
        canonical_bytes=member.canonical_record_bytes + b" ",
        fingerprint=hashlib.sha256(member.canonical_record_bytes + b" ").hexdigest(),
    )
    cut, journal = _history(bad)

    with pytest.raises(ValueError, match="canonical"):
        verify_dispatch_history(cut, journal)


@pytest.mark.asyncio
async def test_r16_startup_binds_scoped_v3_member_to_selected_issuance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = await delivery_publication_fixture()
    member = make_scoped_intent_member(fixture.intent)
    record = OwnerRecordBytes(
        owner="effects",
        record_kind=member.record_kind,
        record_id=member.record_id,
        schema_id=member.schema_id,
        canonical_bytes=member.canonical_record_bytes,
        fingerprint=member.fingerprint,
    )
    batch = CompleteDeliveryBatchV2.model_construct(
        identity=SimpleNamespace(command_id="scoped-v3-command"),
        authentication=SimpleNamespace(applicability_schema="scoped-v3"),
        complete_records=(record,),
    )
    decision = SelectedOwnerDecision(
        prepared=PreparedOwnerPublication(
            request=batch,
            issuance_id="issuance",
            fence_generation="generation",
            fence_frontier=0,
            predecessor_commitment="0" * 64,
        ),
        decision_id="decision",
        decision_head="decision/head",
        decision_fingerprint="1" * 64,
        resulting_commitment="2" * 64,
        tenant_commit_sequence=1,
    )
    issuance = H1CompletionIssuanceV3.model_construct(
        effects_result=SimpleNamespace(intent_result=fixture.result)
    )
    journal = OwnerJournalSnapshot("tenant", "journal-head", (decision,), frozenset())
    runtime = SimpleNamespace(
        _owner_decisions=lambda: SimpleNamespace(snapshot=lambda: journal),
        _tenant_id="tenant",
        _commitment_journal=SimpleNamespace(load=lambda _tenant: None),
        _database="/structural",
    )
    import chiplog.composition.h1_completion_issuance as completion_issuance
    import chiplog.composition.h1_historical_selected_sources as selected_sources

    monkeypatch.setattr(completion_issuance, "decode_h1_completion_issuance", lambda _: issuance)
    monkeypatch.setattr(
        selected_sources,
        "bind_selected_h1_completion",
        lambda *_: SimpleNamespace(decision=decision, issuance=issuance),
    )
    monkeypatch.setattr(r16_dispatch_history, "verify_dispatch_history", lambda *_: None)

    r16_dispatch_history.validate_selected_dispatch_sources(runtime)

    changed = record.model_copy(update={"canonical_bytes": record.canonical_bytes + b" "})
    object.__setattr__(batch, "complete_records", (changed,))
    with pytest.raises(ValueError, match="differs from issuance"):
        r16_dispatch_history.validate_selected_dispatch_sources(runtime)


def test_unknown_v3_effect_member_remains_held() -> None:
    record = OwnerRecordBytes(
        owner="effects",
        record_kind="Other",
        record_id="other",
        schema_id="chiplog.effects.other.v3",
        canonical_bytes=b"{}",
        fingerprint=hashlib.sha256(b"{}").hexdigest(),
    )
    cut, journal = _history(record)

    with pytest.raises(H1ProducerHistoryHold, match="on hold"):
        interpret_h1_producer_effects_history(cut, journal)
