"""Real authenticated IPC and the canonical writer, without fixture authority."""

import asyncio
import base64
import json
import sqlite3
from pathlib import Path

import pytest

from chiplog.capabilities.agent_loop.application import AgentLoop
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy, LoopRejected, ProposalDisplay
from chiplog.capabilities.effects.contracts import (
    EffectPreparationRequest,
    PublishPlanEffectCommand,
)
from chiplog.composition.r14 import open_r14_loop
from chiplog.composition.r14_runtime import AnchoredOwnerDecisionJournal, R14PlanningRuntime
from chiplog.composition.r16_effects import (
    EffectPreviewBinding,
    HermeticEffectProposal,
    R16EffectsProducer,
)
from chiplog.composition.r16_effects_authority import R16PlanEffectAuthority
from chiplog.platform._owner_publication_contracts import (
    ExactReplayQuery,
    JournalSelectedPublication,
    PlanEffectBatch,
    PublicationRejected,
)
from chiplog.platform._sqlite import PhysicalPublicationCommand, PublicationResult
from chiplog.platform.authority_reads import capture_authority_storage_state
from chiplog.platform.broker import PublicPortCall, PublicPortResult
from chiplog.platform.owner_decision_journal import (
    IndependentOwnerDecisionJournal,
    OwnerJournalIntegrityError,
    OwnerJournalSnapshot,
)
from chiplog.platform.owner_publications import (
    OwnerPublicationUncertain,
    PreparedOwnerPublication,
    SelectedOwnerDecision,
)


def response() -> bytes:
    proposal = HermeticEffectProposal(
        schema_id="chiplog.hermetic-effect-proposal.v1",
        purpose="My exact hermetic action",
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


async def prepare_display(loop: AgentLoop) -> tuple[R14PlanningRuntime, ProposalDisplay]:
    created = await loop.create("r", "Propose my exact hermetic action", BudgetPolicy())
    active = await loop.activate("r", created.head)
    await loop.step("r", active.head)
    runtime = loop._planning
    assert isinstance(runtime, R14PlanningRuntime)
    display = await R16EffectsProducer(runtime).display_effect("r/turn/1/proposal/effect")
    return runtime, display


async def test_actual_plan_effect_commit_and_historical_replay(tmp_path: Path) -> None:
    database = tmp_path / "effects.sqlite"
    async with open_r14_loop(database, responses=(response(),)) as loop:
        runtime, display = await prepare_display(loop)
        result = await runtime.publish_effect(
            "hermetic-ingress", display.display_id, display.display_digest, display.adoption_act_id
        )
        assert isinstance(result, JournalSelectedPublication), result
        assert result.kind == "COMMITTED"
        assert tuple(row.owner for row in result.complete_records) == ("planning",) * 5 + (
            "effects",
        )
        selected = runtime._owner_decisions().lookup(runtime._tenant_id, result.command_id)
        assert selected is not None
        batch = selected.prepared.request
        assert isinstance(batch, PlanEffectBatch)
        binding = EffectPreviewBinding.model_validate_json(display.canonical_command)
        assert batch.planning_command.canonical_bytes == base64.b64decode(
            binding.planning_request_base64
        )
        prepared = EffectPreparationRequest.model_validate_json(
            batch.effects_command.canonical_bytes
        )
        command = PublishPlanEffectCommand.model_validate_json(prepared.command_bytes)
        assert command.intent.payload == b"\xffexact\x00payload"
        assert command.intent.authority.valid_until_ns <= binding.valid_until_ns
        with sqlite3.connect(database) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM records WHERE owner='effects'"
            ).fetchone() == (1,)
        # A newer publication must not be rewound when replaying the earlier effect.
        await loop.create("later", "Advance history", BudgetPolicy())
        anchor = runtime._commitment_journal.load(runtime._tenant_id)
        await asyncio.sleep(5.1)
        replay = await runtime.publish_effect(
            "hermetic-ingress", display.display_id, display.display_digest, display.adoption_act_id
        )
        assert isinstance(replay, JournalSelectedPublication)
        assert replay.kind == "EXACT_REPLAY"
        assert replay.complete_records == result.complete_records
        assert runtime._commitment_journal.load(runtime._tenant_id) == anchor
    async with open_r14_loop(database, responses=()) as loop:
        reopened = loop._planning
        assert isinstance(reopened, R14PlanningRuntime)
        replay = await reopened.publish_effect(
            "hermetic-ingress", display.display_id, display.display_digest, display.adoption_act_id
        )
        assert isinstance(replay, JournalSelectedPublication)
        assert replay.kind == "EXACT_REPLAY"


