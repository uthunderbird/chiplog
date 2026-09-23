"""Actual owner IPC, atomic fanout selection, and exact historical recovery."""

import asyncio
import json
import sqlite3
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Literal, cast

import pytest

from chiplog.capabilities.agent_loop.application import AgentLoop
from chiplog.capabilities.agent_loop.call_acceptance_preparation import (
    _proposal_digest,
    call_record_reference,
)
from chiplog.capabilities.agent_loop.contracts import (
    BudgetPolicy,
    LoopRejected,
    LoopSnapshot,
    RunRecord,
)
from chiplog.capabilities.agent_loop.fan_out_contracts import CapturedFanOutProposal
from chiplog.composition.r14 import open_r14_loop
from chiplog.composition.r14_fanout_contracts import (
    FANOUT_OPERATION,
    INITIALIZED_SCHEMA,
    SEAL_SCHEMA,
)
from chiplog.composition.r14_runtime import R14PlanningRuntime
from chiplog.platform._sqlite import (
    LostCommitAcknowledgement,
    PhysicalPublicationCommand,
    PublicationResult,
)
from chiplog.platform.broker import PublicPortCall, PublicPortResult, PublicPortSuccess
from chiplog.platform.r7_runtime import AuthorityBrokerRuntime

_CONTINUE = (
    b'{"kind":"Continue","tool_calls":[{"call_id":"one",'
    b'"tool":"propose_planning","text":"Swim"},{"call_id":"two",'
    b'"tool":"propose_planning","text":"Run"}]}'
)
_COMPLETE = b'{"kind":"Complete","deliveries":[{"kind":"NonAuthoritativeText","text":"Ready"}]}'
_FANOUT_CALL = "agent_loop.prepare_captured_fan_out"


class _Captured(Exception):
    pass


async def _capture(
    loop: AgentLoop, monkeypatch: pytest.MonkeyPatch
) -> tuple[RunRecord, LoopSnapshot]:
    """Stop the real application immediately before acceptance publication."""
    created = await loop.create("run", "Plan", BudgetPolicy())
    active = await loop.activate("run", created.head)
    publish = loop._store.publish
    held: list[tuple[RunRecord, LoopSnapshot]] = []

    async def stop(
        record: RunRecord,
        expected: LoopSnapshot,
        validate: Callable[[LoopSnapshot], None] | None = None,
    ) -> None:
        if record.event in ("ModelResponseReceived", "CompleteAcceptance"):
            held.append((record, expected))
            raise _Captured
        await publish(record, expected, validate)

    with monkeypatch.context() as patch:
        patch.setattr(loop._store, "publish", stop)
        with pytest.raises(_Captured):
            await loop.step("run", active.head)
    assert len(held) == 1
    assert held[0][1].records[-1].event == "ModelResponseCaptured"
    return held[0]


def _selected(runtime: R14PlanningRuntime) -> list[dict[str, object]]:
    return [
        entry
        for _, _, raw in runtime._loop_decisions().entries()
        if (entry := json.loads(raw)).get("operation_kind") == FANOUT_OPERATION
        and entry.get("kind") == "DECIDED"
    ]


def _batch(database: Path, head: str) -> tuple[int, tuple[str, ...], list[tuple[str, str, int]]]:
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT commit_sequence, record_ids FROM publications "
            "WHERE operation_kind=? AND idempotency_key=?",
            (FANOUT_OPERATION, head),
        ).fetchone()
        assert row is not None
        sequence, joined = row
        members = tuple(joined.split("\n"))
        records = [
            connection.execute(
                "SELECT record_id,schema_id,commit_sequence FROM records WHERE record_id=?", (key,)
            ).fetchone()
            for key in members
        ]
        assert all(item is not None for item in records)
        return sequence, members, records


@pytest.mark.parametrize("response,count", ((_CONTINUE, 2), (_COMPLETE, 0)))
async def test_actual_owner_selects_exact_atomic_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, response: bytes, count: int
) -> None:
    database = tmp_path / "loop.sqlite"
    async with open_r14_loop(database, responses=(response,)) as loop:
        runtime = cast(R14PlanningRuntime, loop._session)
        record, expected = await _capture(loop, monkeypatch)
        engine = runtime._supervisor.runtime()
        call = engine.call
        calls: list[PublicPortCall] = []

        async def observe(sent: PublicPortCall) -> PublicPortResult:
            if sent.operation_id == _FANOUT_CALL:
                calls.append(sent)
            return await call(sent)

        with monkeypatch.context() as patch:
            patch.setattr(engine, "call", observe)
            await loop._store.publish(record, expected)
        assert len(calls) == 1
        sequence, members, rows = _batch(database, record.head)
        assert sequence == expected.tenant_head + 1
        assert len(members) == 2 + count + (response == _COMPLETE)
        assert members[0] == record.head
        assert rows[1][1] == SEAL_SCHEMA
        assert [item[1] for item in rows[2 : 2 + count]] == [INITIALIZED_SCHEMA] * count
        assert {item[2] for item in rows} == {sequence}
        assert len(_selected(runtime)) == 1
        assert loop._store.snapshot().records[-1] == record
        assert runtime._loop_snapshot() == loop._store.snapshot()


