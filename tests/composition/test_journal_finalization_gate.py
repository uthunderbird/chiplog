"""Real selected publications survive stale finalizers and competing processes."""

import asyncio
import hashlib
import hmac
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass, replace
from multiprocessing import get_context
from pathlib import Path
from subprocess import Popen, TimeoutExpired, run

import pytest

from chiplog.adapters.driven.deployment_trust import IndependentTenantDecisionJournal
from chiplog.capabilities.planning import CreateIntentionLine
from chiplog.composition.r8 import R8_SURFACES, command_bytes
from chiplog.composition.r14_runtime import R14PlanningRuntime, open_r14_runtime
from chiplog.domain_primitives import RecordId, TenantId
from chiplog.platform._sqlite import PhysicalPublicationCommand, PhysicalRecord, PublicationResult
from chiplog.platform.authority_reads import capture_authority_storage_state
from chiplog.platform.owner_publications import SelectedOwnerDecision
from chiplog.platform.r8_gate import BrokerDeploymentGate
from chiplog.platform.workspace_snapshot import read_connection
from tests.composition.test_owner_runtime_recovery import _mechanical_preparation
from tests.platform.test_owner_journal_gate import _WORKER
from tests.support.deployment_gate import KEY, entitlement, signature
from tests.support.deployment_gate import request as gate_request


def _later_command(runtime: R14PlanningRuntime, identity: str) -> PhysicalPublicationCommand:
    with read_connection(runtime._database) as connection:
        row = connection.execute(
            "SELECT head FROM tenant_heads WHERE tenant_id=?", (runtime._tenant_id,)
        ).fetchone()
    payload = identity.encode()
    fingerprint = hashlib.sha256(payload).hexdigest()
    return PhysicalPublicationCommand(
        tenant_id=runtime._tenant_id,
        operation_kind="workspace.policy",
        idempotency_key=identity,
        request_fingerprint=fingerprint,
        expected_head=0 if row is None else row[0],
        fence_generation="r6",
        expected_fence_frontier=0,
        minimum_fence_frontier=0,
        records=(
            PhysicalRecord(
                identity, "workspace_policy", "chiplog.workspace.policy.v1", payload, fingerprint
            ),
        ),
    )


@dataclass(frozen=True)
class _Pending:
    command: PhysicalPublicationCommand
    predecessor: str
    resulting: str
    completed: Callable[[], bool]
    finish: Callable[[], None]


async def _select_gate(runtime: R14PlanningRuntime) -> _Pending:
    database = runtime._database
    authority = runtime._authority_gate()
    gate = BrokerDeploymentGate(
        database.with_suffix(database.suffix + ".gate.sqlite3"),
        tenant_id=runtime._tenant_id,
        surfaces=R8_SURFACES,
        authenticate=lambda payload, proof: hmac.compare_digest(
            hmac.digest(KEY, payload, "sha256"), proof
        ),
        journal=IndependentTenantDecisionJournal.for_authority_bundle(
            database.with_suffix(database.suffix + ".gate-journal"), authority_gate=authority
        ),
        authority_gate=authority,
        clock=lambda: 10,
    )
    value = entitlement()
    value = value.model_copy(
        update={
            "generation": value.generation.model_copy(update={"tenant_id": runtime._tenant_id}),
            "bounds": value.bounds.model_copy(
                update={"capability_id": "planning.create_intention_line"}
            ),
        }
    )
    assert gate.import_current(value, signature(value), expected=None)
    runtime._gate = gate
    tenant = TenantId(runtime._tenant_id)
    command = CreateIntentionLine(
        RecordId(tenant, "gate-command"),
        RecordId(tenant, "line"),
        RecordId(tenant, "revision"),
        "recovery fixture",
        "act",
    )
    attempt = gate_request(value, "gate-command").model_copy(
        update={
            "surface_id": "planning.create",
            "payload_digest": hashlib.sha256(command_bytes(command)).hexdigest(),
        }
    )
    submit = runtime._appender.submit
    captured: list[PhysicalPublicationCommand] = []

    async def fail_before_commit(physical: PhysicalPublicationCommand) -> PublicationResult:
        captured.append(physical)
        return await submit(replace(physical, fault="before_commit"))

    with pytest.MonkeyPatch.context() as context:
        context.setattr(runtime._appender, "submit", fail_before_commit)
        with pytest.raises(RuntimeError, match="injected fault before commit"):
            await runtime.create(
                principal_id="hermetic-principal",
                credential_id="hermetic-credential",
                session_id="hermetic-session",
                command=command,
                gate_request=attempt,
            )
    ((identity, execution),) = runtime._pending_gate_publications()
    entry = json.loads(execution)
    return _Pending(
        replace(captured[0], admission_guard=None, decision_guard=None),
        entry["predecessor"],
        entry["resulting"],
        lambda: runtime._gate_decision_materialized(identity, execution),
        lambda: runtime._finish_gate_decision(identity, execution),
    )


