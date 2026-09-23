"""Cancellation through actual canonical fanout, owner IPC, journal and writer."""

import json
import sqlite3
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Literal, cast

import pytest

from chiplog.adapters.driven.deployment_trust import IndependentTenantDecisionJournal
from chiplog.adapters.driven.loop_sqlite import LoopIntegrityError
from chiplog.capabilities.agent_loop import domain
from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.contracts import (
    BudgetPolicy,
    LoopRejected,
    LoopSnapshot,
    RunRecord,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.composition import r14_cancellation_records
from chiplog.composition.r14 import open_r14_loop
from chiplog.composition.r14_cancellation_contracts import (
    CANCELLATION_OPERATION,
    CANCELLATION_SCHEMA,
    NOT_EXECUTED_SCHEMA,
    CancelCallSubmission,
    RetainedCancellationPreparation,
)
from chiplog.composition.r14_cancellation_records import build_cancellation_envelope
from chiplog.composition.r14_fanout_records import inventory_from_history, reference
from chiplog.composition.r14_loop_history import read_call_history
from chiplog.composition.r14_runtime import R14PlanningRuntime
from chiplog.platform._sqlite import (
    EventAppender,
    LostCommitAcknowledgement,
    PhysicalPublicationCommand,
    PublicationResult,
)
from chiplog.platform.broker import PublicPortCall, PublicPortResult, PublicPortSuccess
from chiplog.platform.r7_runtime import AuthorityBrokerRuntime
from tests.composition.test_fanout_publication import _COMPLETE, _CONTINUE, _capture


def _request(runtime: R14PlanningRuntime) -> CancelCallSubmission:
    snapshot, fanout, _ = read_call_history(runtime)
    run = snapshot.records[-1]
    initialized = fanout[-1].proposal.fan_out.initialized_records[0]
    return CancelCallSubmission(
        act_id="cancel-one",
        original_call_id=initialized.original_call_id,
        initialized=reference(initialized.original_call_id, initialized),
        current_run=CallSubjectHead(
            subject_id=run.run_id,
            revision=Present(head=run.head, fingerprint=run.digest()),
        ),
    )


async def test_atomic_cancellation_exact_replay_and_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "cancel.sqlite"
    async with open_r14_loop(database, responses=(_CONTINUE,)) as loop:
        runtime = cast(R14PlanningRuntime, loop._session)
        accepted, expected = await _capture(loop, monkeypatch)
        await loop._store.publish(accepted, expected)
        submission = _request(runtime)
        committed = await runtime.cancel_call("hermetic-ingress", submission)
        assert committed.state == "ACTIVE"
        outcomes = domain.current_turn(committed).sealed_calls
        assert outcomes is not None and outcomes[0].result is not None
        result = json.loads(outcomes[0].result)
        assert result["kind"] == "CALL_NOT_EXECUTED_RESULT_V1"
        assert result["outcome"] == "NOT_EXECUTED"
        snapshot, fanout, cancellations = read_call_history(runtime)
        assert len(cancellations) == 1
        evidence = cancellations[0]
        inventory = inventory_from_history(runtime._tenant_id, snapshot, fanout, cancellations)
        cancelled = next(
            row
            for row in inventory.ordered_calls
            if row.original_call_id == submission.original_call_id
        )
        assert (
            cancelled.terminal
            == reference(
                evidence.proposal.terminal.terminal_id, evidence.proposal.terminal
            ).revision
        )
        with sqlite3.connect(database) as connection:
            rows = connection.execute(
                "SELECT r.schema_id,r.commit_sequence FROM records r JOIN publications p "
                "ON r.tenant_id=p.tenant_id AND r.commit_sequence=p.commit_sequence "
                "WHERE p.operation_kind=? ORDER BY r.schema_id",
                (CANCELLATION_OPERATION,),
            ).fetchall()
        assert sorted(schema for schema, _ in rows) == sorted(
            (
                "chiplog.agent-loop.record.v1",
                CANCELLATION_SCHEMA,
                NOT_EXECUTED_SCHEMA,
            )
        )
        assert len({sequence for _, sequence in rows}) == 1
        assert await runtime.cancel_call("hermetic-ingress", submission) == committed
        assert runtime._loop_snapshot() == snapshot
        later = domain.terminal_tool(committed, "two", '{"kind":"PROPOSAL_ONLY"}')
        await loop._store.publish(later, snapshot)
        assert await runtime.cancel_call("hermetic-ingress", submission) == committed
        with pytest.raises(LoopRejected, match="replay differs"):
            await runtime.cancel_call(
                "hermetic-ingress", submission.model_copy(update={"original_call_id": "other"})
            )
    async with open_r14_loop(database, responses=()) as reopened:
        runtime = cast(R14PlanningRuntime, reopened._session)
        assert await runtime.cancel_call("hermetic-ingress", submission) == committed
        assert runtime._loop_snapshot().records[-1] == later


async def test_proposal_terminal_wins_and_late_cancellation_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with open_r14_loop(tmp_path / "race.sqlite", responses=(_CONTINUE,)) as loop:
        runtime = cast(R14PlanningRuntime, loop._session)
        accepted, expected = await _capture(loop, monkeypatch)
        await loop._store.publish(accepted, expected)
        submission = _request(runtime)
        snapshot = runtime._loop_snapshot()
        terminal = domain.terminal_tool(accepted, "one", '{"kind":"PROPOSAL_ONLY"}')
        await loop._store.publish(terminal, snapshot)
        before = runtime._loop_snapshot()
        with pytest.raises(LoopRejected, match="stale"):
            await runtime.cancel_call("hermetic-ingress", submission)
        assert runtime._loop_snapshot() == before
        assert read_call_history(runtime)[2] == ()


async def test_unregistered_peer_cannot_lookup_or_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with open_r14_loop(tmp_path / "peer.sqlite", responses=(_CONTINUE,)) as loop:
        runtime = cast(R14PlanningRuntime, loop._session)
        accepted, expected = await _capture(loop, monkeypatch)
        await loop._store.publish(accepted, expected)
        submission = _request(runtime)
        before = runtime._loop_snapshot()
        with pytest.raises(LoopRejected, match="registered hermetic ingress"):
            await runtime.cancel_call("forged-peer", submission)
        assert runtime._loop_snapshot() == before


async def test_rehashed_changed_act_is_rejected_by_original_owner_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with open_r14_loop(tmp_path / "act.sqlite", responses=(_CONTINUE,)) as loop:
        runtime = cast(R14PlanningRuntime, loop._session)
        accepted, expected = await _capture(loop, monkeypatch)
        await loop._store.publish(accepted, expected)
        await runtime.cancel_call("hermetic-ingress", _request(runtime))
        evidence = read_call_history(runtime)[2][0]
        changed = evidence.model_copy(
            update={
                "act": evidence.act.model_copy(
                    update={
                        "submission": evidence.act.submission.model_copy(
                            update={"act_id": "rival"}
                        ),
                    }
                )
            }
        )
        # Re-encoding the complete outer object cannot substitute the selected act.
        changed = RetainedCancellationPreparation.model_validate_json(changed.canonical_bytes())
        with pytest.raises(ValueError, match="original submitted act"):
            build_cancellation_envelope(changed)
        envelope = build_cancellation_envelope(evidence)
        length = len(envelope.canonical_bytes())
        with monkeypatch.context() as patch:
            patch.setattr(r14_cancellation_records, "MAX_CANCELLATION_BYTES", length)
            assert build_cancellation_envelope(evidence) == envelope
            patch.setattr(r14_cancellation_records, "MAX_CANCELLATION_BYTES", length - 1)
            with pytest.raises(ValueError, match="physical byte bound"):
                build_cancellation_envelope(evidence)


async def test_cancellation_wins_inside_real_step_and_next_turn_uses_joined_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with open_r14_loop(tmp_path / "step.sqlite", responses=(_CONTINUE, _COMPLETE)) as loop:
        runtime = cast(R14PlanningRuntime, loop._session)
        created = await loop.create("run", "Plan", BudgetPolicy())
        active = await loop.activate("run", created.head)
        publish = loop._store.publish

        async def cancel_after_seal(
            record: RunRecord,
            expected: LoopSnapshot,
            validate: Callable[[LoopSnapshot], None] | None = None,
        ) -> None:
            await publish(record, expected, validate)
            if record.event == "ModelResponseReceived":
                await runtime.cancel_call("hermetic-ingress", _request(runtime))

        with monkeypatch.context() as patch:
            patch.setattr(loop._store, "publish", cancel_after_seal)
            stepped = await loop.step("run", active.head)
        assert len(read_call_history(runtime)[2]) == 1
        outcomes = domain.current_turn(loop.record("run")).sealed_calls
        assert outcomes is not None and all(row.state == "TERMINAL" for row in outcomes)
        assert outcomes[0].result is not None and '"NOT_EXECUTED"' in outcomes[0].result
        completed = await loop.step("run", stepped.head)
        assert completed.state == "SUCCEEDED"
        assert len(read_call_history(runtime)[2]) == 1


@pytest.mark.parametrize("fault", ("before_commit", "after_commit"))
async def test_pending_cancellation_recovers_original_bytes_without_owner_reissue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: Literal["before_commit", "after_commit"],
) -> None:
    database = tmp_path / "crash.sqlite"
    async with open_r14_loop(database, responses=(_CONTINUE,)) as loop:
        runtime = cast(R14PlanningRuntime, loop._session)
        accepted, expected = await _capture(loop, monkeypatch)
        await loop._store.publish(accepted, expected)
        submission = _request(runtime)
        submit = runtime._appender.submit

        async def crash(command: PhysicalPublicationCommand) -> PublicationResult:
            return await submit(replace(command, fault=fault))

        with monkeypatch.context() as patch:
            patch.setattr(runtime._appender, "submit", crash)
            failure = RuntimeError if fault == "before_commit" else LostCommitAcknowledgement
            with pytest.raises(failure):
                await runtime.cancel_call("hermetic-ingress", submission)
        pending = runtime._pending()
        assert len(pending) == 1
        evidence = RetainedCancellationPreparation.model_validate_json(
            str(pending[0]["cancellation_preparation"])
        )
        with sqlite3.connect(database) as connection:
            count = connection.execute(
                "SELECT count(*) FROM publications WHERE operation_kind=?",
                (CANCELLATION_OPERATION,),
            ).fetchone()
        assert count == ((0,) if fault == "before_commit" else (1,))
    actual_call = AuthorityBrokerRuntime.call

    async def forbid_new_owner(
        self: AuthorityBrokerRuntime,
        sent: PublicPortCall,
    ) -> PublicPortResult:
        assert sent.operation_id != "agent_loop.prepare_pre_accept_cancellation"
        return await actual_call(self, sent)

    with monkeypatch.context() as patch:
        patch.setattr(AuthorityBrokerRuntime, "call", forbid_new_owner)
        async with open_r14_loop(database, responses=()) as reopened:
            runtime = cast(R14PlanningRuntime, reopened._session)
            assert not runtime._pending()
            assert read_call_history(runtime)[2] == (evidence,)
            assert (
                await runtime.cancel_call("hermetic-ingress", submission) == evidence.run_companion
            )


