"""Anchored historical owner snapshots reuse only an exact raw image."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from chiplog.composition.r14_runtime import AnchoredOwnerDecisionJournal
from chiplog.platform.authority_gate import AuthorityGate
from chiplog.platform.owner_decision_journal import OwnerJournalIntegrityError


def test_snapshot_at_reuses_exact_head_but_rechecks_a_changed_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "snapshot-at.sqlite"
    database.touch()
    gate = AuthorityGate.for_database(database)
    runtime = SimpleNamespace(
        _database=database,
        _tenant_id="tenant",
        _authority_gate=lambda: gate,
    )
    journal = AnchoredOwnerDecisionJournal(runtime)
    scans = 0
    scan = journal._scan_entries

    def counted_scan(entries, operation, record, capture_head=None):
        nonlocal scans
        scans += 1
        return scan(entries, operation, record, capture_head)

    monkeypatch.setattr(journal, "_scan_entries", counted_scan)

    first = journal.snapshot_at(None)
    second = journal.snapshot_at(None)

    assert first == second
    assert first.head is None
    assert scans == 1
    with pytest.raises(OwnerJournalIntegrityError, match="snapshot_at"):
        journal.snapshot_at("absent")
    assert scans == 2

    journal._raw.append(b"{}", None)
    with pytest.raises(OwnerJournalIntegrityError, match="snapshot_at"):
        journal.snapshot_at(None)
    assert scans == 3
