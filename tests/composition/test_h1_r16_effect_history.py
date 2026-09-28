"""Structural wiring checks for H1 local records in the R16 dispatch reader.

These use decoder seams deliberately: they establish only that the dispatch
reader passes the exact selected physical row and batch to the closed codecs.
They do not claim a mounted H1 acceptance path; that retained-fixture check is
owned by the integration suite.
"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from typing import Any

import pytest

import chiplog.composition.r16_dispatch_history as dispatch_history
from chiplog.adapters.driven.effects_queries import StoredEffectRow
from chiplog.capabilities.effects.h1_local_preparation_record_contracts import (
    H1LocalPreparedCommentaryCanonicalMemberV1,
)
from chiplog.composition.r16_effects import MaterializedEffectsCut
from chiplog.platform._owner_publication_contracts import (
    CompleteDeliveryBatchV2,
    OwnerRecordBytes,
)
from chiplog.platform.owner_decision_journal import OwnerJournalSnapshot
from chiplog.platform.owner_publications import (
    PreparedOwnerPublication,
    SelectedOwnerDecision,
)

H1_SCHEMA = "chiplog.effects.h1-local-prepared-commentary-intent.v1"


def _h1_record(*, record_kind: str = "H1LocalPreparedCommentaryIntent") -> OwnerRecordBytes:
    raw = b'{"structural":"h1-local-member"}'
    return OwnerRecordBytes(
        owner="effects",
        record_kind=record_kind,
        record_id="h1-local-intent",
        schema_id=H1_SCHEMA,
        canonical_bytes=raw,
        fingerprint=hashlib.sha256(raw).hexdigest(),
    )


def _batch(record: OwnerRecordBytes) -> CompleteDeliveryBatchV2:
    return CompleteDeliveryBatchV2.model_construct(
        identity=SimpleNamespace(command_id="h1-command"),
        expected=SimpleNamespace(model_dump=lambda *, mode: {"tenant_id": "tenant"}),
        complete_records=(record,),
    )


def _history(
    record: OwnerRecordBytes,
    *,
    rows: tuple[OwnerRecordBytes, ...] | None = None,
    batch: object | None = None,
) -> tuple[MaterializedEffectsCut, OwnerJournalSnapshot]:
    selected_batch = _batch(record) if batch is None else batch
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
    selected_rows = (record,) if rows is None else rows
    cut = MaterializedEffectsCut(
        tenant_id="tenant",
        tenant_frontier=1,
        materialization_commitment="3" * 64,
        owner_journal_head="journal-head",
        physical_path="/structural",
        physical_device=0,
        physical_inode=0,
        rows=tuple(
            StoredEffectRow(1, ordinal, tuple(item.record_id for item in selected_rows), item)
            for ordinal, item in enumerate(selected_rows)
        ),
        worker=None,
        latest_runs=(),
    )
    return cut, OwnerJournalSnapshot("tenant", "journal-head", (decision,), frozenset())


def test_structural_h1_local_row_is_counted_but_not_projected_as_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Decoder seams prove only exact branch wiring, never mounted acceptance."""
    record = _h1_record()
    cut, journal = _history(record)
    decoded_members: list[H1LocalPreparedCommentaryCanonicalMemberV1] = []
    decoded_batches: list[CompleteDeliveryBatchV2] = []

    def decode_member(
        member: H1LocalPreparedCommentaryCanonicalMemberV1,
    ) -> object:
        decoded_members.append(member)
        return object()

    def decode_batch(batch: CompleteDeliveryBatchV2) -> object:
        decoded_batches.append(batch)
        return object()

    import chiplog.capabilities.effects.h1_local_preparation_record_contracts as records
    import chiplog.composition.h1_completion_issuance as issuance

    monkeypatch.setattr(records, "decode_h1_local_prepared_commentary_member", decode_member)
    monkeypatch.setattr(issuance, "decode_h1_completion_issuance", decode_batch)

    verified = dispatch_history.verify_dispatch_history(cut, journal)

    assert verified.members == ()
    assert verified.v2_records == ()
    assert decoded_members == [
        H1LocalPreparedCommentaryCanonicalMemberV1(
            record_id=record.record_id,
            canonical_record_bytes=record.canonical_bytes,
            fingerprint=record.fingerprint,
        )
    ]
    assert decoded_batches == [journal.decisions[0].prepared.request]


def test_h1_local_row_rejects_malformed_physical_member_before_any_decoder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _h1_record(record_kind="not-h1-local")
    cut, journal = _history(record)
    calls: list[object] = []

    import chiplog.capabilities.effects.h1_local_preparation_record_contracts as records
    import chiplog.composition.h1_completion_issuance as issuance

    monkeypatch.setattr(
        records,
        "decode_h1_local_prepared_commentary_member",
        lambda member: calls.append(member),
    )
    monkeypatch.setattr(
        issuance,
        "decode_h1_completion_issuance",
        lambda batch: calls.append(batch),
    )

    with pytest.raises(ValueError):
        dispatch_history.verify_dispatch_history(cut, journal)
    assert calls == []


def test_h1_local_row_with_another_owner_fails_before_any_decoder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = _h1_record()
    foreign = selected.model_copy(update={"owner": "planning"})
    cut, journal = _history(selected, rows=(foreign,))
    calls: list[object] = []

    import chiplog.capabilities.effects.h1_local_preparation_record_contracts as records
    import chiplog.composition.h1_completion_issuance as issuance

    monkeypatch.setattr(
        records,
        "decode_h1_local_prepared_commentary_member",
        lambda member: calls.append(member),
    )
    monkeypatch.setattr(
        issuance,
        "decode_h1_completion_issuance",
        lambda batch: calls.append(batch),
    )

    with pytest.raises(ValueError, match="canonical selected bytes substituted"):
        dispatch_history.verify_dispatch_history(cut, journal)
    assert calls == []


def test_h1_local_row_obeys_universal_absent_membership_check() -> None:
    record = _h1_record()
    cut, journal = _history(record, rows=())

    with pytest.raises(ValueError, match="omits selected members"):
        dispatch_history.verify_dispatch_history(cut, journal)


def test_h1_local_row_obeys_universal_duplicate_membership_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _h1_record()
    cut, journal = _history(record, rows=(record, record))

    import chiplog.capabilities.effects.h1_local_preparation_record_contracts as records
    import chiplog.composition.h1_completion_issuance as issuance

    monkeypatch.setattr(
        records, "decode_h1_local_prepared_commentary_member", lambda member: member
    )
    monkeypatch.setattr(issuance, "decode_h1_completion_issuance", lambda batch: batch)

    with pytest.raises(ValueError, match="omitted, duplicated or outside"):
        dispatch_history.verify_dispatch_history(cut, journal)


def test_h1_local_row_rejects_another_publication_envelope() -> None:
    record = _h1_record()
    non_h1_batch: Any = SimpleNamespace(
        identity=SimpleNamespace(command_id="h1-command"),
        expected=SimpleNamespace(model_dump=lambda *, mode: {"tenant_id": "tenant"}),
        complete_records=(record,),
    )
    cut, journal = _history(record, batch=non_h1_batch)

    with pytest.raises(ValueError, match="H1 local"):
        dispatch_history.verify_dispatch_history(cut, journal)


def test_unknown_effect_history_schema_remains_rejected() -> None:
    record = _h1_record().model_copy(update={"schema_id": "chiplog.effects.other.v1"})
    cut, journal = _history(record)

    with pytest.raises(ValueError, match="unregistered effects history schema"):
        dispatch_history.verify_dispatch_history(cut, journal)
