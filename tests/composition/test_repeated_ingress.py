"""Distinct accepted messages retain distinct provenance even with equal bytes."""

from pathlib import Path
from typing import cast

from chiplog.capabilities.agent_loop.contracts import BudgetPolicy
from chiplog.composition.r13_workspace import R13Workspace, _history
from chiplog.composition.r14 import open_r14_loop
from chiplog.composition.r14_runtime import R14PlanningRuntime

_COMPLETE_PLAN = b'{"kind":"Complete","deliveries":[{"kind":"NonAuthoritativeText","text":"Plan"}]}'


async def test_identical_prompts_keep_distinct_ingress_through_context_and_reopen(
    tmp_path: Path,
) -> None:
    database = tmp_path / "loop.sqlite"
    async with open_r14_loop(database, responses=()) as loop:
        first = await loop.create("first", "Plan", BudgetPolicy())
        second = await loop.create("second", "Plan", BudgetPolicy())
        assert first.run_id != second.run_id
        runtime = cast(R14PlanningRuntime, loop._session)
        entries = _history(runtime)
        assert tuple(entry.entry_id for entry in entries) == (
            "first/ingress",
            "second/ingress",
        )
        assert tuple(entry.accepted_bytes for entry in entries) == (b"Plan", b"Plan")
        assert tuple(entry.envelope.sources[0].record_id for entry in entries) == (
            "first/ingress",
            "second/ingress",
        )
        await R13Workspace(runtime).context(loop.record("second"))
    async with open_r14_loop(database, responses=()) as reopened:
        runtime = cast(R14PlanningRuntime, reopened._session)
        assert _history(runtime) == entries
        original = reopened._store.snapshot().records[-1]
        await R13Workspace(runtime).context(original)


async def test_identical_principal_and_assistant_text_keep_original_complete_attempt_closure(
    tmp_path: Path,
) -> None:
    database = tmp_path / "loop.sqlite"
    async with open_r14_loop(database, responses=(_COMPLETE_PLAN,)) as loop:
        created = await loop.create("first", "Plan", BudgetPolicy())
        active = await loop.activate("first", created.head)
        completed = await loop.step("first", active.head)
        assert completed.state == "SUCCEEDED"
        original = loop.record("first")
        runtime = cast(R14PlanningRuntime, loop._session)
        entries = _history(runtime)
        assert tuple(entry.accepted_bytes for entry in entries) == (b"Plan", b"Plan")
        assert entries[0].envelope.sources != entries[1].envelope.sources
        expected_records = {
            member.record_id
            for turn in original.turns
            for attempt in turn.attempts
            for member in attempt.manifest.members
        }
        assert {source.record_id for source in entries[1].envelope.sources} == expected_records
        await loop.create("second", "Plan", BudgetPolicy())
        await R13Workspace(runtime).context(loop.record("second"))
        expected_history = _history(runtime)
    async with open_r14_loop(database, responses=()) as reopened:
        runtime = cast(R14PlanningRuntime, reopened._session)
        assert _history(runtime) == expected_history
        await R13Workspace(runtime).context(reopened._store.snapshot().records[-1])
