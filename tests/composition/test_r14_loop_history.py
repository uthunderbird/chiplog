"""Canonical R14 history reads authenticate selected bytes, not a storage marker."""

import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from chiplog.adapters.driven.loop_sqlite import LoopIntegrityError
from chiplog.capabilities.agent_loop.application import AgentLoop
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy, LoopRejected, RunRecord
from chiplog.composition.h1_completion_issuance import SCHEMA as H1_ISSUANCE_SCHEMA
from chiplog.composition.r14 import open_r14_loop
from chiplog.composition.r14_execution_completion_records import (
    RetainedCompleteAcceptanceExchangeV1,
    complete_acceptance_command,
)
from chiplog.composition.r14_loop_history import (
    _completion_v2_schema_dispatch,
    completion_v2_terminal_run,
)
from chiplog.composition.r14_runtime import R14PlanningRuntime
from chiplog.platform.authority_reads import capture_authority_storage_state
from chiplog.platform.workspace_snapshot import workspace_snapshot
from tests.support.completion_assembly import accepted_completion_fixture

_CONTINUE = (
    b'{"kind":"Continue","tool_calls":[{"call_id":"plan",'
    b'"tool":"propose_planning","text":"Swim every week"}]}'
)
_COMPLETE = b'{"kind":"Complete","deliveries":[{"kind":"NonAuthoritativeText","text":"Ready"}]}'
_PROPOSAL = "run/turn/1/proposal/plan"


async def test_selected_complete_delivery_v2_projects_its_native_terminal_run() -> None:
    """A real closed owner fixture yields the one terminal Run in batch order."""
    fixture = await accepted_completion_fixture("v2")
    command = complete_acceptance_command(
        RetainedCompleteAcceptanceExchangeV1(
            assembly=fixture.assembly,
            batch=fixture.batch,
            expected_head=fixture.batch.expected.tenant_frontier,
            predecessor_commitment=fixture.batch.expected.expected_materialization_commitment,
        )
    )

    terminal = completion_v2_terminal_run(command)

    assert terminal == fixture.assembly.prepared_completion.run
    assert terminal.state == "SUCCEEDED"
    assert terminal.predecessor == fixture.assembly.original_completion_request.run.head


async def test_complete_delivery_v2_applicability_schema_dispatch_is_total() -> None:
    fixture = await accepted_completion_fixture("v2")

    with pytest.raises(ValueError, match="unsupported complete delivery v2 applicability schema"):
        _completion_v2_schema_dispatch(fixture.batch)

    h1 = fixture.batch.model_copy(
        update={
            "authentication": fixture.batch.authentication.model_copy(
                update={"applicability_schema": H1_ISSUANCE_SCHEMA}
            )
        }
    )
    assert _completion_v2_schema_dispatch(h1) == "H1"


def test_historical_trust_reader_returns_the_canonical_gated_durability() -> None:
    class Gate:
        entered = 0

        @contextmanager
        def hold(self):
            self.entered += 1
            yield

    runtime = object.__new__(R14PlanningRuntime)
    trust = object()
    gate = Gate()
    runtime._trust = trust
    runtime._authority_gate = lambda: gate  # type: ignore[method-assign]

    assert runtime._h1_historical_trust_reader() is trust
    assert gate.entered == 1


@pytest.mark.parametrize("damage", ("remove_run", "corrupt_run"))
async def test_selected_complete_delivery_v2_rejects_corrupt_native_physical_member(
    damage: str,
) -> None:
    fixture = await accepted_completion_fixture("v2")
    command = complete_acceptance_command(
        RetainedCompleteAcceptanceExchangeV1(
            assembly=fixture.assembly,
            batch=fixture.batch,
            expected_head=fixture.batch.expected.tenant_frontier,
            predecessor_commitment=fixture.batch.expected.expected_materialization_commitment,
        )
    )
    run_index = next(
        index
        for index, record in enumerate(command.records)
        if record.schema_id == "chiplog.agent-loop.execution-record.v2"
    )
    if damage == "remove_run":
        damaged = replace(
            command, records=command.records[:run_index] + command.records[run_index + 1 :]
        )
    else:
        run = command.records[run_index]
        damaged = replace(
            command,
            records=(
                *command.records[:run_index],
                replace(run, canonical_bytes=b"{}"),
                *command.records[run_index + 1 :],
            ),
        )

    with pytest.raises(ValueError):
        completion_v2_terminal_run(damaged)