async def test_owner_reply_substitution_and_stale_worker_leave_no_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with open_r14_loop(tmp_path / "changed.sqlite", responses=(_CONTINUE,)) as loop:
        runtime = cast(R14PlanningRuntime, loop._session)
        accepted, expected = await _capture(loop, monkeypatch)
        await loop._store.publish(accepted, expected)
        submission = _request(runtime)
        engine = runtime._supervisor.runtime()
        actual_call = engine.call
        before = runtime._loop_snapshot()
        selected = runtime._loop_decisions().entries()

        async def substitute(sent: PublicPortCall) -> PublicPortResult:
            reply = await actual_call(sent)
            if sent.operation_id == "agent_loop.prepare_pre_accept_cancellation":
                assert isinstance(reply, PublicPortSuccess)
                return reply.model_copy(update={"request_id": "foreign-request"})
            return reply

        with monkeypatch.context() as patch:
            patch.setattr(engine, "call", substitute)
            with pytest.raises(ValueError, match="owner exchange"):
                await runtime.cancel_call("hermetic-ingress", submission)
        assert runtime._loop_snapshot() == before
        assert runtime._loop_decisions().entries() == selected

        async def stale(sent: PublicPortCall) -> PublicPortResult:
            reply = await actual_call(sent)
            if sent.operation_id == "agent_loop.prepare_pre_accept_cancellation":
                monkeypatch.setattr(runtime, "current_worker", lambda: "stale-worker")
            return reply

        with monkeypatch.context() as patch:
            patch.setattr(engine, "call", stale)
            with pytest.raises(LoopRejected, match="STALE"):
                await runtime.cancel_call("hermetic-ingress", submission)
        assert runtime._loop_snapshot() == before
        assert runtime._loop_decisions().entries() == selected


