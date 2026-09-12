import hashlib
import json
from pathlib import Path

import pytest

from chiplog.capabilities.evidence_journal.commands import ConfirmationIngress, JournalRow
from chiplog.capabilities.projections.r9_boundary import WorkspaceRejected
from tests.support.evidence_journal import rows
from tests.support.workspace_batch import workspace


async def test_aligned_t02_confirmation_changes_journal_not_plan_and_invalidates_old_wording(
    tmp_path: Path,
) -> None:
    async with workspace(tmp_path, "Я не ходил в бассейн вчера.", with_plan=True) as (
        runtime,
        command,
    ):
        before = rows(tmp_path / "canonical.db")
        assert any(row[0] == "planning" for row in before)
        assert (await runtime.journal.execute("peer", command)).disposition == "NEEDS_CONFIRMATION"
        assert rows(tmp_path / "canonical.db") == before
        prepared = await runtime.journal.prepare("peer", command)
        assert prepared.record is not None and prepared.record.display is not None
        candidate_batch = await runtime.read_batch()
        candidate = JournalRow.model_validate_json(
            candidate_batch.journal.rows[0].canonical_payload
        )
        assert candidate.record.kind == "CandidateEvidence" and not candidate.current_positive
        assert candidate.status == "unknown"
        assert candidate.record.display is not None
        assert "Plan unchanged" in candidate.record.display.consequence
        display = prepared.record.display
        confirmation = command.model_copy(
            update={
                "command_id": "confirm-1",
                "action": "confirm_candidate",
                "display": display,
                "heads": display.heads,
            }
        )
        assert (await runtime.journal.execute("peer", confirmation)).disposition == "DENIED"
        payload = json.dumps(
            confirmation.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
        runtime.ingress.confirmation_received(
            "peer",
            ConfirmationIngress(
                confirmation_id=confirmation.command_id,
                binding_digest=hashlib.sha256(payload).hexdigest(),
            ),
        )
        assert (await runtime.journal.execute("peer", confirmation)).disposition == "COMMITTED"
        assert (await runtime.journal.execute("peer", confirmation)).disposition == "REPLAY"
        with pytest.raises(WorkspaceRejected):
            runtime.proposal_context(candidate_batch)
        current = await runtime.read_batch()
        owner_rows = [
            JournalRow.model_validate_json(row.canonical_payload) for row in current.journal.rows
        ]
        assert len(owner_rows) == 1 and owner_rows[0].record.kind == "FactClaim"
        assert owner_rows[0].record.claim.payload.outcome == "not_completed"
        assert tuple(
            row for row in rows(tmp_path / "canonical.db") if row[0] == "planning"
        ) == tuple(row for row in before if row[0] == "planning")


async def test_affirmative_direct_admission_and_calendar_read_cannot_publish_fact(
    tmp_path: Path,
) -> None:
    async with workspace(tmp_path) as (runtime, command):
        before = rows(tmp_path / "canonical.db")
        batch = await runtime.read_batch()
        assert batch.calendar.rows and not batch.journal.rows
        assert rows(tmp_path / "canonical.db") == before
        assert (await runtime.journal.execute("peer", command)).disposition == "COMMITTED"
        updated = await runtime.read_batch()
        row = JournalRow.model_validate_json(updated.journal.rows[0].canonical_payload)
        assert row.status == "user reported" and row.current_positive
        assert row.record.kind == "FactClaim"
        assert updated.planning.rows == batch.planning.rows
