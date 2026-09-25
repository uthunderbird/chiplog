"""Focused contracts for raw owner-journal H1 inventory reconciliation."""

from __future__ import annotations

from pathlib import Path

import pytest

import chiplog.composition.h1_inventory_owner_cut as owner_cut
from chiplog.composition.h1_inventory_owner_cut import (
    OWNER_BATCH_KINDS,
    H1OwnerCutFailure,
    OwnerJournalSqlPublication,
    OwnerJournalSqlRecord,
    h1_owner_batch_registry,
    reconcile_h1_historical_owner_cut,
    reconcile_h1_owner_cut,
)
from chiplog.composition.h1_preseal_contracts import H1OwnerAsOfV1, H1SelectedSeal
from chiplog.platform._sqlite import PhysicalPublicationCommand
from chiplog.platform.owner_decision_journal import IndependentOwnerDecisionJournal
from chiplog.platform.owner_publications import PreparedOwnerPublication
from tests.platform.test_owner_publications import digest, request

type _Fixture = tuple[
    tuple[tuple[str, str | None, bytes], ...],
    tuple[OwnerJournalSqlPublication, ...],
    tuple[OwnerJournalSqlRecord, ...],
]


def _fixture(tmp_path: Path, *, materialized: bool) -> _Fixture:
    from chiplog.adapters.driven.deployment_trust import IndependentTenantDecisionJournal

    raw = IndependentTenantDecisionJournal(tmp_path / "owner.journal")
    journal = IndependentOwnerDecisionJournal(raw, "tenant")
    batch = request(digest(b"before"))
    decision = journal.select(
        PreparedOwnerPublication(batch, "issued", "fence", 0, digest(b"before")), digest(b"after")
    )
    if materialized:
        journal.materialized(decision)
    records = tuple(
        OwnerJournalSqlRecord(
            row.record_id,
            row.owner,
            row.schema_id,
            row.canonical_bytes,
            decision.tenant_commit_sequence,
        )
        for row in batch.complete_records
    )
    publications = (
        (
            OwnerJournalSqlPublication(
                batch.operation,
                batch.identity.command_id,
                batch.identity.command_fingerprint,
                decision.tenant_commit_sequence,
                tuple(row.record_id for row in batch.complete_records),
            ),
        )
        if materialized
        else ()
    )
    return raw.entries(), publications, records if materialized else ()


def _historical_fixture(
    tmp_path: Path,
) -> tuple[
    IndependentOwnerDecisionJournal,
    H1OwnerAsOfV1,
    tuple[OwnerJournalSqlPublication, ...],
    tuple[OwnerJournalSqlRecord, ...],
]:
    from chiplog.adapters.driven.deployment_trust import IndependentTenantDecisionJournal

    raw = IndependentTenantDecisionJournal(tmp_path / "owner.journal")
    journal = IndependentOwnerDecisionJournal(raw, "tenant")
    batch = request(digest(b"before"))
    decision = journal.select(
        PreparedOwnerPublication(batch, "issued", "fence", 0, digest(b"before")), digest(b"after")
    )
    journal.materialized(decision)
    head = journal.snapshot().head
    assert head is not None
    publications = (
        OwnerJournalSqlPublication(
            batch.operation,
            batch.identity.command_id,
            batch.identity.command_fingerprint,
            decision.tenant_commit_sequence,
            tuple(row.record_id for row in batch.complete_records),
        ),
    )
    records = tuple(
        OwnerJournalSqlRecord(
            row.record_id,
            row.owner,
            row.schema_id,
            row.canonical_bytes,
            decision.tenant_commit_sequence,
        )
        for row in batch.complete_records
    )
    return journal, H1OwnerAsOfV1(tenant_id="tenant", owner_head=head), publications, records


def _selected_seal(sequence: int) -> H1SelectedSeal:
    return H1SelectedSeal(
        PhysicalPublicationCommand(
            "tenant", "native", "seal", digest(b"seal"), 0, "fence", 0, 0, ()
        ),
        "loop-decision",
        b"{}",
        sequence,
    )


def _seal_publication(sequence: int) -> OwnerJournalSqlPublication:
    return OwnerJournalSqlPublication("native", "seal", digest(b"seal"), sequence, ())


def test_registry_covers_every_registered_owner_envelope_variant() -> None:
    assert (
        h1_owner_batch_registry()
        == OWNER_BATCH_KINDS
        == {
            "SINGLE_OWNER",
            "PLAN_EFFECT_ATOMIC",
            "CALL_EFFECT_ATOMIC",
            "COMPLETE_DELIVERY_ATOMIC",
            "COMPLETE_DELIVERY_ATOMIC_V2",
            "REJECTED_COMPLETION_ATOMIC_V1",
        }
    )


def test_current_cut_reconciles_exact_selected_members_and_pending(tmp_path: Path) -> None:
    entries, publications, records = _fixture(tmp_path, materialized=True)
    cut = reconcile_h1_owner_cut(
        tenant="tenant",
        raw_entries=entries,
        publications=publications,
        records=records,
        phase="PRE_SEAL",
    )
    assert cut.materialized_command_ids == {"command"}
    assert cut.decisions[0].materialized
    assert cut.decisions[0].member_bytes == (b"record1", b"record2")

    entries, publications, records = _fixture(tmp_path / "pending", materialized=False)
    pending = reconcile_h1_owner_cut(
        tenant="tenant",
        raw_entries=entries,
        publications=publications,
        records=records,
        phase="PRE_SEAL",
    )
    assert not pending.decisions[0].materialized