async def test_malformed_pending_act_rejected_before_any_recovery_submission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "bad-pending.sqlite"
    async with open_r14_loop(database, responses=(_CONTINUE,)) as loop:
        runtime = cast(R14PlanningRuntime, loop._session)
        accepted, expected = await _capture(loop, monkeypatch)
        await loop._store.publish(accepted, expected)
        submit = runtime._appender.submit

        async def crash(command: PhysicalPublicationCommand) -> PublicationResult:
            return await submit(replace(command, fault="before_commit"))

        with monkeypatch.context() as patch:
            patch.setattr(runtime._appender, "submit", crash)
            with pytest.raises(RuntimeError, match="injected fault before commit"):
                await runtime.cancel_call("hermetic-ingress", _request(runtime))
        assert len(runtime._pending()) == 1
    actual_entries = IndependentTenantDecisionJournal.entries

    def corrupt_decoded_selection(
        self: IndependentTenantDecisionJournal,
    ) -> tuple[tuple[str, str | None, bytes], ...]:
        # Inject after the authenticated journal interface to reach the semantic
        # startup validator; this is not a claim of defeating the journal MAC.
        rows = []
        for identity, predecessor, raw in actual_entries(self):
            entry = json.loads(raw)
            if isinstance(entry, dict) and entry.get("operation_kind") == CANCELLATION_OPERATION:
                retained = json.loads(entry["cancellation_preparation"])
                del retained["act"]
                entry["cancellation_preparation"] = json.dumps(retained)
                raw = json.dumps(entry, sort_keys=True, separators=(",", ":")).encode()
            rows.append((identity, predecessor, raw))
        return tuple(rows)

    async def forbidden_submit(
        self: EventAppender,
        command: PhysicalPublicationCommand,
    ) -> PublicationResult:
        raise AssertionError("malformed retained cancellation reached physical recovery")

    with monkeypatch.context() as patch:
        patch.setattr(IndependentTenantDecisionJournal, "entries", corrupt_decoded_selection)
        patch.setattr(EventAppender, "submit", forbidden_submit)
        with pytest.raises(LoopIntegrityError):
            async with open_r14_loop(database, responses=()):
                raise AssertionError("malformed pending act accepted by startup")
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT count(*) FROM publications WHERE operation_kind=?",
            (CANCELLATION_OPERATION,),
        ).fetchone() == (0,)
