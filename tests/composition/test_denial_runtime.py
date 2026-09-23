"""Authenticated denying publication, historical replay and corrupted selected inputs."""

import asyncio
import base64
import json
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest

from chiplog.capabilities.agent_loop.application import AgentLoop
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy, LoopRejected
from chiplog.capabilities.effects.contracts import EffectRecord, ExactHead
from chiplog.capabilities.effects.denial_contracts import DenialPreparationRequest
from chiplog.composition.r14_runtime import R14PlanningRuntime
from chiplog.composition.r16_denial_authority import DenialAuthority
from chiplog.composition.r16_denial_history import validate_selected_denials
from chiplog.composition.r16_denial_inputs import validate_retained_denial_sources
from chiplog.composition.r16_denial_publication import open_r16_loop
from chiplog.composition.r16_denial_registry import DenialIngress
from chiplog.composition.r16_effects import HermeticEffectProposal, R16EffectsProducer
from chiplog.composition.r16_effects_inputs import canonical, digest, reference
from chiplog.platform._owner_publication_contracts import (
    BrokerPublicationResult,
    ExactReplayQuery,
    JournalSelectedPublication,
    PublicationRejected,
    RegisteredPublication,
    SingleOwnerBatch,
)
from chiplog.platform._sqlite import PhysicalPublicationCommand, PublicationResult
from chiplog.platform.authority_reads import capture_authority_storage_state
from chiplog.platform.broker import PublicPortCall, PublicPortResult
from chiplog.platform.owner_decision_journal import OwnerJournalIntegrityError
from chiplog.platform.owner_publications import (
    BrokerPublicationCoordinator,
    OwnerPublicationUncertain,
    PreparedOwnerPublication,
    SelectedOwnerDecision,
)


