"""Historical cuts of the independently authenticated owner journal."""

import hashlib
from pathlib import Path

import pytest

from chiplog.adapters.driven.deployment_trust import IndependentTenantDecisionJournal
from chiplog.platform._owner_publication_contracts import InvocationProofRef, SingleOwnerBatch
from chiplog.platform.owner_decision_journal import (
    IndependentOwnerDecisionJournal,
    OwnerJournalIntegrityError,
)
from chiplog.platform.owner_publications import PreparedOwnerPublication


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _batch(command_id: str, frontier: int, predecessor: str) -> SingleOwnerBatch:
    return SingleOwnerBatch.model_validate(
        {
            "operation": "effects.authorize",
            "identity": {
                "tenant_id": "tenant",
                "command_id": command_id,
                "command_fingerprint": _digest(command_id.encode()),
                "canonicalization_version": "chiplog.owner-publication.v1",
            },
            "authentication": {
                "kind": "WORKER",
                "invocation": InvocationProofRef(
                    issuance_id="issued",
                    issuance_fingerprint=_digest(b"issued"),
                    broker_epoch="epoch",
                    broker_session="session",
                    runtime_generation="generation",
                    operation_subject=command_id,
                ),
                "applicability_schema": "fixture.v1",
                "applicability_bytes": b"fixture",
                "applicability_fingerprint": _digest(b"fixture"),
            },
            "expected": {
                "tenant_id": "tenant",
                "tenant_frontier": frontier,
                "expected_materialization_commitment": predecessor,
                "registry_head": "registry",
                "registry_fingerprint": _digest(b"registry"),
                "ordered_heads": (),
                "complete_manifest_fingerprint": _digest(b"manifest"),
            },
            "command": {
                "owner": "effects",
                "schema_id": "fixture.command.v1",
                "canonical_bytes": command_id.encode(),
                "fingerprint": _digest(command_id.encode()),
            },
            "complete_records": (
                {
                    "owner": "effects",
                    "record_kind": "fixture",
                    "record_id": f"record-{command_id}",
                    "schema_id": "fixture.record.v1",
                    "canonical_bytes": command_id.encode(),
                    "fingerprint": _digest(command_id.encode()),
                },
            ),
            "complete_batch_fingerprint": _digest(b"batch" + command_id.encode()),
        }
    )


def _select(
    journal: IndependentOwnerDecisionJournal,
    command_id: str,
    frontier: int,
    predecessor: str,
    result: str,
) -> None:
    journal.select(
        PreparedOwnerPublication(
            _batch(command_id, frontier, predecessor), "issued", "fence", frontier, predecessor
        ),
        result,
    )


def _journal(tmp_path: Path) -> IndependentOwnerDecisionJournal:
    return IndependentOwnerDecisionJournal(
        IndependentTenantDecisionJournal(tmp_path / "owners"), "tenant"
    )


def test_snapshot_at_preserves_selected_state_before_later_materialization(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    before = _digest(b"before")
    after = _digest(b"after")
    _select(journal, "first", 0, before, after)
    selected_head = journal.snapshot().head
    assert selected_head is not None
    selected = journal.snapshot().decisions[0]
    journal.materialized(selected)

    cut = journal.snapshot_at(selected_head)

    assert cut.head == selected_head
    assert cut.decisions == (selected,)
    assert cut.materialized_command_ids == frozenset()


def test_snapshot_at_none_explicitly_selects_the_empty_prefix(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    _select(journal, "first", 0, _digest(b"before"), _digest(b"after"))

    cut = journal.snapshot_at(None)

    assert cut.tenant_id == "tenant"
    assert cut.head is None
    assert cut.decisions == ()
    assert cut.materialized_command_ids == frozenset()


def test_snapshot_at_rejects_absent_head_and_allows_later_valid_pending(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    before = _digest(b"before")
    after = _digest(b"after")
    final = _digest(b"final")
    _select(journal, "first", 0, before, after)
    first = journal.snapshot().decisions[0]
    journal.materialized(first)
    completed_head = journal.snapshot().head
    assert completed_head is not None
    _select(journal, "second", 1, after, final)

    cut = journal.snapshot_at(completed_head)

    assert cut.decisions == (first,)
    assert cut.materialized_command_ids == frozenset({"first"})
    with pytest.raises(OwnerJournalIntegrityError, match=r"snapshot_at.*tenant.*absent"):
        journal.snapshot_at("absent")


def test_snapshot_at_rejects_semantically_corrupt_current_tail(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    before = _digest(b"before")
    _select(journal, "first", 0, before, _digest(b"after"))
    head = journal.snapshot().head
    assert head is not None
    raw = journal._raw
    raw.append(b"{}", head)

    with pytest.raises(OwnerJournalIntegrityError, match=r"snapshot_at.*tenant"):
        journal.snapshot_at(head)
