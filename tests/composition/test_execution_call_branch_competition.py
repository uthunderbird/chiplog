"""Mounted competition between consequential acceptance and pre-accept cancellation."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import sqlite3
import struct
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from chiplog.adapters.driven.effects_broker import EffectsIntegrityError
from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.execution_contracts import (
    ConsequentialToolCall,
    ExecutionContinue,
    SelfEffectArguments,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Absent, Present
from chiplog.capabilities.deployment_trust.cli_custody_contracts import (
    CliCustodyOffer,
    CliCustodyResponse,
)
from chiplog.composition.common_cli_execution_runtime import (
    R17_RETAINED_READER_ID,
    CommonCliExecutionRuntime,
    open_common_cli_execution_runtime,
)
from chiplog.composition.common_execution_driver_contracts import (
    CliRetainedSelectedSourceV1,
    DriveInputRequestV1,
    DriverCommandIdentityV1,
    SelectedExecutionReceiptV1,
)
from chiplog.composition.r14_call_acceptance_port import (
    AcceptedCallReceipt,
    CallAcceptanceAdoption,
    CallAcceptanceTarget,
)
from chiplog.composition.r14_cancellation_contracts import (
    CANCELLATION_SCHEMA,
    NOT_EXECUTED_SCHEMA,
    CancelCallSubmission,
)
from chiplog.composition.r14_execution_cancellation_contracts import (
    EXECUTION_CANCELLATION_OPERATION,
    ExecutionCancelledCallReceipt,
)
from chiplog.composition.r14_fanout_records import reference
from chiplog.composition.r14_loop_history import read_execution_call_history
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform._sqlite import PhysicalPublicationCommand, PublicationResult
from chiplog.platform.broker import PublicPortCall, PublicPortResult
from chiplog.platform.ingress_transition_contracts import (
    IngressCommandIdentity,
    RetainedIngressSource,
)
from chiplog.platform.owner_publications import OwnerPublicationPending, OwnerPublicationUncertain
from chiplog.platform.r7_runtime import AuthorityBrokerRuntime


async def _custody_client(path: Path) -> None:
    reader, writer = await asyncio.open_unix_connection(path)
    try:
        size = struct.unpack("!I", await reader.readexactly(4))[0]
        offer = CliCustodyOffer.model_validate_json(await reader.readexactly(size))
        response = CliCustodyResponse(
            challenge_fingerprint=hashlib.sha256(offer.challenge.canonical_bytes()).hexdigest()
        ).canonical_bytes()
        writer.write(struct.pack("!I", len(response)) + response)
        await writer.drain()
        writer.write_eof()
    finally:
        writer.close()
        await writer.wait_closed()


async def _admit_selected_input(runtime: CommonCliExecutionRuntime) -> DriveInputRequestV1:
    runtime.provision_retained("slot", b"prepare one consequential native call")
    await runtime.allocate_receipt("slot")
    await runtime.stage_receipt("slot")
    token = runtime.ingress_history().custody.entries[0].token.token_id
    async with runtime.cli_custody("slot") as custody:
        client = asyncio.create_task(_custody_client(custody.socket.path))
        assert (await custody.admit()).kind == "COMMITTED"
        await client
    admitted = runtime.read_admitted_inbox(token)
    assert admitted is not None
    record = admitted.record
    identity = IngressCommandIdentity(
        tenant_id="hermetic-tenant", database_id="hermetic-database", command_id=token
    )
    retained = RetainedIngressSource(
        source=record.command.retention.proof,
        reader_id=R17_RETAINED_READER_ID,
        schema_id="chiplog.ingress.retained-source-observation.v1",
        canonical_source_bytes=record.command.retention.observation_bytes,
    )
    return DriveInputRequestV1(
        identity=DriverCommandIdentityV1(
            tenant_id="hermetic-tenant",
            database_id="hermetic-database",
            driver_command_id="driver:" + token,
            original_ingress_identity=identity,
            original_ingress_request_fingerprint=record.inbox.authentication_request_fingerprint,
        ),
        selected_source=CliRetainedSelectedSourceV1(
            original_ingress_identity=identity,
            source_binding=record.command.token.source,
            selected_ingress_decision=admitted.selected_decision,
            source_head=retained.source,
            retained_source=retained,
            expected_reader_id=R17_RETAINED_READER_ID,
        ),
    )


@dataclass(frozen=True)
class _Branch:
    runtime: CommonCliExecutionRuntime
    resources: HermeticDispatchResources
    database: Path
    target: CallAcceptanceTarget
    adoption: CallAcceptanceAdoption
    cancellation: CancelCallSubmission


@asynccontextmanager
async def _initialized_branch(tmp_path: Path) -> AsyncIterator[_Branch]:
    """Use the installed CLI assembly through selected ingress and native fanout."""
    database = tmp_path / "branch-competition.sqlite"
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )
    response = ExecutionContinue(
        kind="Continue",
        tool_calls=(
            ConsequentialToolCall(
                call_id="effect",
                tool="request_self_effect",
                arguments=SelfEffectArguments(payload=b"branch", bundle_members=("one",)),
            ),
        ),
    ).model_dump_json().encode()
    async with open_common_cli_execution_runtime(
        database, resources=resources, responses=(response,)
    ) as runtime:
        initial = await runtime.drive_input(await _admit_selected_input(runtime))
        assert isinstance(initial, SelectedExecutionReceiptV1)
        started = await runtime.begin_execution(
            "hermetic-ingress", initial.stable_run_lineage_id, initial.selected_run_head.head
        )
        captured = await runtime.capture_execution(
            "hermetic-ingress", initial.stable_run_lineage_id, started.head
        )
        sealed = await runtime.seal_execution(
            "hermetic-ingress", initial.stable_run_lineage_id, captured.head
        )
        _, inventory, preparations = read_execution_call_history(runtime)
        assert preparations and preparations[-1].proposal.sealed_run == sealed
        assert len(inventory.ordered_calls) == 1
        row = inventory.ordered_calls[0]
        assert row.acceptance.kind == "INITIALIZED" and isinstance(row.terminal, Absent)
        current_run = CallSubjectHead(
            subject_id=sealed.run_id,
            revision=Present(head=sealed.head, fingerprint=sealed.digest()),
        )
        yield _Branch(
            runtime=runtime,
            resources=resources,
            database=database,
            target=CallAcceptanceTarget(
                original_call_id=row.original_call_id,
                initialized=row.initialized,
                current_run=current_run,
            ),
            adoption=CallAcceptanceAdoption(act_id="accept-branch", preview_bytes=b"pending"),
            cancellation=CancelCallSubmission(
                act_id="cancel-branch",
                original_call_id=row.original_call_id,
                initialized=reference(
                    row.initialized_record.original_call_id, row.initialized_record
                ),
                current_run=current_run,
            ),
        )


def _decision_entries(runtime: CommonCliExecutionRuntime) -> tuple[dict[str, object], ...]:
    return tuple(json.loads(raw) for _, _, raw in runtime._loop_decisions().entries())


def _physical_members(
    database: Path, tenant_id: str, sequence: int
) -> tuple[tuple[str, str, bytes], ...]:
    with sqlite3.connect(database) as connection:
        return tuple(
            connection.execute(
                "SELECT record_id, schema_id, canonical_bytes FROM records WHERE tenant_id=? "
                "AND commit_sequence=? ORDER BY rowid",
                (tenant_id, sequence),
            )
        )


async def _rejected_by_winner(task: asyncio.Task[object]) -> LoopRejected:
    with pytest.raises(LoopRejected) as caught:
        await asyncio.wait_for(task, timeout=10)
    assert task.done() and not task.cancelled()
    return caught.value


def _selected_members(
    runtime: CommonCliExecutionRuntime, winner: str
) -> tuple[tuple[str, str, bytes], ...]:
    """Capture the selected batch before recovery can materialize it."""
    if winner == "acceptance":
        selected = runtime._owner_decisions().snapshot().decisions[-1]
        return tuple(
            (record.record_id, record.schema_id, record.canonical_bytes)
            for record in selected.prepared.request.complete_records
        )
    decisions = tuple(
        item
        for item in _decision_entries(runtime)
        if item["kind"] == "DECIDED"
        and item["operation_kind"] == EXECUTION_CANCELLATION_OPERATION
    )
    assert len(decisions) == 1
    records = decisions[0]["records"]
    assert isinstance(records, list)
    return tuple(
        (
            str(record["record_id"]),
            str(record["schema"]),
            base64.b64decode(str(record["payload"]), validate=True),
        )
        for record in records
    )


@pytest.mark.parametrize("winner", ("acceptance", "cancellation"))
async def test_selected_unmaterialized_cross_family_winner_recovers_before_rival_replays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, winner: str
) -> None:
    """A selected but uncommitted winner excludes its rival through restart."""
    response = ExecutionContinue(
        kind="Continue",
        tool_calls=(
            ConsequentialToolCall(
                call_id="effect",
                tool="request_self_effect",
                arguments=SelfEffectArguments(payload=b"branch", bundle_members=("one",)),
            ),
        ),
    ).model_dump_json().encode()
    async with _initialized_branch(tmp_path) as branch:
        runtime = branch.runtime
        preview = await runtime.preview_call_acceptance("hermetic-ingress", branch.target)
        adoption = branch.adoption.model_copy(update={"preview_bytes": preview.canonical_bytes()})
        before = read_execution_call_history(runtime)
        submit = runtime._appender.submit
        selected_operation = (
            "effects.accept_call" if winner == "acceptance" else EXECUTION_CANCELLATION_OPERATION
        )

        async def crash_selected(command: PhysicalPublicationCommand) -> PublicationResult:
            if command.operation_kind == selected_operation:
                command = replace(command, fault="before_commit")
            return await submit(command)

        with monkeypatch.context() as patch:
            patch.setattr(runtime._appender, "submit", crash_selected)
            if winner == "acceptance":
                with pytest.raises(OwnerPublicationUncertain):
                    await runtime.accept_call("hermetic-ingress", adoption)
            else:
                with pytest.raises(RuntimeError, match="injected fault before commit"):
                    await runtime.cancel_execution_call("hermetic-ingress", branch.cancellation)

        pending_owner = runtime._owner_decisions().snapshot()
        pending_loop = _decision_entries(runtime)
        expected_members = _selected_members(runtime, winner)
        expected_sequence = before[0].tenant_head + 1
        assert _physical_members(branch.database, runtime._tenant_id, expected_sequence) == ()
        assert len(runtime._pending_owners()) + len(runtime._pending()) == 1

        if winner == "acceptance":
            with pytest.raises(OwnerPublicationPending, match="selected publication"):
                await runtime.cancel_execution_call("hermetic-ingress", branch.cancellation)
        else:
            with pytest.raises(EffectsIntegrityError) as rejected:
                await runtime.accept_call("hermetic-ingress", adoption)
            assert isinstance(rejected.value.__cause__, EffectsIntegrityError)
            assert isinstance(rejected.value.__cause__.__cause__, OwnerPublicationPending)
            assert "selected publication" in str(rejected.value.__cause__.__cause__)
        assert runtime._owner_decisions().snapshot() == pending_owner
        assert _decision_entries(runtime) == pending_loop
        assert _physical_members(branch.database, runtime._tenant_id, expected_sequence) == ()
        assert branch.resources.require_original_provider().transfers == ()

    original_call = AuthorityBrokerRuntime.call

    async def forbid_selected_owner_reissue(
        self: AuthorityBrokerRuntime, request: PublicPortCall
    ) -> PublicPortResult:
        assert request.operation_id != (
            "agent_loop.prepare_consequential_acceptance"
            if winner == "acceptance"
            else "agent_loop.prepare_pre_accept_cancellation"
        )
        return await original_call(self, request)

    # The guard is present while startup recovers the pending selected bytes.
    monkeypatch.setattr(AuthorityBrokerRuntime, "call", forbid_selected_owner_reissue)
    reopened_resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )
    async with open_common_cli_execution_runtime(
        branch.database, resources=reopened_resources, responses=(response,)
    ) as reopened:
        history, inventory, _ = read_execution_call_history(reopened)
        assert history.tenant_head == before[0].tenant_head + 1
        assert not reopened._pending_owners() and not reopened._pending()
        assert (
            _physical_members(reopened._database, reopened._tenant_id, history.tenant_head)
            == expected_members
        )
        recovered_owner = reopened._owner_decisions().snapshot()
        recovered_loop = _decision_entries(reopened)
        with sqlite3.connect(reopened._database) as connection:
            sequences = connection.execute(
                "SELECT DISTINCT commit_sequence FROM records WHERE tenant_id=? "
                "AND record_id IN (" + ", ".join("?" for _ in expected_members) + ")",
                (reopened._tenant_id, *(record_id for record_id, _, _ in expected_members)),
            ).fetchall()
        assert sequences == [(history.tenant_head,)]
        if winner == "acceptance":
            accepted = await reopened.accept_call("hermetic-ingress", adoption)
            assert isinstance(accepted, AcceptedCallReceipt)
            with pytest.raises(LoopRejected):
                await reopened.cancel_execution_call("hermetic-ingress", branch.cancellation)
            assert inventory.ordered_calls[0].acceptance.kind == "CONSEQUENTIAL_ACCEPTED"
        else:
            cancelled = await reopened.cancel_execution_call(
                "hermetic-ingress", branch.cancellation
            )
            assert isinstance(cancelled, ExecutionCancelledCallReceipt)
            with pytest.raises(LoopRejected):
                await reopened.accept_call("hermetic-ingress", adoption)
            assert inventory.ordered_calls[0].terminal == cancelled.terminal.revision
        after_replays = read_execution_call_history(reopened)
        assert after_replays[0].tenant_head == history.tenant_head
        assert not reopened._pending_owners() and not reopened._pending()
        assert reopened._owner_decisions().snapshot() == recovered_owner
        assert _decision_entries(reopened) == recovered_loop
        assert (
            _physical_members(reopened._database, reopened._tenant_id, history.tenant_head)
            == expected_members
        )
        assert reopened_resources.require_original_provider().transfers == ()


@pytest.mark.parametrize("winner", ("acceptance", "cancellation"))
async def test_selected_acceptance_and_native_cancellation_compete_on_one_initialized_cut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, winner: str
) -> None:
    async with _initialized_branch(tmp_path) as branch:
        runtime = branch.runtime
        preview = await runtime.preview_call_acceptance("hermetic-ingress", branch.target)
        adoption = branch.adoption.model_copy(update={"preview_bytes": preview.canonical_bytes()})
        before_history = read_execution_call_history(runtime)
        before_owner = runtime._owner_decisions().snapshot()
        before_loop = _decision_entries(runtime)
        acceptance_at_commit = asyncio.Event()
        release_acceptance = asyncio.Event()
        cancellation_at_writer = asyncio.Event()
        release_cancellation = asyncio.Event()
        original_submit = runtime._appender.submit

        async def paused_submit(command: PhysicalPublicationCommand) -> PublicationResult:
            if command.operation_kind == "effects.accept_call":
                # This is reached only from the real coordinator after it has
                # prepared the issued batch, immediately before writer selection.
                acceptance_at_commit.set()
                await release_acceptance.wait()
            elif command.operation_kind == EXECUTION_CANCELLATION_OPERATION:
                cancellation_at_writer.set()
                await release_cancellation.wait()
            return await original_submit(command)

        monkeypatch.setattr(runtime._appender, "submit", paused_submit)
        acceptance_task = asyncio.create_task(runtime.accept_call("hermetic-ingress", adoption))
        await asyncio.wait_for(acceptance_at_commit.wait(), timeout=10)
        # The coordinator has prepared acceptance but writer selection has not run.
        assert read_execution_call_history(runtime) == before_history
        assert runtime._owner_decisions().snapshot() == before_owner
        assert _decision_entries(runtime) == before_loop
        cancellation_task = asyncio.create_task(
            runtime.cancel_execution_call("hermetic-ingress", branch.cancellation)
        )
        await asyncio.wait_for(cancellation_at_writer.wait(), timeout=10)
        assert read_execution_call_history(runtime) == before_history
        assert runtime._owner_decisions().snapshot() == before_owner
        assert _decision_entries(runtime) == before_loop

        if winner == "acceptance":
            release_acceptance.set()
            acceptance_result = await asyncio.wait_for(acceptance_task, timeout=10)
            assert acceptance_task.done() and not acceptance_task.cancelled()
            release_cancellation.set()
            cancellation_result = await _rejected_by_winner(cancellation_task)
        else:
            release_cancellation.set()
            cancellation_result = await asyncio.wait_for(cancellation_task, timeout=10)
            assert cancellation_task.done() and not cancellation_task.cancelled()
            release_acceptance.set()
            acceptance_result = await _rejected_by_winner(acceptance_task)

        history, inventory, preparations = read_execution_call_history(runtime)
        row = inventory.ordered_calls[0]
        assert history.tenant_head == before_history[0].tenant_head + 1
        await asyncio.sleep(0)
        assert branch.resources.require_original_provider().transfers == ()
        owner_after = runtime._owner_decisions().snapshot()
        loop_after = _decision_entries(runtime)
        if winner == "acceptance":
            assert isinstance(acceptance_result, AcceptedCallReceipt)
            assert isinstance(cancellation_result, LoopRejected)
            assert row.acceptance.kind == "CONSEQUENTIAL_ACCEPTED"
            assert isinstance(row.terminal, Absent)
            assert len(owner_after.decisions) == len(before_owner.decisions) + 1
            assert loop_after == before_loop
            members = _physical_members(runtime._database, runtime._tenant_id, history.tenant_head)
            assert len(members) == 3
            selected_batch = owner_after.decisions[-1].prepared.request
            assert members == tuple(
                (record.record_id, record.schema_id, record.canonical_bytes)
                for record in selected_batch.complete_records
            )
            assert acceptance_result.accepted.subject_id in {
                record_id for record_id, _, _ in members
            }
            replay = await runtime.accept_call("hermetic-ingress", adoption)
            assert replay == acceptance_result
        else:
            assert isinstance(cancellation_result, ExecutionCancelledCallReceipt)
            assert isinstance(acceptance_result, LoopRejected)
            assert row.acceptance.kind == "INITIALIZED"
            assert row.terminal == cancellation_result.terminal.revision
            assert owner_after == before_owner
            decisions = loop_after[len(before_loop) :]
            selected = tuple(item for item in decisions if item["kind"] == "DECIDED")
            materialized = tuple(item for item in decisions if item["kind"] == "MATERIALIZED")
            assert len(selected) == len(materialized) == 1
            assert selected[0]["operation_kind"] == EXECUTION_CANCELLATION_OPERATION
            members = _physical_members(runtime._database, runtime._tenant_id, history.tenant_head)
            records = selected[0]["records"]
            assert isinstance(records, list)
            assert members == tuple(
                (
                    str(record["record_id"]),
                    str(record["schema"]),
                    base64.b64decode(str(record["payload"]), validate=True),
                )
                for record in records
            )
            assert tuple(schema for _, schema, _ in members) == (
                CANCELLATION_SCHEMA,
                NOT_EXECUTED_SCHEMA,
            )
            assert tuple(record_id for record_id, _, _ in members) == (
                cancellation_result.terminal.subject_id,
                cancellation_result.result.subject_id,
            )
            replay = await runtime.cancel_execution_call("hermetic-ingress", branch.cancellation)
            assert replay == cancellation_result
        assert len(inventory.ordered_calls) == 1
        assert read_execution_call_history(runtime) == (history, inventory, preparations)
        assert runtime._owner_decisions().snapshot() == owner_after
        assert _decision_entries(runtime) == loop_after
        assert branch.resources.require_original_provider().transfers == ()
