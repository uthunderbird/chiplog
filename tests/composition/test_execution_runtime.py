"""Real initial execution publication and original-byte recovery through the sole writer."""

import base64
import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy, LoopRejected, ModelAttempt
from chiplog.capabilities.agent_loop.execution_contracts import (
    ExecutionModelAttempt,
    ExecutionPromptArtifact,
    ExecutionRunRecord,
)
from chiplog.capabilities.agent_loop.execution_transition_contracts import (
    ActivateExecutionRun,
    CreateExecutionRun,
    EmitExecutionAttempt,
    ExecutionTransitionRequest,
    StartInitialExecutionTurn,
)
from chiplog.composition.r14_execution_runtime import open_execution_runtime
from chiplog.composition.r14_execution_transition_records import (
    EXECUTION_TRANSITION_OPERATION,
    ExecutionHistorySnapshot,
)
from chiplog.composition.r14_loop_history import read_execution_history
from chiplog.platform._sqlite import PhysicalPublicationCommand, PublicationResult
from chiplog.platform.broker import PublicPortCall, PublicPortResult
from chiplog.platform.r7_runtime import AuthorityBrokerRuntime


async def test_initial_execution_publishes_exact_owner_history_and_reopens(tmp_path: Path) -> None:
    database = tmp_path / "execution.sqlite"
    async with open_execution_runtime(database) as runtime:
        with pytest.raises(LoopRejected, match="unregistered"):
            await runtime.create_execution("foreign", "run", "Plan", BudgetPolicy())
        created = await runtime.create_execution("hermetic-ingress", "run", "Plan", BudgetPolicy())
        started = await runtime.begin_execution("hermetic-ingress", "run", created.head)
        assert started.turns[-1].state == "PREPARING"
        assert started.turns[-1].attempts == ()
        snapshot = read_execution_history(runtime)
        assert tuple(run.event for run in snapshot.records) == (
            "RunCreated",
            "RunActivated",
            "TurnStarted",
        )
        assert all(run.origin == created.origin for run in snapshot.records)
        assert runtime._pending() == ()
        with pytest.raises(LoopRejected):
            await runtime.begin_execution("hermetic-ingress", "run", created.head)
    async with open_execution_runtime(database) as reopened:
        assert read_execution_history(reopened) == snapshot
        assert reopened.current_worker() != created.worker_session
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT canonical_bytes FROM records WHERE owner='agent_loop' ORDER BY commit_sequence"
        ).fetchall()
        assert tuple(row[0] for row in rows) == tuple(
            run.canonical_bytes() for run in snapshot.records
        )


async def test_selected_creation_recovers_without_owner_reissue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "crash.sqlite"
    async with open_execution_runtime(database) as runtime:
        submit = runtime._appender.submit

        async def crash(command: PhysicalPublicationCommand) -> PublicationResult:
            if command.operation_kind == EXECUTION_TRANSITION_OPERATION:
                command = replace(command, fault="before_commit")
            return await submit(command)

        publish = runtime._publish_initial_transition

        async def fault_at_execution_publication(
            peer: str,
            request: CreateExecutionRun | ActivateExecutionRun | StartInitialExecutionTurn,
        ) -> ExecutionRunRecord:
            with monkeypatch.context() as fault:
                fault.setattr(runtime._appender, "submit", crash)
                return await publish(peer, request)

        with monkeypatch.context() as patch:
            patch.setattr(runtime, "_publish_initial_transition", fault_at_execution_publication)
            with pytest.raises(RuntimeError, match="injected fault before commit"):
                await runtime.create_execution("hermetic-ingress", "run", "Plan", BudgetPolicy())
        pending = runtime._pending()
        assert len(pending) == 1
        retained = pending[0]["execution_transition"]
        selected_rows = runtime._publication(pending[0]).records
    call = AuthorityBrokerRuntime.call

    async def no_reissue(self: AuthorityBrokerRuntime, request: PublicPortCall) -> PublicPortResult:
        if request.operation_id == "agent_loop.prepare_execution_transition":
            raise AssertionError("recovery regenerated original owner transition")
        return await call(self, request)

    with monkeypatch.context() as patch:
        patch.setattr(AuthorityBrokerRuntime, "call", no_reissue)
        async with open_execution_runtime(database) as reopened:
            snapshot = read_execution_history(reopened)
            assert len(snapshot.records) == 1
            assert snapshot.records[0].canonical_bytes() == selected_rows[0].canonical_bytes
            assert reopened._pending() == ()
            assert any(
                json.loads(raw).get("execution_transition") == retained
                for _, _, raw in reopened._loop_decisions().entries()
            )