async def test_rejecting_callback_selects_nothing_and_historical_replay_skips_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with open_r14_loop(tmp_path / "loop.sqlite", responses=(_COMPLETE,)) as loop:
        runtime = cast(R14PlanningRuntime, loop._session)
        record, expected = await _capture(loop, monkeypatch)
        journal = runtime._loop_decisions().entries()
        calls = 0

        def reject(current: LoopSnapshot) -> None:
            nonlocal calls
            calls += 1
            assert current == expected
            raise LoopRejected("test predicate denied")

        with pytest.raises(LoopRejected, match="test predicate denied"):
            await loop._store.publish(record, expected, reject)
        assert calls == 1
        assert runtime._loop_decisions().entries() == journal
        assert loop._store.snapshot() == expected
        await loop._store.publish(record, expected)
        await loop.create("later-run", "Later independent Run", BudgetPolicy())
        selected = runtime._loop_decisions().entries()
        anchor = runtime._commitment_journal.load(runtime._tenant_id)
        await loop._store.publish(record, expected, reject)
        assert calls == 1
        assert runtime._loop_decisions().entries() == selected
        assert runtime._commitment_journal.load(runtime._tenant_id) == anchor
        with pytest.raises(LoopRejected, match="predecessor snapshot"):
            await loop._store.publish(record, expected.model_copy(update={"tenant_head": 0}))
        assert runtime._loop_decisions().entries() == selected


@pytest.mark.parametrize("change", ("predicate", "worker"))
async def test_admission_changes_while_real_owner_reply_is_suspended(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    async with open_r14_loop(tmp_path / "loop.sqlite", responses=(_CONTINUE,)) as loop:
        runtime = cast(R14PlanningRuntime, loop._session)
        record, expected = await _capture(loop, monkeypatch)
        engine = runtime._supervisor.runtime()
        call = engine.call
        replied, release = asyncio.Event(), asyncio.Event()
        allowed = True

        async def suspended(sent: PublicPortCall) -> PublicPortResult:
            result = await call(sent)
            if sent.operation_id == _FANOUT_CALL:
                assert isinstance(result, PublicPortSuccess)
                replied.set()
                await release.wait()
            return result

        def validate(current: LoopSnapshot) -> None:
            assert current == expected
            if not allowed:
                raise LoopRejected("late predicate denied")

        journal = runtime._loop_decisions().entries()
        with monkeypatch.context() as patch:
            patch.setattr(engine, "call", suspended)
            task = asyncio.create_task(loop._store.publish(record, expected, validate))
            try:
                await asyncio.wait_for(replied.wait(), 10)
                if change == "predicate":
                    allowed = False
                    reason = "late predicate denied"
                else:
                    patch.setattr(runtime, "current_worker", lambda: "replacement-worker")
                    reason = "STALE"
                release.set()
                with pytest.raises(LoopRejected, match=reason):
                    await task
            finally:
                release.set()
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
        assert runtime._loop_decisions().entries() == journal
        assert loop._store.snapshot() == expected


@pytest.mark.parametrize("fault", ("before_commit", "after_commit"))
async def test_restart_materializes_selected_bytes_without_fanout_preparation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: Literal["before_commit", "after_commit"],
) -> None:
    database = tmp_path / "loop.sqlite"
    async with open_r14_loop(database, responses=(_CONTINUE,)) as loop:
        runtime = cast(R14PlanningRuntime, loop._session)
        record, expected = await _capture(loop, monkeypatch)
        submit = runtime._appender.submit

        async def crash(command: PhysicalPublicationCommand) -> PublicationResult:
            return await submit(replace(command, fault=fault))

        with monkeypatch.context() as patch:
            patch.setattr(runtime._appender, "submit", crash)
            if fault == "before_commit":
                with pytest.raises(RuntimeError, match="injected fault before commit"):
                    await loop._store.publish(record, expected)
            else:
                with pytest.raises(LostCommitAcknowledgement):
                    await loop._store.publish(record, expected)
        selected = _selected(runtime)
        assert len(selected) == 1
        with sqlite3.connect(database) as connection:
            actual = connection.execute(
                "SELECT COUNT(*) FROM publications WHERE operation_kind=?", (FANOUT_OPERATION,)
            ).fetchone()
            assert actual == ((0,) if fault == "before_commit" else (1,))

    original = AuthorityBrokerRuntime.call

    async def forbidden_fanout(
        self: AuthorityBrokerRuntime, sent: PublicPortCall
    ) -> PublicPortResult:
        assert sent.operation_id != _FANOUT_CALL, "recovery reinvoked fanout owner"
        return await original(self, sent)

    with monkeypatch.context() as patch:
        patch.setattr(AuthorityBrokerRuntime, "call", forbidden_fanout)
        async with open_r14_loop(database, responses=()) as reopened:
            runtime = cast(R14PlanningRuntime, reopened._session)
            assert reopened._store.snapshot().records[-1] == record
            assert _selected(runtime) == selected
            assert not runtime._pending()
            assert len(_batch(database, record.head)[1]) == 4
            await reopened._store.publish(record, expected)


