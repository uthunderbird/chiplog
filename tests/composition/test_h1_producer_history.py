"""Pure completeness checks for the H1 producer effects-history interpreter."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

import chiplog.composition.h1_producer_history as history
from chiplog.adapters.driven.effects_queries import StoredEffectRow
from chiplog.capabilities.effects.h1_local_preparation_record_contracts import (
    H1LocalPreparedCommentaryCanonicalMemberV1,
)
from chiplog.composition.r16_effects import MaterializedEffectsCut
from chiplog.platform._owner_publication_contracts import CompleteDeliveryBatchV2, OwnerRecordBytes
from chiplog.platform.owner_decision_journal import OwnerJournalSnapshot
from chiplog.platform.owner_publications import (
    PreparedOwnerPublication,
    SelectedOwnerDecision,
)
from tests.composition.test_effects_registry import record as legacy_record
from tests.support.acceptance_v2 import prepared_acceptance


def _owner_record(
    *, record_id: str, record_kind: str, schema_id: str, raw: bytes
) -> OwnerRecordBytes:
    return OwnerRecordBytes(
        owner="effects",
        record_kind=record_kind,
        record_id=record_id,
        schema_id=schema_id,
        canonical_bytes=raw,
        fingerprint=hashlib.sha256(raw).hexdigest(),
    )


def _legacy(name: str = "old") -> OwnerRecordBytes:
    decoded = legacy_record("INTENT_RECORDED", name)
    return _owner_record(
        record_id=decoded.record.head,
        record_kind="effects." + decoded.kind,
        schema_id="chiplog.effects.record.v1",
        raw=decoded.canonical_bytes(),
    )


def _dispatch() -> OwnerRecordBytes:
    decoded = prepared_acceptance().effects_proposal
    return _owner_record(
        record_id=decoded.record.head,
        record_kind="effects." + decoded.kind,
        schema_id="chiplog.effects.dispatch-record.v2",
        raw=decoded.canonical_bytes(),
    )


def _local() -> OwnerRecordBytes:
    raw = b'{"structural":"h1-local"}'
    member = H1LocalPreparedCommentaryCanonicalMemberV1(
        record_id="h1-local-intent",
        canonical_record_bytes=raw,
        fingerprint=hashlib.sha256(raw).hexdigest(),
    )
    return _owner_record(
        record_id=member.record_id,
        record_kind=member.record_kind,
        schema_id=member.schema_id,
        raw=member.canonical_record_bytes,
    )


def _history(
    records: tuple[OwnerRecordBytes, ...], *, rows: tuple[OwnerRecordBytes, ...] | None = None
) -> tuple[MaterializedEffectsCut, OwnerJournalSnapshot]:
    batch: object
    if any(item.schema_id == history.H1_LOCAL_PREPARED_COMMENTARY_SCHEMA for item in records):
        batch = CompleteDeliveryBatchV2.model_construct(
            identity=SimpleNamespace(command_id="command"),
            expected=SimpleNamespace(),
            complete_records=records,
        )
    else:
        batch = SimpleNamespace(complete_records=records)
    decision = SelectedOwnerDecision(
        prepared=PreparedOwnerPublication(
            request=batch,  # type: ignore[arg-type]
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
    actual = records if rows is None else rows
    cut = MaterializedEffectsCut(
        tenant_id="tenant",
        tenant_frontier=1,
        materialization_commitment="3" * 64,
        owner_journal_head="journal-head",
        physical_path="/structural",
        physical_device=0,
        physical_inode=0,
        rows=tuple(
            StoredEffectRow(1, ordinal, tuple(item.record_id for item in actual), item)
            for ordinal, item in enumerate(actual)
        ),
        worker=None,
        latest_runs=(),
    )
    return cut, OwnerJournalSnapshot("tenant", "journal-head", (decision,), frozenset())


@pytest.fixture(autouse=True)
def _semantic_verifier(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ledger tests isolate the existing verifier; its own suite owns semantics."""
    monkeypatch.setattr(history, "verify_dispatch_history", lambda cut, journal: object())


def test_local_member_is_kept_in_complete_raw_ledger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local = _local()
    cut, journal = _history((local,))
    monkeypatch.setattr(
        history,
        "decode_h1_local_prepared_commentary_member",
        lambda member: SimpleNamespace(intent_id=member.record_id),
    )

    interpreted = history.interpret_h1_producer_effects_history(cut, journal)

    assert [member.record_id for member in interpreted.ordered_members] == [local.record_id]
    assert interpreted.ordered_members[0].intent_id == "h1-local-intent"
    with pytest.raises(history.H1ProducerHistoryError, match="already present"):
        interpreted.require_intent_absent("h1-local-intent")