async def _finish(loop: AgentLoop) -> RunRecord:
    created = await loop.create("run", "Help me plan", BudgetPolicy())
    active = await loop.activate("run", created.head)
    continued = await loop.step("run", active.head)
    assert continued.state == "ACTIVE"
    first = loop.record("run")
    assert first.turns[0].sealed_calls is not None
    assert first.turns[0].sealed_calls[0].state == "TERMINAL"
    assert first.turns[0].sealed_calls[0].proposal_id == _PROPOSAL
    completed = await loop.step("run", continued.head)
    assert completed.state == "SUCCEEDED"
    final = loop.record("run")
    assert final.accepted_text == ("Ready",)
    assert len(final.deliveries) == 1
    return final


async def test_canonical_r14_continue_complete_and_planning_lookup_survive_reopen(
    tmp_path: Path,
) -> None:
    database = tmp_path / "loop.sqlite"
    async with open_r14_loop(database, responses=(_CONTINUE, _COMPLETE)) as loop:
        runtime = cast(R14PlanningRuntime, loop._session)
        run = await _finish(loop)
        assert runtime._proposal(_PROPOSAL) == (run, "Swim every week")
        snapshot = loop._store.snapshot()
        assert snapshot.records[-1] == run
        assert snapshot == runtime._loop_snapshot()
        assert run.worker_session == runtime.current_worker()
    async with open_r14_loop(database, responses=()) as reopened:
        runtime = cast(R14PlanningRuntime, reopened._session)
        with pytest.raises(LoopRejected, match="current authenticated binding"):
            reopened.record("run")
        assert reopened._store.snapshot().records[-1] == run
        assert reopened._store.snapshot() == snapshot
        assert runtime._proposal(_PROPOSAL) == (run, "Swim every week")


async def test_historical_read_does_not_reinvoke_owner_or_rebuild_companions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with open_r14_loop(tmp_path / "loop.sqlite", responses=(_CONTINUE, _COMPLETE)) as loop:
        run = await _finish(loop)
        runtime = cast(R14PlanningRuntime, loop._session)
        expected = loop._store.snapshot()
        journal = runtime._loop_decisions().entries()
        anchor = runtime._commitment_journal.load(runtime._tenant_id)

        def forbidden(*args: object, **kwargs: object) -> None:
            pytest.fail("historical read invoked live owner or regenerated companions")

        with monkeypatch.context() as patch:
            patch.setattr(runtime._supervisor.runtime(), "call_sync", forbidden)
            patch.setattr(runtime, "companions", forbidden)
            patch.setattr(runtime, "validate", forbidden)
            assert loop._store.snapshot() == expected
            assert runtime._proposal(_PROPOSAL) == (run, "Swim every week")
        assert runtime._loop_decisions().entries() == journal
        assert runtime._commitment_journal.load(runtime._tenant_id) == anchor


