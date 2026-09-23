"""Real canonical runtime histories; no current-effects authority fixture injection."""

import base64
import json
import sqlite3
from pathlib import Path

import pytest

from chiplog.adapters.driven.deployment_trust import IndependentTenantDecisionJournal
from chiplog.adapters.driven.effects_broker import EffectsIntegrityError
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy, LoopRejected
from chiplog.composition.r13 import open_r13_loop
from chiplog.composition.r13_planning import R13PlanningRuntime
from chiplog.composition.r14 import open_r14_loop
from chiplog.composition.r14_runtime import R14PlanningRuntime
from chiplog.composition.r16_effects import (
    EffectsCurrentWorkerHold,
    HermeticEffectProposal,
    R16EffectsProducer,
    read_materialized_effects,
)
from chiplog.platform.owner_decision_journal import IndependentOwnerDecisionJournal


def _response() -> bytes:
    proposal = HermeticEffectProposal(
        schema_id="chiplog.hermetic-effect-proposal.v1",
        purpose="Record my hermetic action",
        payload_base64=base64.b64encode(b"\xffexact\x00payload").decode(),
        bundle_members=("self-action",),
    )
    return json.dumps(
        {
            "kind": "Continue",
            "tool_calls": [
                {
                    "call_id": "effect",
                    "tool": "propose_intent",
                    "text": proposal.canonical_bytes().decode(),
                }
            ],
        }
    ).encode()


async def test_composite_preview_adoption_is_exact_and_still_unpublished(tmp_path: Path) -> None:
    database = tmp_path / "loop.sqlite"
    async with open_r13_loop(database, responses=(_response(),)) as loop:
        created = await loop.create("r", "Propose my hermetic action", BudgetPolicy())
        active = await loop.activate("r", created.head)
        await loop.step("r", active.head)
        runtime = loop._planning
        assert isinstance(runtime, R13PlanningRuntime)
        producer = R16EffectsProducer(runtime)
        display = await producer.display_effect("r/turn/1/proposal/effect")
        with sqlite3.connect(database) as connection:
            before = connection.execute(
                "SELECT * FROM publications ORDER BY commit_sequence"
            ).fetchall()
        assert "unknown" in display.display_text
        with pytest.raises(LoopRejected):
            await runtime.adopt(
                "hermetic-ingress",
                display.display_id,
                display.display_digest,
                display.adoption_act_id,
            )
        for peer, digest, act in (
            ("model", display.display_digest, display.adoption_act_id),
            ("hermetic-ingress", "forged", display.adoption_act_id),
            ("hermetic-ingress", display.display_digest, "other-act"),
        ):
            with pytest.raises(LoopRejected):
                await producer.adopt_effect(peer, display.display_id, digest, act)
        prepared = await producer.adopt_effect(
            "hermetic-ingress", display.display_id, display.display_digest, display.adoption_act_id
        )
        assert prepared.disposition == "PREPARED"
        assert prepared.binding.proposal.payload() == b"\xffexact\x00payload"
        with sqlite3.connect(database) as connection:
            assert (
                connection.execute("SELECT * FROM publications ORDER BY commit_sequence").fetchall()
                == before
            )
            assert connection.execute(
                "SELECT COUNT(*) FROM records WHERE owner IN ('effects', 'planning')"
            ).fetchone() == (0,)
        journal = IndependentOwnerDecisionJournal(
            IndependentTenantDecisionJournal(tmp_path / "owners.journal"), "hermetic-tenant"
        )
        cut = read_materialized_effects(runtime, journal)
        assert cut.worker is None
        assert (cut.physical_device, cut.physical_inode) == (
            database.stat().st_dev,
            database.stat().st_ino,
        )
        assert cut.rows == ()
        assert cut.tenant_frontier == before[-1][4]
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE tenant_heads SET head = head + 1")
        with pytest.raises(EffectsIntegrityError, match="read_materialized_effects") as error:
            read_materialized_effects(runtime, journal)
        assert error.value.__cause__ is not None


