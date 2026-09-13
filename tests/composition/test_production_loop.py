import asyncio
import os
from pathlib import Path
from typing import cast

import pytest

from chiplog.capabilities.agent_loop.contracts import BudgetPolicy, LoopRejected
from chiplog.composition.r13 import open_r13_loop
from chiplog.composition.r13_runtime import R13Runtime

COMPLETE = b'{"kind":"Complete","deliveries":[{"kind":"NonAuthoritativeText","text":"Ready"}]}'
CONTINUE = (
    b'{"kind":"Continue","tool_calls":[{"call_id":"c","tool":"propose_intent",'
    b'"text":"Propose a calendar event"}]}'
)


async def test_real_loop_owner_canaries_and_old_generation_cannot_continue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHIPLOG_R13_SECRET_CANARY", "must-not-reach-any-owner")
    async with open_r13_loop(tmp_path / "loop.sqlite", responses=(CONTINUE, COMPLETE)) as loop:
        runtime = cast(R13Runtime, loop._session)
        attestations = runtime._supervisor.runtime().attest()
        # Each live worker attempts filesystem, AF_INET and process access;
        # attest rejects unless all fail and the inherited environment is empty.
        assert {value.identity.owner_id for value in attestations} == {
            "agent_loop",
            "deployment_trust",
            "planning",
            "projections",
        }
        assert all(value.process_id != os.getpid() for value in attestations)
        created = await loop.create("r", "Help", BudgetPolicy())
        active = await loop.activate("r", created.head)
        current = await loop.step("r", active.head)
        before = loop._store.snapshot()
        runtime.restart_generation()
        with pytest.raises(LoopRejected, match="stale loop worker"):
            await loop.step("r", current.head)
        assert loop._store.snapshot() == before
        assert b"must-not-reach-any-owner" not in before.canonical_bytes()


async def test_canonical_loop_persists_attempt_before_response_and_atomic_completion(
    tmp_path: Path,
) -> None:
    database = tmp_path / "loop.sqlite"
    async with open_r13_loop(database, responses=(CONTINUE, COMPLETE)) as loop:
        created = await loop.create("run", "Help me plan", BudgetPolicy())
        active = await loop.activate("run", created.head)
        continued = await loop.step("run", active.head)
        assert continued.state == "ACTIVE"
        assert loop.record("run").turns[0].sealed_calls[0].result  # type: ignore[index]
        succeeded = await loop.step("run", continued.head)
        assert succeeded.state == "SUCCEEDED"
        run = loop.record("run")
        assert run.accepted_text == ("Ready",)
        assert len(run.deliveries) == 1
        assert run.deliveries[0].payload.endpoint == run.origin
        assert all(t.attempts[t.selector].state == "TERMINAL_ACCEPTED" for t in run.turns)
    async with open_r13_loop(database, responses=()) as restarted:
        assert restarted.record("run") == run
        with pytest.raises(LoopRejected):
            await restarted.step("run", succeeded.head)


async def test_rival_turn_start_has_one_winner(tmp_path: Path) -> None:
    async with open_r13_loop(tmp_path / "loop.sqlite", responses=(COMPLETE,)) as loop:
        created = await loop.create("run", "Hello", BudgetPolicy())
        active = await loop.activate("run", created.head)
        results = await asyncio.gather(
            loop.step("run", active.head), loop.step("run", active.head), return_exceptions=True
        )
        assert sum(isinstance(result, LoopRejected) for result in results) == 1
        assert len(loop.record("run").turns) == 1


@pytest.mark.parametrize(
    "response",
    [
        b'{"kind":"Complete","deliveries":[],"tool_calls":[]}',
        b'{"kind":"Continue","tool_calls":[{"call_id":"x","tool":"send_calendar","text":"x"}]}',
        b'{"kind":"Complete","deliveries":[{"kind":"DeliveryAssertion","text":"Committed",'
        b'"assertion_code":"LOCAL_PLANNING_COMMITTED","evidence_id":"invented"}]}',
        b"not json",
        b'{"kind":"Complete","deliveries":[{"kind":"NonAuthoritativeText","text":"canary",'
        b'"endpoint":{"kind":"MODEL_SELECTED_EXACT","ingress_binding_head":"hermetic-ingress-v1",'
        b'"endpoint_head":"hermetic-endpoint-v1","endpoint_id":"real-recipient-canary",'
        b'"provider":"hermetic-local","recipient":"canary@example.invalid",'
        b'"canonical_address":"https://canary.invalid/recipient",'
        b'"credential_binding_head":"hermetic-v1"}}]}',
    ],
)
async def test_schema_or_semantic_reject_keeps_trace_without_success(
    tmp_path: Path, response: bytes
) -> None:
    async with open_r13_loop(tmp_path / "loop.sqlite", responses=(response,)) as loop:
        created = await loop.create("run", "Hello", BudgetPolicy())
        active = await loop.activate("run", created.head)
        result = await loop.step("run", active.head)
        run = loop.record("run")
        assert result.state != "SUCCEEDED"
        assert run.accepted_text == ()
        assert run.deliveries == ()
        assert run.turns[-1].attempts[-1].state == "TERMINAL_REJECTED"
        assert run.turns[-1].attempts[-1].response_base64 is not None


async def test_turn_budget_survives_restart(tmp_path: Path) -> None:
    database = tmp_path / "loop.sqlite"
    async with open_r13_loop(database, responses=(CONTINUE,)) as loop:
        created = await loop.create("run", "Hello", BudgetPolicy(max_turns=1))
        active = await loop.activate("run", created.head)
        first = await loop.step("run", active.head)
    async with open_r13_loop(database, responses=(COMPLETE,)) as loop:
        suspended = await loop.step("run", first.head)
        assert suspended.state == "SUSPENDED"
        assert suspended.turn_ordinal == 1


async def test_rejected_raw_trace_is_visible_to_next_turn_without_becoming_conversation(
    tmp_path: Path,
) -> None:
    async with open_r13_loop(
        tmp_path / "loop.sqlite", responses=(b"malformed private response", COMPLETE)
    ) as loop:
        created = await loop.create("r", "Help", BudgetPolicy())
        active = await loop.activate("r", created.head)
        rejected = await loop.step("r", active.head)
        assert loop.record("r").accepted_text == ()
        succeeded = await loop.step("r", rejected.head)
        run = loop.record("r")
        assert succeeded.state == "SUCCEEDED" and run.accepted_text == ("Ready",)
        assert run.turns[0].attempts[0].state == "TERMINAL_REJECTED"
        assert "rejection" in run.turns[1].attempts[0].request