async def test_rehashed_same_session_owner_substitution_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with open_r14_loop(tmp_path / "loop.sqlite", responses=(_CONTINUE,)) as loop:
        runtime = cast(R14PlanningRuntime, loop._session)
        record, expected = await _capture(loop, monkeypatch)
        engine = runtime._supervisor.runtime()
        call = engine.call

        async def substitute(sent: PublicPortCall) -> PublicPortResult:
            result = await call(sent)
            if sent.operation_id != _FANOUT_CALL:
                return result
            assert isinstance(result, PublicPortSuccess)
            proposal = CapturedFanOutProposal.model_validate_json(result.canonical_payload)
            inner = proposal.fan_out
            changed = inner.initialized_records[0].model_copy(update={"original_call_id": "forged"})
            initialized = (changed, *inner.initialized_records[1:])
            references = tuple(
                call_record_reference(item.original_call_id, item) for item in initialized
            )
            seal = inner.response_seal.model_copy(
                update={"complete_ordered_initialized": references}
            )
            inner = inner.model_copy(
                update={
                    "initialized_records": initialized,
                    "response_seal": seal,
                    "complete_ordered_record_manifest": (
                        call_record_reference(seal.response_seal_id, seal),
                        *references,
                    ),
                }
            )
            inner = inner.model_copy(update={"proposal_fingerprint": _proposal_digest(inner)})
            proposal = proposal.model_copy(update={"fan_out": inner})
            proposal = proposal.model_copy(
                update={"proposal_fingerprint": _proposal_digest(proposal)}
            )
            return result.model_copy(update={"canonical_payload": proposal.canonical_bytes()})

        journal = runtime._loop_decisions().entries()
        with monkeypatch.context() as patch:
            patch.setattr(engine, "call", substitute)
            with pytest.raises(LoopRejected):
                await loop._store.publish(record, expected)
        assert runtime._loop_decisions().entries() == journal
        assert loop._store.snapshot() == expected


async def test_actual_planning_adoption_replays_and_complete_keeps_receipt(tmp_path: Path) -> None:
    database = tmp_path / "loop.sqlite"
    async with open_r14_loop(database, responses=(_CONTINUE, _COMPLETE)) as loop:
        runtime = cast(R14PlanningRuntime, loop._session)
        created = await loop.create("run", "Plan", BudgetPolicy())
        active = await loop.activate("run", created.head)
        await loop.step("run", active.head)
        display = await loop.display("run/turn/1/proposal/one")
        assert loop.record("run").planning_receipts == ()
        receipt = await loop.adopt(
            "hermetic-ingress", display.display_id, display.display_digest, display.adoption_act_id
        )
        assert receipt.purpose == "Swim"
        assert loop.record("run").planning_receipts == (receipt,)
        before_replay = loop._store.snapshot()
        replay = await loop.adopt(
            "hermetic-ingress", display.display_id, display.display_digest, display.adoption_act_id
        )
        assert replay == receipt
        assert loop._store.snapshot() == before_replay
        completed = await loop.step("run", loop.record("run").head)
        assert completed.state == "SUCCEEDED"
        record = loop.record("run")
        assert record.planning_receipts == (receipt,)
        assert record.accepted_text == ("Ready",)
        assert runtime._proposal(display.proposal_id) == (record, "Swim")
        assert len(_selected(runtime)) == 2
        assert len(_batch(database, record.head)[1]) == 3
    async with open_r14_loop(database, responses=()) as reopened:
        runtime = cast(R14PlanningRuntime, reopened._session)
        assert reopened._store.snapshot().records[-1] == record
        assert runtime._proposal(display.proposal_id) == (record, "Swim")
