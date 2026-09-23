"""Selected journal history must agree with fresh, exact physical membership."""

import sqlite3
from pathlib import Path

import pytest

from chiplog.composition.r14_runtime import R14PlanningRuntime, open_r14_runtime
from chiplog.platform.authority_reads import capture_authority_storage_state
from chiplog.platform.owner_decision_journal import OwnerJournalIntegrityError
from chiplog.platform.workspace_snapshot import workspace_snapshot
from tests.composition.test_journal_finalization_gate import _Pending, _select


async def _recover(runtime: R14PlanningRuntime, selected: _Pending) -> None:
    await runtime._recover_exact(
        selected.command,
        selected.predecessor,
        selected.resulting,
        is_materialized=selected.completed,
    )


def _remove_selected_row(database: Path, selected: _Pending) -> None:
    with sqlite3.connect(database) as connection:
        deleted = connection.execute(
            "DELETE FROM records WHERE tenant_id = ? AND record_id = ?",
            (selected.command.tenant_id, selected.command.records[0].record_id),
        )
        assert deleted.rowcount == 1


@pytest.mark.parametrize("family", ["owner", "loop", "gate"])
@pytest.mark.parametrize("historical", [False, True])
async def test_recovery_rejects_missing_selected_membership(
    tmp_path: Path, family: str, historical: bool
) -> None:
    database = tmp_path / "main.sqlite3"
    async with open_r14_runtime(database) as runtime:
        selected = await _select(runtime, family)
        await _recover(runtime, selected)
        if historical:
            selected.finish()
            assert selected.completed()
        else:
            assert not selected.completed()
        _remove_selected_row(database, selected)
        if historical:
            # Deliberately authenticate the damaged current state: membership must
            # independently reject it, rather than relying on anchor mismatch.
            runtime._commitment_journal.commit(
                runtime._tenant_id, capture_authority_storage_state(database)[0]
            )
        anchor_before = runtime._commitment_journal.load(runtime._tenant_id)
        journal_before = runtime._loop_decisions().entries()
        with pytest.raises(OwnerJournalIntegrityError):
            await _recover(runtime, selected)
        assert runtime._commitment_journal.load(runtime._tenant_id) == anchor_before
        assert runtime._loop_decisions().entries() == journal_before
        with sqlite3.connect(database) as connection:
            assert (
                connection.execute(
                    "SELECT 1 FROM records WHERE tenant_id = ? AND record_id = ?",
                    (selected.command.tenant_id, selected.command.records[0].record_id),
                ).fetchone()
                is None
            )


async def test_loop_historical_completion_rejects_removed_record(tmp_path: Path) -> None:
    database = tmp_path / "main.sqlite3"
    async with open_r14_runtime(database) as runtime:
        selected = await _select(runtime, "loop")
        await _recover(runtime, selected)
        selected.finish()
        assert selected.completed()
        _remove_selected_row(database, selected)
        with pytest.raises(OwnerJournalIntegrityError):
            selected.completed()
        with pytest.raises(OwnerJournalIntegrityError):
            selected.finish()


@pytest.mark.parametrize("damage", ["unrelated_storage", "wrong_anchor", "missing_anchor"])
async def test_loop_history_requires_valid_current_anchor(tmp_path: Path, damage: str) -> None:
    database = tmp_path / "main.sqlite3"
    async with open_r14_runtime(database) as runtime:
        selected = await _select(runtime, "loop")
        await _recover(runtime, selected)
        selected.finish()
        if damage == "unrelated_storage":
            with sqlite3.connect(database) as connection:
                changed = connection.execute(
                    "UPDATE tenant_heads SET head = head + 1 WHERE tenant_id = ?",
                    (runtime._tenant_id,),
                )
                assert changed.rowcount == 1
        elif damage == "wrong_anchor":
            runtime._commitment_journal.commit(runtime._tenant_id, "0" * 64)
        else:
            runtime._commitment_journal._path.unlink()
        # The exact historical records remain present in all three cases.
        with sqlite3.connect(database) as connection:
            for record in selected.command.records:
                assert connection.execute(
                    "SELECT canonical_bytes FROM records WHERE tenant_id = ? AND record_id = ?",
                    (runtime._tenant_id, record.record_id),
                ).fetchone() == (record.canonical_bytes,)
        with pytest.raises(OwnerJournalIntegrityError):
            selected.completed()
        with pytest.raises(OwnerJournalIntegrityError):
            selected.finish()


async def test_loop_history_does_not_join_stale_workspace_snapshot(tmp_path: Path) -> None:
    database = tmp_path / "main.sqlite3"
    async with open_r14_runtime(database) as runtime:
        selected = await _select(runtime, "loop")
        await _recover(runtime, selected)
        selected.finish()
        record = selected.command.records[0]
        query = "SELECT canonical_bytes FROM records WHERE tenant_id = ? AND record_id = ?"
        parameters = (runtime._tenant_id, record.record_id)
        with workspace_snapshot(database) as snapshot:
            # BEGIN alone does not pin a SQLite read cut; this SELECT does.
            assert snapshot.connection.execute(query, parameters).fetchone() == (
                record.canonical_bytes,
            )
            _remove_selected_row(database, selected)
            runtime._commitment_journal.commit(
                runtime._tenant_id, capture_authority_storage_state(database)[0]
            )
            assert snapshot.connection.execute(query, parameters).fetchone() == (
                record.canonical_bytes,
            )
            with pytest.raises(OwnerJournalIntegrityError):
                selected.completed()
            with pytest.raises(OwnerJournalIntegrityError):
                await _recover(runtime, selected)