def _response() -> bytes:
    proposal = HermeticEffectProposal(
        schema_id="chiplog.hermetic-effect-proposal.v1",
        purpose="My cancellable action",
        payload_base64=base64.b64encode(b"exact payload").decode(),
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


async def _effect(loop: AgentLoop, run_id: str = "r") -> tuple[R14PlanningRuntime, EffectRecord]:
    created = await loop.create(run_id, "Prepare my action for " + run_id, BudgetPolicy())
    active = await loop.activate(run_id, created.head)
    await loop.step(run_id, active.head)
    runtime = loop._planning
    assert isinstance(runtime, R14PlanningRuntime)
    display = await R16EffectsProducer(runtime).display_effect(run_id + "/turn/1/proposal/effect")
    result = await runtime.publish_effect(
        "hermetic-ingress", display.display_id, display.display_digest, display.adoption_act_id
    )
    assert isinstance(result, JournalSelectedPublication), result
    return runtime, EffectRecord.model_validate_json(result.complete_records[-1].canonical_bytes)


def _ingress(
    record: EffectRecord,
    disposition: Literal[
        "HELD_BEFORE_SEND", "CANCELLED_BEFORE_SEND", "SUPERSEDED_BEFORE_SEND"
    ] = "CANCELLED_BEFORE_SEND",
    successor: ExactHead | None = None,
) -> DenialIngress:
    return DenialIngress(
        schema_id="chiplog.hermetic-denial-ingress.v1",
        act_id="cancel-act",
        intent_id=record.snapshot.intent.intent_id,
        expected_attempt=record.snapshot.attempt,
        disposition=disposition,
        successor=successor,
    )


@pytest.mark.parametrize("disposition", ("HELD_BEFORE_SEND", "CANCELLED_BEFORE_SEND"))
async def test_denial_commits_and_replays_after_restart(
    tmp_path: Path,
    disposition: Literal["HELD_BEFORE_SEND", "CANCELLED_BEFORE_SEND"],
) -> None:
    database = tmp_path / "denial.sqlite"
    async with open_r16_loop(database, responses=(_response(),)) as loop:
        runtime, original = await _effect(loop)
        ingress = _ingress(original, disposition)
        result = await runtime.dispose_effect("hermetic-ingress", "r", ingress)
        assert isinstance(result, JournalSelectedPublication), result
        assert result.kind == "COMMITTED"
        record = EffectRecord.model_validate_json(result.complete_records[0].canonical_bytes)
        assert record.snapshot.state == disposition
        assert record.snapshot.intent == original.snapshot.intent
        assert not record.snapshot.transmissions
        conflict = await runtime.dispose_effect(
            "hermetic-ingress",
            "r",
            ingress.model_copy(update={"expected_attempt": record.snapshot.attempt}),
        )
        assert isinstance(conflict, PublicationRejected) and conflict.kind == "CONFLICT"
    async with open_r16_loop(database, responses=()) as loop:
        reopened = loop._planning
        assert isinstance(reopened, R14PlanningRuntime)
        replay = await reopened.dispose_effect("hermetic-ingress", "no-current-run", ingress)
        assert isinstance(replay, JournalSelectedPublication), replay
        assert replay.kind == "EXACT_REPLAY"
        assert replay.complete_records == result.complete_records


async def test_fresh_denial_does_not_renew_expired_original_send(tmp_path: Path) -> None:
    async with open_r16_loop(tmp_path / "expired.sqlite", responses=(_response(),)) as loop:
        runtime, original = await _effect(loop)
        await asyncio.sleep(5.1)
        result = await runtime.dispose_effect("hermetic-ingress", "r", _ingress(original))
        assert isinstance(result, JournalSelectedPublication), result
        record = EffectRecord.model_validate_json(result.complete_records[0].canonical_bytes)
        assert record.snapshot.intent.authority == original.snapshot.intent.authority
        assert not record.snapshot.transmissions


@pytest.mark.parametrize("alias", ("operation", "schema", "nonobject_output"))
async def test_selected_denial_envelope_alias_cannot_skip_history(
    tmp_path: Path,
    alias: str,
) -> None:
    async with open_r16_loop(tmp_path / "alias.sqlite", responses=(_response(),)) as loop:
        runtime, original = await _effect(loop)
        result = await runtime.dispose_effect("hermetic-ingress", "r", _ingress(original))
        assert isinstance(result, JournalSelectedPublication), result
        history = runtime._owner_decisions().snapshot()
        selected = history.decisions[-1]
        batch = selected.prepared.request
        assert isinstance(batch, SingleOwnerBatch)
        changed = (
            batch.model_copy(update={"operation": "effects.publish_plan_effect"})
            if alias == "operation"
            else batch.model_copy(
                update={
                    "command": batch.command.model_copy(
                        update={"schema_id": "chiplog.effects.prepare.v1"}
                    )
                }
            )
        )
        if alias == "nonobject_output":
            raw = b"[]"
            row = batch.complete_records[0].model_copy(
                update={
                    "canonical_bytes": raw,
                    "fingerprint": digest(raw),
                }
            )
            command = batch.command.model_copy(
                update={
                    "schema_id": "fixture.v1",
                    "canonical_bytes": b"{}",
                    "fingerprint": digest(b"{}"),
                }
            )
            changed = batch.model_copy(
                update={
                    "operation": "effects.authorize",
                    "command": command,
                    "complete_records": (row,),
                    "complete_batch_fingerprint": digest(canonical((row,))),
                }
            )
        mutant = replace(
            history,
            decisions=(
                *history.decisions[:-1],
                replace(selected, prepared=replace(selected.prepared, request=changed)),
            ),
        )
        with pytest.raises(OwnerJournalIntegrityError):
            validate_selected_denials(mutant)


async def test_source_change_during_owner_ipc_prevents_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with open_r16_loop(tmp_path / "race.sqlite", responses=(_response(),)) as loop:
        runtime, original = await _effect(loop)
        broker = runtime._supervisor.runtime()
        call_original = broker.call

        async def call(request: PublicPortCall) -> PublicPortResult:
            result = await call_original(request)
            if request.operation_id == "effects.prepare_denial":
                await loop.create("interleaved", "Change source history", BudgetPolicy())
            return result

        monkeypatch.setattr(broker, "call", call)
        with pytest.raises(LoopRejected, match="changed"):
            await runtime.dispose_effect("hermetic-ingress", "r", _ingress(original))
        assert not any(
            d.prepared.request.operation == "effects.before_send"
            for d in runtime._owner_decisions().snapshot().decisions
        )


async def test_selected_denial_recovers_exact_owner_bytes_on_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "recovery.sqlite"
    async with open_r16_loop(database, responses=(_response(),)) as loop:
        runtime, original = await _effect(loop)
        ingress = _ingress(original)
        journal = runtime._owner_decisions()
        select = journal.select

        def lost_ack(prepared: PreparedOwnerPublication, commitment: str) -> SelectedOwnerDecision:
            select(prepared, commitment)
            raise RuntimeError("lost acknowledgment")

        monkeypatch.setattr(journal, "select", lost_ack)
        with pytest.raises(OwnerPublicationUncertain):
            await runtime.dispose_effect("hermetic-ingress", "r", ingress)
        selected = journal.snapshot().decisions[-1]
    async with open_r16_loop(database, responses=()) as loop:
        reopened = loop._planning
        assert isinstance(reopened, R14PlanningRuntime)
        result = await reopened.dispose_effect("hermetic-ingress", "absent", ingress)
        assert isinstance(result, JournalSelectedPublication), result
        assert result.complete_records == selected.prepared.request.complete_records


async def test_supersession_retains_exact_independently_adopted_successor(tmp_path: Path) -> None:
    async with open_r16_loop(
        tmp_path / "supersede.sqlite", responses=(_response(), _response())
    ) as loop:
        runtime, original = await _effect(loop)
        _, successor = await _effect(loop, "successor")
        intent = successor.snapshot.intent
        exact = ExactHead(
            subject_id=intent.intent_id,
            head=intent.intent_id + "/" + intent.fingerprint,
            fingerprint=intent.fingerprint,
        )
        result = await runtime.dispose_effect(
            "hermetic-ingress", "successor", _ingress(original, "SUPERSEDED_BEFORE_SEND", exact)
        )
        assert isinstance(result, JournalSelectedPublication), result
        record = EffectRecord.model_validate_json(result.complete_records[0].canonical_bytes)
        assert record.snapshot.state == "SUPERSEDED_BEFORE_SEND"
        assert record.snapshot.intent == original.snapshot.intent
        assert not record.snapshot.transmissions


async def test_new_generation_can_cancel_old_intent_with_new_run(tmp_path: Path) -> None:
    database = tmp_path / "new-generation.sqlite"
    async with open_r16_loop(database, responses=(_response(),)) as loop:
        _, original = await _effect(loop)
    async with open_r16_loop(database, responses=()) as loop:
        runtime = loop._planning
        assert isinstance(runtime, R14PlanningRuntime)
        created = await loop.create("new-worker", "Cancel my old intent", BudgetPolicy())
        await loop.activate("new-worker", created.head)
        result = await runtime.dispose_effect("hermetic-ingress", "new-worker", _ingress(original))
        assert isinstance(result, JournalSelectedPublication), result
        assert result.kind == "COMMITTED"


@pytest.mark.parametrize("invalid", ("peer", "run", "attempt"))
async def test_unregistered_peer_worker_or_attempt_cannot_publish(
    tmp_path: Path,
    invalid: str,
) -> None:
    async with open_r16_loop(tmp_path / "invalid.sqlite", responses=(_response(),)) as loop:
        runtime, original = await _effect(loop)
        ingress = _ingress(original)
        if invalid == "attempt":
            ingress = ingress.model_copy(update={"expected_attempt": original.record})
        with pytest.raises((LoopRejected, RuntimeError)):
            await runtime.dispose_effect(
                "foreign" if invalid == "peer" else "hermetic-ingress",
                "absent" if invalid == "run" else "r",
                ingress,
            )
        assert not any(
            d.prepared.request.operation == "effects.before_send"
            for d in runtime._owner_decisions().snapshot().decisions
        )


@pytest.mark.parametrize("role", ("clock", "principal_rights", "effects_history"))
async def test_rehashed_historical_source_must_reproduce_original_semantics(
    tmp_path: Path,
    role: str,
) -> None:
    async with open_r16_loop(tmp_path / "source.sqlite", responses=(_response(),)) as loop:
        runtime, original = await _effect(loop)
        result = await runtime.dispose_effect("hermetic-ingress", "r", _ingress(original))
        assert isinstance(result, JournalSelectedPublication), result
        batch = runtime._owner_decisions().snapshot().decisions[-1].prepared.request
        assert isinstance(batch, SingleOwnerBatch)
        request = DenialPreparationRequest.model_validate_json(batch.command.canonical_bytes)
        validate_retained_denial_sources(request)
        authority = request.command.authority
        source = getattr(authority.sources, role)
        value = json.loads(source.canonical_value)
        if role == "clock":
            value["observed_ns"] += 1
        elif role == "principal_rights":
            value[1]["fingerprint"] = "0" * 64
        else:
            value["worker"]["fingerprint"] = "0" * 64
        raw = canonical(value)
        source = source.model_copy(
            update={
                "canonical_value": raw,
                "head": reference(source.source_id, raw),
            }
        )
        authority = authority.model_copy(
            update={
                "sources": authority.sources.model_copy(update={role: source}),
            }
        )
        mutant = request.model_copy(
            update={
                "command": request.command.model_copy(update={"authority": authority}),
                "current": request.current.model_copy(update={"authority": authority}),
            }
        )
        with pytest.raises(ValueError, match=r"worker|source payloads"):
            validate_retained_denial_sources(mutant)


async def test_equal_but_unissued_batch_object_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with open_r16_loop(tmp_path / "clone.sqlite", responses=(_response(),)) as loop:
        runtime, original = await _effect(loop)
        commit_original = BrokerPublicationCoordinator.commit

        async def commit(
            coordinator: BrokerPublicationCoordinator,
            request: RegisteredPublication,
        ) -> BrokerPublicationResult:
            return await commit_original(coordinator, request.model_copy())

        monkeypatch.setattr(BrokerPublicationCoordinator, "commit", commit)
        result = await runtime.dispose_effect("hermetic-ingress", "r", _ingress(original))
        assert isinstance(result, PublicationRejected) and result.kind == "DENIED"
        assert not any(
            d.prepared.request.operation == "effects.before_send"
            for d in runtime._owner_decisions().snapshot().decisions
        )


async def test_writer_rechecks_generation_after_issuance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with open_r16_loop(tmp_path / "writer.sqlite", responses=(_response(),)) as loop:
        runtime, original = await _effect(loop)
        submit_original = runtime._appender.submit
        mutated = False

        async def submit(command: PhysicalPublicationCommand) -> PublicationResult:
            nonlocal mutated
            if command.operation_kind == "effects.before_send":
                with runtime._authority_gate().hold():
                    ledger = runtime._read_ledger
                    state = ledger.current_state(runtime._tenant_id)
                    ledger.start_owner_drain(runtime._tenant_id, state.fingerprint())
                    mutated = True
            return await submit_original(command)

        monkeypatch.setattr(runtime._appender, "submit", submit)
        result = await runtime.dispose_effect("hermetic-ingress", "r", _ingress(original))
        assert mutated
        assert isinstance(result, PublicationRejected) and result.kind in {"STALE", "DENIED"}
        assert not any(
            d.prepared.request.operation == "effects.before_send"
            for d in runtime._owner_decisions().snapshot().decisions
        )


async def test_pending_denial_replay_denied_cannot_materialize_then_authorized_replay_recovers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with open_r16_loop(tmp_path / "denied-pending.sqlite", responses=(_response(),)) as loop:
        runtime, original = await _effect(loop)
        ingress = _ingress(original)
        journal = runtime._owner_decisions()
        select = journal.select

        def lost_ack(prepared: PreparedOwnerPublication, commitment: str) -> SelectedOwnerDecision:
            select(prepared, commitment)
            raise RuntimeError("selected before SQL")

        with monkeypatch.context() as patch:
            patch.setattr(journal, "select", lost_ack)
            with pytest.raises(OwnerPublicationUncertain):
                await runtime.dispose_effect("hermetic-ingress", "r", ingress)
        selected = journal.snapshot().decisions[-1]
        before = capture_authority_storage_state(runtime._database)
        history = journal.snapshot()
        assert runtime._pending_owners() == (selected,)
        authority = DenialAuthority(runtime)
        assert authority.materialization_state(selected) == "ABSENT"
        denied_calls = 0

        def deny(authority: DenialAuthority, query: ExactReplayQuery) -> PublicationRejected:
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
            patch.setattr(DenialAuthority, "authenticate_replay", deny)
            patch.setattr(runtime._appender, "submit", forbidden_submit)
            result = await runtime.dispose_effect("hermetic-ingress", "r", ingress)
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
            recovered = await runtime.dispose_effect("hermetic-ingress", "r", ingress)
        assert isinstance(recovered, JournalSelectedPublication)
        assert recovered.kind == "EXACT_REPLAY"
        assert recovered.complete_records == selected.prepared.request.complete_records
        assert authority.materialization_state(selected) == "COMPLETE"
        assert journal.snapshot().decisions == history.decisions


async def test_pending_denial_predecessor_guard_rejects_durable_rival_loop_decision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with open_r16_loop(tmp_path / "rival-pending.sqlite", responses=(_response(),)) as loop:
        runtime, original = await _effect(loop)
        ingress = _ingress(original)
        journal = runtime._owner_decisions()
        select = journal.select

        def lost_ack(prepared: PreparedOwnerPublication, commitment: str) -> SelectedOwnerDecision:
            select(prepared, commitment)
            raise RuntimeError("selected before SQL")

        with monkeypatch.context() as patch:
            patch.setattr(journal, "select", lost_ack)
            with pytest.raises(OwnerPublicationUncertain):
                await runtime.dispose_effect("hermetic-ingress", "r", ingress)
        selected = journal.snapshot().decisions[-1]
        authority = DenialAuthority(runtime)
        assert authority.check_selected_predecessor(selected)
        before = capture_authority_storage_state(runtime._database)
        rival = {"version": 1, "kind": "DECIDED", "operation_id": "rival-loop"}
        runtime._append_decision(rival)
        assert runtime._pending() == (rival,)
        assert runtime._pending_owners() == (selected,)
        assert capture_authority_storage_state(runtime._database) == before
        assert not authority.check_selected_predecessor(selected)