@pytest.mark.parametrize(
    "damage", ("authenticated-malformed", "body-replaced", "body-corrupted", "head", "key")
)
async def test_repeated_journal_snapshot_reauthenticates_after_semantic_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, damage: str
) -> None:
    async with open_r14_loop(tmp_path / "cached.sqlite", responses=(response(),)) as loop:
        runtime, display = await prepare_display(loop)
        journal = runtime._owner_decisions()
        empty = journal.snapshot()
        result = await runtime.publish_effect(
            "hermetic-ingress", display.display_id, display.display_digest, display.adoption_act_id
        )
        assert isinstance(result, JournalSelectedPublication)
        warm = journal.snapshot()
        assert warm != empty
        assert result.command_id in warm.materialized_command_ids
        actual_entries = journal._raw.entries
        probes = 0

        def fresh_entries() -> tuple[tuple[str, str | None, bytes], ...]:
            nonlocal probes
            probes += 1
            return actual_entries()

        def redundant_decode(operation: str, record: str) -> None:
            raise AssertionError("unchanged authenticated bytes were decoded again")

        with monkeypatch.context() as patch:
            patch.setattr(journal._raw, "entries", fresh_entries)
            patch.setattr(journal, "_scan", redundant_decode)
            assert journal.snapshot() == warm
            assert journal.snapshot() == warm
            assert probes == 2
        raw = journal._raw
        if damage == "authenticated-malformed":
            raw.append(b'{"kind":"UNKNOWN"}', warm.head)
        elif damage == "body-replaced":
            replacement = tmp_path / "replacement"
            replacement.write_bytes(raw._path.read_bytes())
            replacement.replace(raw._path)
        elif damage == "body-corrupted":
            raw._path.write_bytes(raw._path.read_bytes() + b"invalid\n")
        elif damage == "head":
            raw._head_path.write_text("", encoding="ascii")
        else:
            raw._key_path.write_bytes(b"x" * 32)
        with pytest.raises(OwnerJournalIntegrityError):
            journal.snapshot()


async def test_journal_snapshot_rejects_middle_cut_mismatch_before_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    async with open_r14_loop(tmp_path / "middle-cut.sqlite", responses=()) as loop:
        runtime = loop._planning
        assert isinstance(runtime, R14PlanningRuntime)
        journal = AnchoredOwnerDecisionJournal(runtime)
        original = IndependentOwnerDecisionJournal.snapshot

        def middle_cut(self: IndependentOwnerDecisionJournal) -> OwnerJournalSnapshot:
            return replace(original(self), head="different-middle-cut")

        with monkeypatch.context() as patch:
            patch.setattr(IndependentOwnerDecisionJournal, "snapshot", middle_cut)
            with pytest.raises(OwnerJournalIntegrityError):
                journal.snapshot()
        # A failed read did not poison the cache, and authentic empty history works.
        assert journal.snapshot().head is None


@pytest.mark.parametrize("partial_corruption", (False, True))
async def test_selected_before_sql_failure_recovers_only_exact_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    partial_corruption: bool,
) -> None:
    async with open_r14_loop(tmp_path / "recovery.sqlite", responses=(response(),)) as loop:
        runtime, display = await prepare_display(loop)
        journal = runtime._owner_decisions()
        select = journal.select

        def lost_ack(prepared: PreparedOwnerPublication, commitment: str) -> SelectedOwnerDecision:
            select(prepared, commitment)
            raise RuntimeError("lost decision acknowledgment")

        monkeypatch.setattr(journal, "select", lost_ack)
        with pytest.raises(OwnerPublicationUncertain):
            await runtime.publish_effect(
                "hermetic-ingress",
                display.display_id,
                display.display_digest,
                display.adoption_act_id,
            )
        selected = journal.snapshot().decisions[-1]
        with sqlite3.connect(runtime._database) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM records WHERE owner='effects'"
            ).fetchone() == (0,)
        if partial_corruption:
            row = selected.prepared.request.complete_records[0]
            with sqlite3.connect(runtime._database) as connection:
                connection.execute(
                    "INSERT INTO records "
                    "(tenant_id,record_id,owner,schema_id,canonical_bytes,commit_sequence) "
                    "VALUES (?,?,?,?,?,?)",
                    (
                        runtime._tenant_id,
                        row.record_id,
                        row.owner,
                        row.schema_id,
                        row.canonical_bytes,
                        selected.tenant_commit_sequence,
                    ),
                )
        monkeypatch.setattr(journal, "select", select)
        replay = await runtime.publish_effect(
            "hermetic-ingress", display.display_id, display.display_digest, display.adoption_act_id
        )
        if partial_corruption:
            assert isinstance(replay, PublicationRejected)
            assert replay.kind == "INTEGRITY_FAULT"
            with sqlite3.connect(runtime._database) as connection:
                assert connection.execute(
                    "SELECT COUNT(*) FROM records WHERE owner='planning'"
                ).fetchone() == (1,)
            assert selected.prepared.request.identity.command_id not in (
                journal.snapshot().materialized_command_ids
            )
        else:
            assert isinstance(replay, JournalSelectedPublication)
            assert replay.kind == "EXACT_REPLAY"
            assert replay.complete_records == selected.prepared.request.complete_records