@pytest.mark.parametrize(
    "damage",
    (
        "remove_companion",
        "change_companion",
        "extra_companion",
        "reorder_membership",
        "remove_publication",
        "change_fingerprint",
        "orphan_publication",
        "orphan_loop",
        "unknown_loop",
    ),
)
async def test_both_readers_reject_reanchored_physical_history_tampering(
    tmp_path: Path,
    damage: str,
) -> None:
    database = tmp_path / "loop.sqlite"
    async with open_r14_loop(database, responses=(_CONTINUE, _COMPLETE)) as loop:
        run = await _finish(loop)
        runtime = cast(R14PlanningRuntime, loop._session)
        journal_before = runtime._loop_decisions().entries()
        with sqlite3.connect(database) as connection:
            row = connection.execute(
                "SELECT commit_sequence, record_ids FROM publications "
                "WHERE tenant_id = ? AND operation_kind = 'agent_loop.fanout.v1' "
                "AND idempotency_key = ?",
                (runtime._tenant_id, run.head),
            ).fetchone()
            assert row is not None
            sequence, members = row
            identifiers = members.split("\n")
            assert len(identifiers) == 3
            assert identifiers[0] == run.head
            companion = identifiers[-1]
            if damage == "remove_companion":
                connection.execute(
                    "DELETE FROM records WHERE tenant_id = ? AND record_id = ?",
                    (runtime._tenant_id, companion),
                )
            elif damage == "change_companion":
                connection.execute(
                    "UPDATE records SET canonical_bytes = ? WHERE tenant_id = ? AND record_id = ?",
                    (b"{}", runtime._tenant_id, companion),
                )
            elif damage == "extra_companion":
                connection.execute(
                    "INSERT INTO records SELECT tenant_id, 'extra-companion', owner, schema_id, "
                    "canonical_bytes, commit_sequence "
                    "FROM records WHERE tenant_id = ? AND record_id = ?",
                    (runtime._tenant_id, companion),
                )
            elif damage == "reorder_membership":
                connection.execute(
                    "UPDATE publications SET record_ids = ? "
                    "WHERE tenant_id = ? AND idempotency_key = ?",
                    ("\n".join(reversed(identifiers)), runtime._tenant_id, run.head),
                )
            elif damage == "remove_publication":
                connection.execute(
                    "DELETE FROM publications WHERE tenant_id = ? AND idempotency_key = ?",
                    (runtime._tenant_id, run.head),
                )
            elif damage == "change_fingerprint":
                connection.execute(
                    "UPDATE publications SET request_fingerprint = ? "
                    "WHERE tenant_id = ? AND idempotency_key = ?",
                    ("0" * 64, runtime._tenant_id, run.head),
                )
            elif damage == "orphan_publication":
                connection.execute(
                    "INSERT INTO publications VALUES (?, 'agent_loop', 'orphan', ?, ?, 'orphan')",
                    (runtime._tenant_id, "a" * 64, sequence + 1),
                )
            else:
                schema = (
                    "chiplog.agent-loop.record.v1" if damage == "orphan_loop" else "unknown.loop.v1"
                )
                connection.execute(
                    "INSERT INTO records VALUES (?, 'orphan', 'agent_loop', ?, ?, ?)",
                    (runtime._tenant_id, schema, run.canonical_bytes(), sequence + 1),
                )
        # A current physical anchor alone cannot turn this mutation into journal selection.
        runtime._commitment_journal.commit(
            runtime._tenant_id, capture_authority_storage_state(database)[0]
        )
        anchor_before = runtime._commitment_journal.load(runtime._tenant_id)
        for read in (loop._store.snapshot, lambda: runtime._proposal(_PROPOSAL)):
            with pytest.raises(LoopIntegrityError):
                read()
        assert runtime._loop_decisions().entries() == journal_before
        assert runtime._commitment_journal.load(runtime._tenant_id) == anchor_before


async def test_both_history_readers_reject_a_pinned_stale_workspace_cut(tmp_path: Path) -> None:
    database = tmp_path / "loop.sqlite"
    async with open_r14_loop(database, responses=(_CONTINUE, _COMPLETE)) as loop:
        await _finish(loop)
        runtime = cast(R14PlanningRuntime, loop._session)
        with workspace_snapshot(database) as snapshot:
            before = loop._store.snapshot()
            assert runtime._proposal(_PROPOSAL)[0] == before.records[-1]
            with sqlite3.connect(database) as connection:
                connection.execute(
                    "UPDATE tenant_heads SET head = head + 1 WHERE tenant_id = ?",
                    (runtime._tenant_id,),
                )
            runtime._commitment_journal.commit(
                runtime._tenant_id, capture_authority_storage_state(database)[0]
            )
            assert snapshot.connection.execute(
                "SELECT head FROM tenant_heads WHERE tenant_id = ?", (runtime._tenant_id,)
            ).fetchone() == (before.tenant_head,)
            for read in (loop._store.snapshot, lambda: runtime._proposal(_PROPOSAL)):
                with pytest.raises(LoopIntegrityError):
                    read()
