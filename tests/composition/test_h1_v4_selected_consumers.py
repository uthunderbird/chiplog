"""V4 selected consumers retain the V3 physical wire without widening authority."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from types import SimpleNamespace

import pytest

import chiplog.composition.h1_producer_history as producer_history
import chiplog.composition.scoped_delivery_selected_sources as selected_sources
from chiplog.adapters.driven.effects_queries import StoredEffectRow
from chiplog.capabilities.effects.scoped_intent_record_contracts import make_scoped_intent_member
from chiplog.composition.h1_completion_issuance import (
    H1CompletionIssuanceV3,
    H1CompletionIssuanceV4,
)
from chiplog.composition.h1_historical_selected_sources import H1HistoricalSelection
from chiplog.composition.h1_scoped_authorization_historical_binder import (
    H1ScopedAuthorizationHistoricalBinder,
    ScopedAuthorizationHistoricalBindHold,
)
from chiplog.composition.h1_scoped_authorization_issuance_v3 import ScopedAuthorizationIssuanceV3
from chiplog.composition.r16_effects import MaterializedEffectsCut
from chiplog.platform._owner_publication_contracts import CompleteDeliveryBatchV2, OwnerRecordBytes
from chiplog.platform.owner_decision_journal import OwnerJournalSnapshot
from chiplog.platform.owner_publications import PreparedOwnerPublication, SelectedOwnerDecision
from tests.support.scoped_intent_records import delivery_publication_fixture


def _selected(
    record: OwnerRecordBytes, *, sequence: int = 1
) -> tuple[MaterializedEffectsCut, OwnerJournalSnapshot, SelectedOwnerDecision]:
    batch = CompleteDeliveryBatchV2.model_construct(
        identity=SimpleNamespace(command_id=f"selected-v4-{sequence}"), complete_records=(record,)
    )
    decision = SelectedOwnerDecision(
        prepared=PreparedOwnerPublication(
            request=batch,
            issuance_id=f"issuance-{sequence}",
            fence_generation="generation",
            fence_frontier=sequence - 1,
            predecessor_commitment="0" * 64,
        ),
        decision_id=f"decision-{sequence}",
        decision_head=f"decision-{sequence}",
        decision_fingerprint="1" * 64,
        resulting_commitment="2" * 64,
        tenant_commit_sequence=sequence,
    )
    cut = MaterializedEffectsCut(
        tenant_id="tenant",
        tenant_frontier=sequence,
        materialization_commitment="3" * 64,
        owner_journal_head="journal-head",
        physical_path="/structural",
        physical_device=0,
        physical_inode=0,
        rows=(StoredEffectRow(sequence, 0, (record.record_id,), record),),
        worker=None,
        latest_runs=(),
    )
    return cut, OwnerJournalSnapshot("tenant", "journal-head", (decision,), frozenset()), decision


def _record(member: object) -> OwnerRecordBytes:
    return OwnerRecordBytes(
        owner="effects",
        record_kind=member.record_kind,
        record_id=member.record_id,
        schema_id=member.schema_id,
        canonical_bytes=member.canonical_record_bytes,
        fingerprint=member.fingerprint,
    )


@pytest.mark.asyncio
async def test_v4_producer_history_binds_exact_physical_member_and_absence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = await delivery_publication_fixture()
    member = make_scoped_intent_member(fixture.intent)
    cut, journal, decision = _selected(_record(member))
    issuance = H1CompletionIssuanceV4.model_construct(
        effects_result=SimpleNamespace(intent_result=fixture.result)
    )
    import chiplog.composition.h1_completion_issuance as issuance_module
    import chiplog.composition.h1_historical_selected_sources as historical

    monkeypatch.setattr(producer_history, "verify_dispatch_history", lambda *_: None)
    monkeypatch.setattr(issuance_module, "decode_h1_completion_issuance", lambda _: issuance)
    monkeypatch.setattr(
        historical,
        "bind_selected_h1_completion",
        lambda *_: H1HistoricalSelection(decision, issuance),
    )

    history = producer_history.interpret_h1_producer_effects_history(cut, journal, runtime=object())

    assert history.ordered_members[0].canonical_record_bytes == member.canonical_record_bytes
    with pytest.raises(producer_history.H1ProducerHistoryError, match="already present"):
        history.require_intent_absent(fixture.intent.intent_id)


@pytest.mark.asyncio
async def test_v4_selected_reader_returns_only_absent_first_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = await delivery_publication_fixture()
    member = make_scoped_intent_member(fixture.intent)
    cut, journal, decision = _selected(_record(member))
    issuance = H1CompletionIssuanceV4.model_construct(
        effects_result=SimpleNamespace(intent_result=fixture.result)
    )

    class Gate:
        def hold(self) -> Gate:
            return self

        def __enter__(self) -> None:
            return None

        def __exit__(self, *_: object) -> None:
            return None

        def require_held(self) -> None:
            return None

    class Runtime:
        def _authority_gate(self) -> Gate:
            return Gate()

        def _owner_decisions(self) -> object:
            return object()

    monkeypatch.setattr(selected_sources, "CommonCliExecutionRuntime", Runtime)
    monkeypatch.setattr(
        selected_sources, "_read_materialized_effects_with_history", lambda *_: (cut, journal)
    )
    monkeypatch.setattr(selected_sources, "verify_dispatch_history", lambda *_: None)
    monkeypatch.setattr(
        selected_sources,
        "bind_selected_h1_completion",
        lambda *_: H1HistoricalSelection(decision, issuance),
    )

    result = selected_sources.ScopedDeliverySelectedSources(Runtime()).read(
        intent_id=fixture.intent.intent_id, act_id="future-send"
    )

    assert type(result.selected_h1.issuance) is H1CompletionIssuanceV4
    assert result.first_send.kind == "FIRST_SEND_ABSENT"


@pytest.mark.asyncio
async def test_v4_forged_physical_member_and_cross_version_adoption_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = await delivery_publication_fixture()
    member = make_scoped_intent_member(fixture.intent)
    record = _record(member)
    cut, journal, decision = _selected(record)
    v4 = H1CompletionIssuanceV4.model_construct(
        effects_result=SimpleNamespace(intent_result=fixture.result)
    )
    v3 = H1CompletionIssuanceV3.model_construct(
        effects_result=SimpleNamespace(intent_result=fixture.result)
    )
    import chiplog.composition.h1_completion_issuance as issuance_module
    import chiplog.composition.h1_historical_selected_sources as historical

    monkeypatch.setattr(producer_history, "verify_dispatch_history", lambda *_: None)
    monkeypatch.setattr(issuance_module, "decode_h1_completion_issuance", lambda _: v4)
    monkeypatch.setattr(
        historical, "bind_selected_h1_completion", lambda *_: H1HistoricalSelection(decision, v4)
    )
    forged = record.model_copy(update={"fingerprint": hashlib.sha256(b"forged").hexdigest()})
    forged_cut = replace(cut, rows=(StoredEffectRow(1, 0, (record.record_id,), forged),))
    with pytest.raises(producer_history.H1ProducerHistoryError, match=r"malformed|substituted"):
        producer_history.interpret_h1_producer_effects_history(
            forged_cut, journal, runtime=object()
        )

    second_batch = CompleteDeliveryBatchV2.model_construct(
        identity=SimpleNamespace(command_id="selected-v3-adoption"), complete_records=(record,)
    )
    second = SelectedOwnerDecision(
        prepared=PreparedOwnerPublication(
            request=second_batch,
            issuance_id="issuance-v3",
            fence_generation="generation",
            fence_frontier=1,
            predecessor_commitment="0" * 64,
        ),
        decision_id="decision-v3",
        decision_head="decision-v3",
        decision_fingerprint="4" * 64,
        resulting_commitment="5" * 64,
        tenant_commit_sequence=2,
    )
    mixed = OwnerJournalSnapshot("tenant", "journal-head", (decision, second), frozenset())
    with pytest.raises(producer_history.H1ProducerHistoryError, match="duplicate effect record"):
        producer_history.interpret_h1_producer_effects_history(cut, mixed, runtime=object())
    assert type(v3) is H1CompletionIssuanceV3


def test_v4_authorization_predecessor_stays_held_for_seven_independent_roles() -> None:
    issuance = ScopedAuthorizationIssuanceV3.model_construct()

    with pytest.raises(
        ScopedAuthorizationHistoricalBindHold,
        match=(
            "endpoint, credential_lifecycle, deployment_entitlement, clock, effects_history, "
            "normative_conflict_generation, runtime_and_fence"
        ),
    ):
        H1ScopedAuthorizationHistoricalBinder._require_independent_inventory_sources(issuance, "V4")