async def _select(runtime: R14PlanningRuntime, family: str) -> _Pending:
    if family == "gate":
        return await _select_gate(runtime)
    if family == "owner":
        prepared = _mechanical_preparation(runtime)
        decisions: list[SelectedOwnerDecision] = []

        def select(resulting: str) -> None:
            decisions.append(runtime._owner_decisions().select(prepared, resulting))

        request = prepared.request
        command = PhysicalPublicationCommand(
            tenant_id=runtime._tenant_id,
            operation_kind=request.operation,
            idempotency_key=request.identity.command_id,
            request_fingerprint=request.identity.command_fingerprint,
            expected_head=request.expected.tenant_frontier,
            fence_generation="r6",
            expected_fence_frontier=0,
            minimum_fence_frontier=0,
            records=tuple(
                PhysicalRecord(
                    row.record_id, row.owner, row.schema_id, row.canonical_bytes, row.fingerprint
                )
                for row in request.complete_records
            ),
            decision_guard=select,
        )
    else:
        command = _later_command(runtime, "old-loop")
    with pytest.raises(RuntimeError, match="injected fault before commit"):
        await runtime._appender.submit(replace(command, fault="before_commit"))
    if family == "owner":
        decision = decisions[0]
        return _Pending(
            runtime._owner_command(decision),
            prepared.predecessor_commitment,
            decision.resulting_commitment,
            lambda: runtime._owner_decision_materialized(decision),
            lambda: runtime._owner_decisions().materialized(decision),
        )
    (entry,) = runtime._pending()
    identity = str(entry["operation_id"])
    return _Pending(
        runtime._publication(entry),
        str(entry["predecessor"]),
        str(entry["resulting"]),
        lambda: runtime._loop_decision_materialized(identity, entry),
        lambda: runtime._finish_decision(identity, expected=entry),
    )


_ADVANCE = """
import asyncio, sys
from pathlib import Path
from chiplog.composition.r14_runtime import open_r14_runtime
from tests.composition.test_journal_finalization_gate import _later_command
async def main():
    async with open_r14_runtime(Path(sys.argv[1])) as runtime:
        result = await runtime._appender.submit(_later_command(runtime, 'newer-loop'))
        assert result.disposition == 'COMMITTED', result
        runtime._require_no_pending()
asyncio.run(main())
"""