@pytest.mark.parametrize("mutation", ("frontier", "run", "expiry"))
async def test_composite_ipc_is_unlocked_and_does_not_replace_legacy_trace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    from types import SimpleNamespace

    from chiplog.capabilities.planning import CreateIntentionLine, PlanningOutcome
    from chiplog.capabilities.planning.r7_boundary import R7PlanningCreateDTO
    from chiplog.capabilities.planning.r8_boundary import R8PlanningRequest
    from chiplog.composition import r16_effects
    from chiplog.composition.r7_planning import PreparedPlanningCandidate
    from chiplog.composition.r16_effects import EffectPreviewBinding

    async with open_r13_loop(
        tmp_path / "race.sqlite", responses=(_response(), _response())
    ) as loop:
        created = await loop.create("r", "Propose", BudgetPolicy())
        active = await loop.activate("r", created.head)
        await loop.step("r", active.head)
        runtime = loop._planning
        assert isinstance(runtime, R13PlanningRuntime)
        original = runtime._prepare_planning_request
        sentinel = R8PlanningRequest(
            command_bytes=b"legacy-command",
            authority_trace_bytes=b"legacy-trace",
            observed_time_ns=0,
        )
        runtime._traced_request = sentinel
        runtime._trace_principal = "legacy-principal"
        mutate = False

        async def observed(
            command: CreateIntentionLine,
            request: R7PlanningCreateDTO | R8PlanningRequest,
            trust_reference_bytes: bytes,
        ) -> PreparedPlanningCandidate | PlanningOutcome:
            assert not runtime._planning_lane.locked()
            assert runtime._traced_request is sentinel
            assert runtime._trace_principal == "legacy-principal"
            result = await original(command, request, trust_reference_bytes)
            if mutate:
                if mutation == "frontier":
                    await loop.create("other", "Interleaving canonical mutation", BudgetPolicy())
                elif mutation == "run":
                    await loop.step("r", loop.record("r").head)
                else:
                    deadline = EffectPreviewBinding.model_validate_json(
                        display.canonical_command
                    ).valid_until_ns
                    assert isinstance(result, PreparedPlanningCandidate)
                    request_value = json.loads(result.request_bytes)
                    assert request_value["observed_time_ns"] + 5_000_000_000 > deadline
                    monkeypatch.setattr(
                        r16_effects, "time", SimpleNamespace(monotonic_ns=lambda: deadline + 1)
                    )
            assert runtime._traced_request is sentinel
            return result

        monkeypatch.setattr(runtime, "_prepare_planning_request", observed)
        producer = R16EffectsProducer(runtime)
        display = await producer.display_effect("r/turn/1/proposal/effect")
        await producer.adopt_effect(
            "hermetic-ingress", display.display_id, display.display_digest, display.adoption_act_id
        )
        mutate = True
        with pytest.raises(LoopRejected, match=r"changed|expired"):
            await producer.adopt_effect(
                "hermetic-ingress",
                display.display_id,
                display.display_digest,
                display.adoption_act_id,
            )


async def test_materialized_cut_rejects_byte_identical_path_swap_and_old_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    import shutil

    from chiplog.composition.r16_effects import EffectsCurrentWorkerHold
    from chiplog.platform.owner_decision_journal import OwnerJournalSnapshot

    database = tmp_path / "identity.sqlite"
    journal = IndependentOwnerDecisionJournal(
        IndependentTenantDecisionJournal(tmp_path / "identity-owners.journal"), "hermetic-tenant"
    )
    async with open_r13_loop(database, responses=()) as loop:
        created = await loop.create("r", "Worker identity", BudgetPolicy())
        await loop.activate("r", created.head)
        runtime = loop._planning
        assert isinstance(runtime, R13PlanningRuntime)
        assert read_materialized_effects(runtime, journal).worker is None
        original = journal.snapshot
        calls = 0
        backup = tmp_path / "original-inode.sqlite"

        def swap_on_final_snapshot() -> OwnerJournalSnapshot:
            nonlocal calls
            snapshot = original()
            calls += 1
            if calls == 2:
                database.rename(backup)
                shutil.copyfile(backup, database)
                assert database.read_bytes() == backup.read_bytes()
                assert database.stat().st_ino != backup.stat().st_ino
            return snapshot

        monkeypatch.setattr(journal, "snapshot", swap_on_final_snapshot)
        try:
            with pytest.raises(EffectsIntegrityError) as error:
                read_materialized_effects(runtime, journal)
            assert "physical authority database changed" in str(error.value.__cause__)
        finally:
            if backup.exists():
                os.replace(backup, database)
            monkeypatch.setattr(journal, "snapshot", original)
    async with open_r13_loop(database, responses=()) as restarted:
        current = restarted._planning
        assert isinstance(current, R13PlanningRuntime)
        with pytest.raises(EffectsCurrentWorkerHold, match="current non-scheduler"):
            read_materialized_effects(current, journal, run_id="r")


async def test_current_canonical_worker_positive_requires_actual_assembly(tmp_path: Path) -> None:
    """Actual current worker is valid; reopening never adopts the previous worker."""
    database = tmp_path / "current-worker.sqlite"
    async with open_r14_loop(database, responses=()) as loop:
        created = await loop.create("r", "Current worker", BudgetPolicy())
        await loop.activate("r", created.head)
        runtime = loop._planning
        assert isinstance(runtime, R14PlanningRuntime)
        old_worker = runtime.current_worker()
        old_run = loop.record("r")
        assert old_run.worker_session == old_worker
        worker = read_materialized_effects(runtime, runtime._owner_decisions(), run_id="r").worker
        assert worker is not None
        assert worker.fence.run_head == loop.record("r").head
        assert worker.fence.worker_session_id == runtime.current_worker()
    async with open_r14_loop(database, responses=()) as reopened:
        current = reopened._planning
        assert isinstance(current, R14PlanningRuntime)
        assert current.current_worker() != old_worker
        historical = read_materialized_effects(current, current._owner_decisions())
        assert tuple(run for run in historical.latest_runs if run.run_id == "r") == (old_run,)
        with pytest.raises(LoopRejected, match="current authenticated binding differs"):
            reopened.record("r")
        with pytest.raises(EffectsCurrentWorkerHold, match="current non-scheduler"):
            read_materialized_effects(current, current._owner_decisions(), run_id="r")
        created = await reopened.create("new", "New worker", BudgetPolicy())
        await reopened.activate("new", created.head)
        fresh = read_materialized_effects(current, current._owner_decisions(), run_id="new").worker
        assert fresh is not None
        assert reopened.record("new").worker_session == current.current_worker()
        assert fresh.fence.worker_session_id == current.current_worker()
