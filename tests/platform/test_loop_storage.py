import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest

from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.adapters.driven.loop_sqlite import LoopIntegrityError
from chiplog.capabilities.agent_loop import domain
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy, LoopRejected, ModelAttempt
from chiplog.composition.r13 import open_r13_loop
from chiplog.platform._sqlite import EventAppender, PhysicalPublicationCommand, PublicationResult


async def test_corrupted_physical_publication_key_cannot_be_read_as_valid(tmp_path: Path) -> None:
    database = tmp_path / "loop.sqlite"
    async with open_r13_loop(database, responses=()) as loop:
        await loop.create("r", "Plan", BudgetPolicy())
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute(
                "UPDATE publications SET idempotency_key = 'wrong-' || idempotency_key "
                "WHERE operation_kind = 'agent_loop'"
            )
        with pytest.raises(LoopIntegrityError, match="operation=snapshot"):
            loop.status("r")


@pytest.mark.parametrize("fault", ["before_commit", "after_commit"])
async def test_complete_acceptance_failure_is_atomic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    response = b'{"kind":"Complete","deliveries":[{"kind":"NonAuthoritativeText","text":"Done"}]}'
    submit = EventAppender.submit

    async def injected(
        self: EventAppender, command: PhysicalPublicationCommand
    ) -> PublicationResult:
        if any(b'"event":"CompleteAcceptance"' in row.canonical_bytes for row in command.records):
            command = replace(command, fault=fault)  # type: ignore[arg-type]
        return await submit(self, command)

    invoke = HermeticModel.invoke

    async def reached(self: HermeticModel, attempt: ModelAttempt) -> tuple[bytes, str]:
        # Inject at the reached model boundary, after the real R12 workspace has
        # admitted its batch. The fault targets CompleteAcceptance's transaction.
        result = await invoke(self, attempt)
        monkeypatch.setattr(EventAppender, "submit", injected)
        return result

    monkeypatch.setattr(HermeticModel, "invoke", reached)
    database = tmp_path / "loop.sqlite"
    async with open_r13_loop(database, responses=(response,)) as loop:
        created = await loop.create("r", "Plan", BudgetPolicy())
        active = await loop.activate("r", created.head)
        with pytest.raises(RuntimeError):
            await loop.step("r", active.head)
        record = loop.record("r")
        assert (record.state == "SUCCEEDED") == (fault == "after_commit")
        assert bool(record.accepted_text) == bool(record.deliveries) == (fault == "after_commit")
        with closing(sqlite3.connect(database)) as connection:
            accepted = connection.execute(
                "SELECT count(*) FROM records WHERE owner='conversation' AND record_id='r/accepted'"
            ).fetchone()[0]
        assert accepted == int(fault == "after_commit")
    monkeypatch.setattr(EventAppender, "submit", submit)
    async with open_r13_loop(database, responses=()) as loop:
        recovered = loop.record("r")
        assert recovered.state == "SUCCEEDED"
        assert recovered.accepted_text == ("Done",)
        assert len(recovered.deliveries) == 1
        if fault == "after_commit":
            assert recovered == record


async def test_canonical_successor_cannot_drop_historical_turn_to_reset_budget(
    tmp_path: Path,
) -> None:
    response = (
        b'{"kind":"Continue","tool_calls":[{"call_id":"c",'
        b'"tool":"propose_intent","text":"Calendar proposal"}]}'
    )
    async with open_r13_loop(tmp_path / "loop.sqlite", responses=(response,)) as loop:
        created = await loop.create("r", "Plan", BudgetPolicy(max_turns=1))
        active = await loop.activate("r", created.head)
        await loop.step("r", active.head)
        previous = loop.record("r")
        corrupted = domain.successor(previous, "ToolTerminal", turns=())
        with pytest.raises(LoopRejected):
            await loop._store.publish(corrupted, loop._store.snapshot())
        assert loop.record("r") == previous