@pytest.mark.parametrize("family", ["owner", "loop", "gate"])
async def test_stale_exact_recovery_preserves_newer_process_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, family: str
) -> None:
    database = tmp_path / "main.sqlite3"
    async with open_r14_runtime(database) as runtime:
        selected = await _select(runtime, family)
        submit = runtime._appender.submit
        advanced = False

        async def pause_then_submit(command: PhysicalPublicationCommand) -> PublicationResult:
            nonlocal advanced
            # Recovery has captured its predecessor but has not entered the writer.
            # A separate canonical runtime recovers that exact decision, then advances.
            result = await asyncio.to_thread(
                run,
                [sys.executable, "-c", _ADVANCE, str(database)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            advanced = True
            return await submit(command)

        with monkeypatch.context() as context:
            context.setattr(runtime._appender, "submit", pause_then_submit)
            await runtime._recover_exact(
                selected.command,
                selected.predecessor,
                selected.resulting,
                is_materialized=selected.completed,
            )
        assert advanced and selected.completed()
        actual, _ = capture_authority_storage_state(database)
        assert actual != selected.resulting
        owner_before = runtime._owner_decisions().snapshot()
        loop_before = runtime._loop_decisions().entries()
        assert runtime._gate is not None and runtime._gate._journal is not None
        gate_before = runtime._gate._journal.entries()
        selected.finish()
        assert runtime._owner_decisions().snapshot() == owner_before
        assert runtime._loop_decisions().entries() == loop_before
        assert runtime._gate._journal.entries() == gate_before
        assert runtime._commitment_journal.load(runtime._tenant_id) == actual
        assert runtime._read_ledger.current_state(
            runtime._tenant_id
        ).materialization_commitment == (actual)


@pytest.mark.parametrize("family", ["owner", "loop", "gate"])
async def test_anchor_and_marker_are_one_process_exclusion_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, family: str
) -> None:
    database = tmp_path / "main.sqlite3"
    async with open_r14_runtime(database) as runtime:
        selected = await _select(runtime, family)
        await runtime._recover_exact(
            selected.command,
            selected.predecessor,
            selected.resulting,
            is_materialized=selected.completed,
        )
        parent, child = get_context("spawn").Pipe()
        process = await asyncio.to_thread(
            Popen,
            [sys.executable, "-c", _WORKER, str(tmp_path), str(child.fileno())],
            pass_fds=(child.fileno(),),
        )
        child.close()
        commit = runtime._commitment_journal.commit
        visited = False

        def paused_commit(tenant_id: str, commitment: str) -> None:
            nonlocal visited
            commit(tenant_id, commitment)
            visited = True
            assert not selected.completed(), "marker unexpectedly precedes anchor"
            parent.send("run")
            assert parent.poll(5) and parent.recv() == "attempting"
            assert not parent.poll(0.2), "contender acquired gate between anchor and marker"

        try:
            assert parent.poll(10) and parent.recv() == "ready"
            with monkeypatch.context() as context:
                context.setattr(runtime._commitment_journal, "commit", paused_commit)
                selected.finish()
            assert visited and selected.completed()
            assert parent.poll(10) and parent.recv() == "acquired"
            process.wait(5)
            assert process.returncode == 0
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(5)
            except TimeoutExpired:
                process.kill()
                process.wait(5)
            parent.close()


async def test_retained_planning_result_cannot_anchor_unknown_newer_contents(
    tmp_path: Path,
) -> None:
    database = tmp_path / "main.sqlite3"
    async with open_r14_runtime(database) as runtime:
        selected = await _select(runtime, "loop")
        await runtime._recover_exact(
            selected.command,
            selected.predecessor,
            selected.resulting,
            is_materialized=selected.completed,
        )
        selected.finish()
        newer = await _select(runtime, "owner")
        await runtime._recover_exact(
            newer.command,
            newer.predecessor,
            newer.resulting,
            is_materialized=newer.completed,
        )
        assert capture_authority_storage_state(database)[0] == newer.resulting
        assert runtime._commitment_journal.load(runtime._tenant_id) == selected.resulting
        with pytest.raises(RuntimeError, match="requires materialization recovery"):
            runtime._finalize_planning_commitment(selected.predecessor, selected.resulting)
        assert runtime._commitment_journal.load(runtime._tenant_id) == selected.resulting
        assert not newer.completed()
        newer.finish()


async def test_gate_finalizer_rejects_changed_exact_execution(tmp_path: Path) -> None:
    async with open_r14_runtime(tmp_path / "main.sqlite3") as runtime:
        selected = await _select(runtime, "gate")
        await runtime._recover_exact(
            selected.command,
            selected.predecessor,
            selected.resulting,
            is_materialized=selected.completed,
        )
        ((identity, execution),) = runtime._pending_gate_publications()
        assert runtime._gate is not None and runtime._gate._journal is not None
        before = runtime._gate._journal.entries()
        with pytest.raises(RuntimeError, match="missing or changed exact gate decision history"):
            runtime._finish_gate_decision(identity, execution + b" ")
        assert runtime._gate._journal.entries() == before
        assert runtime._commitment_journal.load(runtime._tenant_id) == selected.predecessor
        selected.finish()
        with pytest.raises(RuntimeError, match="missing or changed exact gate decision history"):
            runtime._finish_gate_decision(identity, execution + b" ")