def test_selected_marker_without_exact_sql_publication_is_corrupt(tmp_path: Path) -> None:
    entries, _, _ = _fixture(tmp_path, materialized=True)
    with pytest.raises(H1OwnerCutFailure, match="missing-publication") as raised:
        reconcile_h1_owner_cut(
            tenant="tenant", raw_entries=entries, publications=(), records=(), phase="PRE_SEAL"
        )
    assert raised.value.code == "CORRUPT"


def test_selected_member_bytes_must_match_its_sql_row(tmp_path: Path) -> None:
    entries, publications, records = _fixture(tmp_path, materialized=True)
    changed = (
        OwnerJournalSqlRecord(
            records[0].record_id,
            records[0].owner,
            records[0].schema,
            b"changed",
            records[0].commit_sequence,
        ),
        *records[1:],
    )
    with pytest.raises(H1OwnerCutFailure, match="member/record1") as raised:
        reconcile_h1_owner_cut(
            tenant="tenant",
            raw_entries=entries,
            publications=publications,
            records=changed,
            phase="POST_SEAL",
        )
    assert raised.value.code == "CORRUPT"


def test_historical_owner_cut_is_explicitly_unsupported_after_authentication(
    tmp_path: Path,
) -> None:
    entries, publications, records = _fixture(tmp_path, materialized=True)
    with pytest.raises(H1OwnerCutFailure, match="HISTORICAL_OWNER") as raised:
        reconcile_h1_owner_cut(
            tenant="tenant",
            raw_entries=entries,
            publications=publications,
            records=records,
            phase="HISTORICAL",
        )
    assert raised.value.code == "UNSUPPORTED"


def test_historical_cut_uses_only_the_located_prefix_and_requires_preseal_sql(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal, locator, publications, records = _historical_fixture(tmp_path)
    monkeypatch.setattr(owner_cut, "_parse_h1_owner_asof", lambda _seal, _tenant: locator)

    full_publications = (*publications, _seal_publication(2))
    cut = reconcile_h1_historical_owner_cut(
        tenant="tenant",
        owner_journal=journal,
        selected_seal=_selected_seal(2),
        publications=full_publications,
        records=records,
        known_non_owner_operations=frozenset(),
    )
    assert cut.materialized_command_ids == {"command"}
    with pytest.raises(H1OwnerCutFailure, match="beyond-selected-seal"):
        reconcile_h1_historical_owner_cut(
            tenant="tenant",
            owner_journal=journal,
            selected_seal=_selected_seal(1),
            publications=full_publications,
            records=records,
            known_non_owner_operations=frozenset(),
        )


def test_historical_prefix_selected_without_marker_is_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chiplog.adapters.driven.deployment_trust import IndependentTenantDecisionJournal

    journal = IndependentOwnerDecisionJournal(
        IndependentTenantDecisionJournal(tmp_path / "owner.journal"), "tenant"
    )
    batch = request(digest(b"before"))
    journal.select(
        PreparedOwnerPublication(batch, "issued", "fence", 0, digest(b"before")), digest(b"after")
    )
    head = journal.snapshot().head
    assert head is not None
    monkeypatch.setattr(
        owner_cut,
        "_parse_h1_owner_asof",
        lambda _seal, _tenant: H1OwnerAsOfV1(tenant_id="tenant", owner_head=head),
    )
    with pytest.raises(H1OwnerCutFailure, match="owner-prefix/pending") as raised:
        reconcile_h1_historical_owner_cut(
            tenant="tenant",
            owner_journal=journal,
            selected_seal=_selected_seal(2),
            publications=(_seal_publication(2),),
            records=(),
            known_non_owner_operations=frozenset(),
        )
    assert raised.value.code == "INCOMPLETE"


def test_historical_locator_is_strictly_decoded_from_the_selected_v2_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    locator = H1OwnerAsOfV1(tenant_id="tenant", owner_head=None)

    class _Retained:
        def canonical_bytes(self) -> bytes:
            return b"retained-v2"

    class _V2Decoder:
        @staticmethod
        def model_validate_json(raw: str) -> _Retained:
            assert raw == "retained-v2"
            return _Retained()

    monkeypatch.setattr(owner_cut, "RetainedExecutionCompleteSealV2", _V2Decoder)
    selected = _selected_seal(1)
    decision = (
        b'{"execution_complete_seal":"retained-v2","fingerprint":"'
        + digest(b"seal").encode()
        + b'","expected_head":0,"h1_owner_asof":'
        + locator.canonical_bytes()
        + b',"kind":"DECIDED","operation_id":"seal","operation_kind":"native","version":1}'
    )
    selected = selected.__class__(
        selected.command, selected.decision_id, decision, selected.commit_sequence
    )
    assert owner_cut._parse_h1_owner_asof(selected, "tenant") == locator

    duplicate = selected.__class__(
        selected.command,
        selected.decision_id,
        decision.replace(b'"h1_owner_asof":', b'"h1_owner_asof":null,"h1_owner_asof":', 1),
        selected.commit_sequence,
    )
    with pytest.raises(H1OwnerCutFailure, match="selected-loop-decision"):
        owner_cut._parse_h1_owner_asof(duplicate, "tenant")