@pytest.mark.parametrize("field", ("peer", "digest", "act"))
async def test_changed_adoption_never_publishes(tmp_path: Path, field: str) -> None:
    async with open_r14_loop(tmp_path / "reject.sqlite", responses=(response(),)) as loop:
        runtime, display = await prepare_display(loop)
        with pytest.raises(LoopRejected):
            await runtime.publish_effect(
                "wrong" if field == "peer" else "hermetic-ingress",
                display.display_id,
                "wrong" if field == "digest" else display.display_digest,
                "wrong" if field == "act" else display.adoption_act_id,
            )
        assert not runtime._owner_decisions().snapshot().decisions


async def test_pending_effect_replay_denial_cannot_materialize_then_authorized_replay_recovers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with open_r14_loop(tmp_path / "denied-pending.sqlite", responses=(response(),)) as loop:
        runtime, display = await prepare_display(loop)
        journal = runtime._owner_decisions()
        select = journal.select

        def lost_ack(prepared: PreparedOwnerPublication, commitment: str) -> SelectedOwnerDecision:
            select(prepared, commitment)
            raise RuntimeError("selected before SQL")

        with monkeypatch.context() as patch:
            patch.setattr(journal, "select", lost_ack)
            with pytest.raises(OwnerPublicationUncertain):
                await runtime.publish_effect(
                    "hermetic-ingress",
                    display.display_id,
                    display.display_digest,
                    display.adoption_act_id,
                )
        selected = journal.snapshot().decisions[-1]
        before = capture_authority_storage_state(runtime._database)
        history = journal.snapshot()
        assert runtime._pending_owners() == (selected,)
        authority = R16PlanEffectAuthority(runtime)
        assert authority.materialization_state(selected) == "ABSENT"
        denied_calls = 0

        def deny(authority: R16PlanEffectAuthority, query: ExactReplayQuery) -> PublicationRejected:
            nonlocal denied_calls
            denied_calls += 1
            return PublicationRejected(
                kind="DENIED",
                tenant_id=query.identity.tenant_id,
                command_id=query.identity.command_id,
                reason="current replay invocation denied",
            )

        async def forbidden_submit(command: PhysicalPublicationCommand) -> PublicationResult:
            pytest.fail("denied public replay attempted physical recovery submit")

        with monkeypatch.context() as patch:
            patch.setattr(R16PlanEffectAuthority, "authenticate_replay", deny)
            patch.setattr(runtime._appender, "submit", forbidden_submit)
            result = await runtime.publish_effect(
                "hermetic-ingress",
                display.display_id,
                display.display_digest,
                display.adoption_act_id,
            )
        assert denied_calls == 1
        assert isinstance(result, PublicationRejected) and result.kind == "DENIED"
        assert journal.snapshot() == history
        assert capture_authority_storage_state(runtime._database) == before
        assert authority.materialization_state(selected) == "ABSENT"
        assert (
            journal.lookup(runtime._tenant_id, selected.prepared.request.identity.command_id)
            == selected
        )

        broker = runtime._supervisor.runtime()
        call = broker.call

        async def no_preparation(request: PublicPortCall) -> PublicPortResult:
            if request.operation_id.startswith(("effects.", "planning.")):
                pytest.fail("exact pending recovery invoked semantic owner preparation")
            return await call(request)

        with monkeypatch.context() as patch:
            patch.setattr(broker, "call", no_preparation)
            recovered = await runtime.publish_effect(
                "hermetic-ingress",
                display.display_id,
                display.display_digest,
                display.adoption_act_id,
            )
        assert isinstance(recovered, JournalSelectedPublication)
        assert recovered.kind == "EXACT_REPLAY"
        assert recovered.complete_records == selected.prepared.request.complete_records
        assert authority.materialization_state(selected) == "COMPLETE"
        assert journal.snapshot().decisions == history.decisions


async def test_pending_effect_predecessor_guard_rejects_durable_rival_loop_decision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with open_r14_loop(tmp_path / "rival-pending.sqlite", responses=(response(),)) as loop:
        runtime, display = await prepare_display(loop)
        journal = runtime._owner_decisions()
        select = journal.select

        def lost_ack(prepared: PreparedOwnerPublication, commitment: str) -> SelectedOwnerDecision:
            select(prepared, commitment)
            raise RuntimeError("selected before SQL")

        with monkeypatch.context() as patch:
            patch.setattr(journal, "select", lost_ack)
            with pytest.raises(OwnerPublicationUncertain):
                await runtime.publish_effect(
                    "hermetic-ingress",
                    display.display_id,
                    display.display_digest,
                    display.adoption_act_id,
                )
        selected = journal.snapshot().decisions[-1]
        authority = R16PlanEffectAuthority(runtime)
        assert authority.check_selected_predecessor(selected)
        before = capture_authority_storage_state(runtime._database)
        rival = {"version": 1, "kind": "DECIDED", "operation_id": "rival-loop"}
        runtime._append_decision(rival)
        assert runtime._pending() == (rival,)
        assert runtime._pending_owners() == (selected,)
        assert capture_authority_storage_state(runtime._database) == before
        assert not authority.check_selected_predecessor(selected)