def test_legacy_and_v2_intent_identities_are_in_the_absence_ledger() -> None:
    legacy = _legacy()
    cut, journal = _history((legacy,))
    interpreted = history.interpret_h1_producer_effects_history(cut, journal)
    with pytest.raises(history.H1ProducerHistoryError, match="already present"):
        interpreted.require_intent_absent("intent")

    dispatch = _dispatch()
    cut, journal = _history((dispatch,))
    interpreted = history.interpret_h1_producer_effects_history(cut, journal)
    with pytest.raises(history.H1ProducerHistoryError, match="already present"):
        interpreted.require_intent_absent("intent")


@pytest.mark.parametrize("schema_id", ["chiplog.effects.external-action-intent.v3"])
def test_selected_v3_is_held_before_the_legacy_verifier(
    monkeypatch: pytest.MonkeyPatch, schema_id: str
) -> None:
    raw = b"v3"
    selected = _owner_record(
        record_id="v3-intent", record_kind="ExternalActionIntent", schema_id=schema_id, raw=raw
    )
    cut, journal = _history((selected,))
    calls: list[object] = []
    monkeypatch.setattr(history, "verify_dispatch_history", lambda *args: calls.append(args))

    with pytest.raises(history.H1ProducerHistoryHold, match="on hold"):
        history.interpret_h1_producer_effects_history(cut, journal)
    assert calls == []


def test_unknown_selected_schema_is_rejected_before_the_legacy_verifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = b"unknown"
    selected = _owner_record(
        record_id="unknown", record_kind="Other", schema_id="chiplog.effects.other.v1", raw=raw
    )
    cut, journal = _history((selected,))
    calls: list[object] = []
    monkeypatch.setattr(history, "verify_dispatch_history", lambda *args: calls.append(args))

    with pytest.raises(history.H1ProducerHistoryError, match="unregistered"):
        history.interpret_h1_producer_effects_history(cut, journal)
    assert calls == []


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        (lambda one, two: (one,), "omits selected"),
        (lambda one, two: (one, one), "duplicated"),
        (lambda one, two: (two, one), "reordered"),
        (lambda one, two: (one, two, _legacy("extra")), "outside selected"),
    ],
)
def test_selected_membership_and_order_are_complete(
    rows: object, message: str
) -> None:
    first, second = _legacy("first"), _legacy("second")
    chosen = rows(first, second)  # type: ignore[operator]
    cut, journal = _history((first, second), rows=chosen)

    with pytest.raises(history.H1ProducerHistoryError, match=message):
        history.interpret_h1_producer_effects_history(cut, journal)


def test_substituted_selected_bytes_and_envelope_fingerprint_are_rejected() -> None:
    selected = _legacy()
    substituted = selected.model_copy(update={"canonical_bytes": selected.canonical_bytes + b" "})
    cut, journal = _history((selected,), rows=(substituted,))
    with pytest.raises(history.H1ProducerHistoryError, match="substituted"):
        history.interpret_h1_producer_effects_history(cut, journal)

    bad_fingerprint = selected.model_copy(update={"fingerprint": "0" * 64})
    cut, journal = _history((bad_fingerprint,))
    with pytest.raises(history.H1ProducerHistoryError, match="fingerprint"):
        history.interpret_h1_producer_effects_history(cut, journal)


def test_digest_binds_selected_envelope_metadata() -> None:
    selected = _legacy()
    cut, journal = _history((selected,))
    original = history.interpret_h1_producer_effects_history(cut, journal)
    changed = OwnerJournalSnapshot(
        journal.tenant_id,
        journal.head,
        (
            SelectedOwnerDecision(
                prepared=journal.decisions[0].prepared,
                decision_id="another-decision",
                decision_head="another/head",
                decision_fingerprint="4" * 64,
                resulting_commitment=journal.decisions[0].resulting_commitment,
                tenant_commit_sequence=journal.decisions[0].tenant_commit_sequence,
            ),
        ),
        journal.materialized_command_ids,
    )
    changed_history = history.interpret_h1_producer_effects_history(cut, changed)
    assert changed_history.digest != original.digest