@pytest.mark.parametrize("failure", ["none", "lost_response", "before_commit"])
async def test_model_requires_durable_emission_and_restart_never_reissues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    database = tmp_path / "model.sqlite"
    response = b"\xffexact binary response"
    invoked: list[ModelAttempt | ExecutionModelAttempt] = []
    original_invoke = HermeticModel.invoke

    async def observe(
        model: HermeticModel, attempt: ModelAttempt | ExecutionModelAttempt
    ) -> tuple[bytes, str]:
        with sqlite3.connect(database) as connection:
            rows = connection.execute(
                "SELECT canonical_bytes FROM records WHERE owner='agent_loop' "
                "ORDER BY commit_sequence"
            ).fetchall()
        selected = ExecutionRunRecord.model_validate_json(rows[-1][0])
        assert selected.event == "ModelAttemptEmitted"
        assert selected.turns[-1].attempts[-1] == attempt
        assert attempt.state == "EMITTED_OUTCOME_UNKNOWN"
        invoked.append(attempt)
        result = await original_invoke(model, attempt)
        if failure == "lost_response":
            raise RuntimeError("injected lost model response")
        return result

    monkeypatch.setattr(HermeticModel, "invoke", observe)
    async with open_execution_runtime(database, responses=(response,)) as runtime:
        created = await runtime.create_execution("hermetic-ingress", "run", "Plan", BudgetPolicy())
        started = await runtime.begin_execution("hermetic-ingress", "run", created.head)
        publish = runtime._publish_initial_transition
        submit = runtime._appender.submit

        async def crash(command: PhysicalPublicationCommand) -> PublicationResult:
            return await submit(replace(command, fault="before_commit"))

        async def intercept(
            peer: str,
            request: ExecutionTransitionRequest,
            *,
            expected_snapshot: ExecutionHistorySnapshot | None = None,
        ) -> ExecutionRunRecord:
            with monkeypatch.context() as fault:
                if failure == "before_commit" and isinstance(request, EmitExecutionAttempt):
                    fault.setattr(runtime._appender, "submit", crash)
                return await publish(peer, request, expected_snapshot=expected_snapshot)

        monkeypatch.setattr(runtime, "_publish_initial_transition", intercept)
        if failure == "none":
            captured = await runtime.capture_execution("hermetic-ingress", "run", started.head)
            attempt = captured.turns[-1].attempts[-1]
            assert attempt.state == "RESPONSE_CAPTURED"
            assert base64.b64decode(attempt.response_base64 or "") == response
            assert attempt.receipt is not None
            assert attempt.manifest == invoked[0].manifest
        else:
            with pytest.raises(RuntimeError, match="injected"):
                await runtime.capture_execution("hermetic-ingress", "run", started.head)
        count = len(invoked)
        assert count == (0 if failure == "before_commit" else 1)
    async with open_execution_runtime(database, responses=(response,)) as reopened:
        selected = read_execution_history(reopened).records[-1]
        assert isinstance(selected, ExecutionRunRecord)
        attempt = selected.turns[-1].attempts[-1]
        assert attempt.state == (
            "RESPONSE_CAPTURED" if failure == "none" else "EMITTED_OUTCOME_UNKNOWN"
        )
        assert reopened._pending() == ()
        with pytest.raises(LoopRejected, match="unknown cannot retry"):
            await reopened.capture_execution("hermetic-ingress", "run", selected.head)
        assert len(invoked) == count


async def test_workspace_cut_changed_during_render_never_reaches_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chiplog.adapters.driven.execution_prompts import render_execution_prompt
    from chiplog.composition import r14_execution_runtime

    async with open_execution_runtime(tmp_path / "stale.sqlite", responses=(b"unused",)) as runtime:
        created = await runtime.create_execution("hermetic-ingress", "run", "First", BudgetPolicy())
        other = await runtime.create_execution(
            "hermetic-ingress", "other", "Second", BudgetPolicy()
        )
        started = await runtime.begin_execution("hermetic-ingress", "run", created.head)

        async def advance(context: str) -> ExecutionPromptArtifact:
            artifact = await render_execution_prompt(context)
            await runtime._publish_initial_transition(
                "hermetic-ingress", ActivateExecutionRun(command_id="other-activate", run=other)
            )
            return artifact

        monkeypatch.setattr(r14_execution_runtime, "render_execution_prompt", advance)
        with pytest.raises(LoopRejected, match="visibility source cut changed"):
            await runtime.capture_execution("hermetic-ingress", "run", started.head)
        assert runtime._execution_model.requests == []
        history = read_execution_history(runtime)
        assert [row for row in history.records if row.run_id == "run"][-1] == started
