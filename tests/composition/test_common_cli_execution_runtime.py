from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import struct
from pathlib import Path

import pytest

from chiplog.capabilities.agent_loop.delivery_preparation import (
    Commentary,
    DeliveryCompletion,
    ProposedDelivery,
)
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.capabilities.deployment_trust.cli_custody_contracts import (
    CliCustodyOffer,
    CliCustodyResponse,
)
from chiplog.capabilities.effects.h1_local_preparation_record_contracts import (
    H1LocalPreparedCommentaryCanonicalMemberV1,
    decode_h1_local_prepared_commentary_member,
)
from chiplog.composition.common_cli_execution_runtime import (
    R17_RETAINED_READER_ID,
    CommonCliExecutionRuntime,
    open_common_cli_execution_runtime,
)
from chiplog.composition.common_execution_driver_contracts import (
    AdvanceExecutionRequestV1,
    CliRetainedSelectedSourceV1,
    DriveInputRequestV1,
    DriverCommandIdentityV1,
    LookupExecutionRequestV1,
    SelectedExecutionReceiptV1,
)
from chiplog.composition.r14_execution_completion_records import (
    COMPLETE_ACCEPTANCE_OPERATION,
)
from chiplog.composition.r14_execution_inbox_records import (
    EXECUTION_INBOX_INITIALIZATION_OPERATION,
)
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform._owner_publication_contracts import CompleteDeliveryBatchV2
from chiplog.platform._sqlite import PhysicalPublicationCommand
from chiplog.platform.ingress_transition_contracts import (
    IngressCommandIdentity,
    RetainedIngressSource,
)


async def _client(path: Path) -> None:
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


async def _admit(runtime: CommonCliExecutionRuntime) -> tuple[str, DriveInputRequestV1]:
    runtime.provision_retained("slot", b"make a native run")
    await runtime.allocate_receipt("slot")
    await runtime.stage_receipt("slot")
    token = runtime.ingress_history().custody.entries[0].token.token_id
    async with runtime.cli_custody("slot") as custody:
        peer = asyncio.create_task(_client(custody.socket.path))
        assert (await custody.admit()).kind == "COMMITTED"
        await peer
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
    request = DriveInputRequestV1(
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
    return token, request


async def _admit_complete_script(
    database: Path, custody: Path
) -> tuple[DriveInputRequestV1, bytes]:
    """Persist a real selected CLI input, then derive its one exact model response."""
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1, custody_path=custody)
    async with open_common_cli_execution_runtime(database, resources=resources) as runtime:
        _, request = await _admit(runtime)
        admitted = runtime._selected_input(request, resources.observe())
        run_id = runtime._run_id(admitted)
    complete = DeliveryCompletion(
        tenant="hermetic-tenant",
        run_id=run_id,
        turn_id=run_id + "/turn/1",
        deliveries=(ProposedDelivery(payload=(Commentary(text="Hello"),)),),
    )
    return request, complete.canonical_bytes()


def _physical_snapshot(database: Path) -> tuple[tuple[object, ...], ...]:
    with sqlite3.connect(database) as connection:
        return tuple(
            connection.execute(
                "SELECT record_id, owner, schema_id, canonical_bytes, commit_sequence "
                "FROM records ORDER BY rowid"
            ).fetchall()
        )


class H1TerminalBoundaryUnavailable(Exception):
    """The public H1 terminal boundary has not selected its owner publication."""


def _require_h1_terminal_boundary(receipt: SelectedExecutionReceiptV1) -> None:
    if receipt.phase == "RUNNING":
        raise H1TerminalBoundaryUnavailable(
            "H1 terminal owner publication not implemented: receipt phase RUNNING at terminal "
            "boundary; expected TERMINAL"
        )


def _assert_h1_local_prepared_commentary_record(
    rows: list[tuple[str, str, str, bytes]],
    complete: bytes,
    batch: CompleteDeliveryBatchV2,
    command: PhysicalPublicationCommand,
) -> None:
    assert len(rows) == len(command.records) == len(batch.complete_records)
    assert batch.terminal_work_command.owner == "agent_loop"
    assert batch.terminal_work_command.schema_id == "chiplog.agent-loop.prepare-terminal-work.v1"
    local_rows = [
        row
        for row in rows
        if row[1] == "effects"
        and row[2] == "chiplog.effects.h1-local-prepared-commentary-intent.v1"
    ]
    assert len(local_rows) == 1
    local_row = local_rows[0]
    completion = DeliveryCompletion.model_validate_json(complete)
    assert len(completion.deliveries) == 1
    assert len(completion.deliveries[0].payload) == 1
    commentary = completion.deliveries[0].payload[0]
    assert isinstance(commentary, Commentary)
    member = H1LocalPreparedCommentaryCanonicalMemberV1(
        record_id=local_row[0],
        canonical_record_bytes=local_row[3],
        fingerprint=hashlib.sha256(local_row[3]).hexdigest(),
    )
    intent = decode_h1_local_prepared_commentary_member(member)
    assert intent.canonical_bytes() == local_row[3]
    assert intent.external_delivery is False
    assert intent.delivery.rendered_bytes == commentary.text.encode()


def _selected_h1_owner_command(
    runtime: CommonCliExecutionRuntime, receipt: SelectedExecutionReceiptV1
) -> tuple[CompleteDeliveryBatchV2, PhysicalPublicationCommand]:
    selected = [
        decision
        for decision in runtime._owner_decisions().snapshot().decisions
        if isinstance(decision.prepared.request, CompleteDeliveryBatchV2)
        and decision.prepared.request.operation == COMPLETE_ACCEPTANCE_OPERATION
    ]
    assert len(selected) == 1
    decision = selected[0]
    batch = decision.prepared.request
    assert isinstance(batch, CompleteDeliveryBatchV2)
    assert decision.decision_head == receipt.selected_journal_decision.head
    command = runtime._owner_command(decision)
    assert command.operation_kind == COMPLETE_ACCEPTANCE_OPERATION
    assert receipt.commit_sequence == decision.tenant_commit_sequence == command.expected_head + 1
    return batch, command


def _advance(
    initial: SelectedExecutionReceiptV1, request: DriveInputRequestV1
) -> AdvanceExecutionRequestV1:
    return AdvanceExecutionRequestV1(
        identity=request.identity,
        original_driver_command_fingerprint=request.original_driver_command_fingerprint(),
        expected_selected_run_head=initial.selected_run_head,
    )


@pytest.mark.asyncio
async def test_socket_selected_inbox_creates_native_run_and_reopens(tmp_path: Path) -> None:
    database = tmp_path / "h0.sqlite"
    custody = tmp_path / "dispatch-custody"
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1, custody_path=custody)
    async with open_common_cli_execution_runtime(database, resources=resources) as runtime:
        _, request = await _admit(runtime)
        committed = await runtime.drive_input(request)
        assert committed.kind == "SELECTED_EXECUTION_RECEIPT_V1"
        assert committed.disposition == "COMMITTED"
        assert committed.selected_run_state == "CREATED"
        assert committed.selected_run_head.identity == committed.selected_run_head.head
        entries = [json.loads(raw) for _, _, raw in runtime._loop_decisions().entries()]
        h0 = [
            entry
            for entry in entries
            if entry.get("operation_kind") == EXECUTION_INBOX_INITIALIZATION_OPERATION
        ]
        assert len(h0) == 1
        assert committed.commit_sequence == h0[0]["expected_head"] + 1
        assert committed.selected_journal_decision != committed.selected_ingress_decision
        with sqlite3.connect(database) as connection:
            row = connection.execute(
                "SELECT canonical_bytes FROM records WHERE record_id=?",
                (committed.selected_run_head.head,),
            ).fetchone()
        assert row is not None
        assert hashlib.sha256(row[0]).hexdigest() == committed.selected_run_head.fingerprint
        before_count = len(
            [
                entry
                for entry in entries
                if entry.get("operation_kind") == EXECUTION_INBOX_INITIALIZATION_OPERATION
            ]
        )
    reopened_resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=custody
    )
    async with open_common_cli_execution_runtime(database, resources=reopened_resources) as runtime:
        replay = await runtime.drive_input(request)
        assert replay.kind == "SELECTED_EXECUTION_RECEIPT_V1", replay
        assert replay.disposition == "EXACT_REPLAY"
        assert replay.selected_run_head == committed.selected_run_head
        assert replay.selected_journal_decision == committed.selected_journal_decision
        lookup = await runtime.lookup_execution(
            LookupExecutionRequestV1(
                identity=request.identity,
                original_driver_command_fingerprint=request.original_driver_command_fingerprint(),
            )
        )
        assert lookup.kind == "SELECTED_EXECUTION_RECEIPT_V1"
        assert lookup.disposition == "EXACT_REPLAY"
        assert lookup.selected_run_head == committed.selected_run_head
        conflict = await runtime.lookup_execution(
            LookupExecutionRequestV1(
                identity=request.identity,
                original_driver_command_fingerprint="0" * 64,
            )
        )
        assert conflict.kind == "EXECUTION_DRIVER_REJECTED_V1"
        assert conflict.code == "CONFLICT"
        after_count = len(
            [
                json.loads(raw)
                for _, _, raw in runtime._loop_decisions().entries()
                if json.loads(raw).get("operation_kind") == EXECUTION_INBOX_INITIALIZATION_OPERATION
            ]
        )
        assert after_count == before_count == 1


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    raises=H1TerminalBoundaryUnavailable,
    reason="H1 terminal owner publication not implemented",
)
async def test_socket_complete_selects_native_terminal_batch(tmp_path: Path) -> None:
    database = tmp_path / "h1-complete.sqlite"
    custody = tmp_path / "dispatch-custody"
    request, complete = await _admit_complete_script(database, custody)
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1, custody_path=custody)
    async with open_common_cli_execution_runtime(
        database, resources=resources, responses=(complete,)
    ) as runtime:
        initial = await runtime.drive_input(request)

        assert initial.kind == "SELECTED_EXECUTION_RECEIPT_V1"
        assert initial.phase == "INITIALIZED"
        assert initial.selected_run_state == "CREATED"
        assert len(runtime._execution_model.requests) == 0

        receipt = await runtime.advance_execution(_advance(initial, request))
        assert receipt.kind == "SELECTED_EXECUTION_RECEIPT_V1"
        _require_h1_terminal_boundary(receipt)
        assert receipt.phase == "TERMINAL"
        assert receipt.selected_run_state == "SUCCEEDED"
        assert receipt.terminal_detail is not None
        assert receipt.terminal_detail.kind == "ACCEPTED"
        assert receipt.selected_run_head != initial.selected_run_head
        assert receipt.selected_journal_decision != initial.selected_journal_decision
        assert receipt.commit_sequence > initial.commit_sequence
        assert len(runtime._execution_model.requests) == 1

        batch, command = _selected_h1_owner_command(runtime, receipt)
        with sqlite3.connect(database) as connection:
            rows = connection.execute(
                "SELECT record_id, owner, schema_id, canonical_bytes FROM records "
                "WHERE commit_sequence=? ORDER BY rowid",
                (receipt.commit_sequence,),
            ).fetchall()
        assert rows == [
            (member.record_id, member.owner, member.schema_id, member.canonical_bytes)
            for member in command.records
        ]
        _assert_h1_local_prepared_commentary_record(rows, complete, batch, command)
        terminal_run = ExecutionRunRecord.model_validate_json(rows[2][3])
        assert terminal_run.state == "SUCCEEDED"
        assert terminal_run.turns[-1].state == "ACCEPTED"
        assert terminal_run.turns[-1].attempts[-1].state == "TERMINAL_ACCEPTED"
        assert json.loads(rows[3][3])["source_kind"] == "COMPLETION"
        assert resources.require_original_provider().transfers == ()


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    raises=H1TerminalBoundaryUnavailable,
    reason="H1 terminal owner publication not implemented",
)
async def test_terminal_drive_reopens_without_second_decision_or_model_call(tmp_path: Path) -> None:
    database = tmp_path / "h1-replay.sqlite"
    custody = tmp_path / "dispatch-custody"
    request, complete = await _admit_complete_script(database, custody)
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1, custody_path=custody)
    async with open_common_cli_execution_runtime(
        database, resources=resources, responses=(complete,)
    ) as runtime:
        initial = await runtime.drive_input(request)
        assert initial.kind == "SELECTED_EXECUTION_RECEIPT_V1"
        assert initial.phase == "INITIALIZED"
        assert initial.selected_run_state == "CREATED"
        assert len(runtime._execution_model.requests) == 0
        advance = _advance(initial, request)
        committed = await runtime.advance_execution(advance)
        assert committed.kind == "SELECTED_EXECUTION_RECEIPT_V1"
        _require_h1_terminal_boundary(committed)
        assert committed.phase == "TERMINAL"
        assert committed.selected_run_state == "SUCCEEDED"
        assert len(runtime._execution_model.requests) == 1
        loop_decisions = runtime._loop_decisions().entries()
        owner_decisions = runtime._owner_decisions().snapshot()
        batch, command = _selected_h1_owner_command(runtime, committed)
        records = _physical_snapshot(database)
        with sqlite3.connect(database) as connection:
            rows = connection.execute(
                "SELECT record_id, owner, schema_id, canonical_bytes FROM records "
                "WHERE commit_sequence=? ORDER BY rowid",
                (committed.commit_sequence,),
            ).fetchall()
        assert rows == [
            (member.record_id, member.owner, member.schema_id, member.canonical_bytes)
            for member in command.records
        ]
        _assert_h1_local_prepared_commentary_record(rows, complete, batch, command)
        assert resources.require_original_provider().transfers == ()

    reopened_resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=custody
    )
    async with open_common_cli_execution_runtime(database, resources=reopened_resources) as runtime:
        replay = await runtime.advance_execution(advance)
        admission_replay = await runtime.drive_input(request)
        lookup = await runtime.lookup_execution(
            LookupExecutionRequestV1(
                identity=request.identity,
                original_driver_command_fingerprint=request.original_driver_command_fingerprint(),
            )
        )
        assert replay.kind == lookup.kind == "SELECTED_EXECUTION_RECEIPT_V1"
        assert replay.disposition == lookup.disposition == "EXACT_REPLAY"
        assert replay.phase == lookup.phase == "TERMINAL"
        assert replay.selected_run_state == lookup.selected_run_state == "SUCCEEDED"
        assert replay.selected_journal_decision == committed.selected_journal_decision
        assert lookup.selected_journal_decision == committed.selected_journal_decision
        assert replay.model_dump(exclude={"disposition"}) == committed.model_dump(
            exclude={"disposition"}
        )
        assert lookup.model_dump(exclude={"disposition"}) == committed.model_dump(
            exclude={"disposition"}
        )
        assert admission_replay.kind == "SELECTED_EXECUTION_RECEIPT_V1"
        assert admission_replay.disposition == "EXACT_REPLAY"
        assert admission_replay.model_dump(exclude={"disposition"}) == initial.model_dump(
            exclude={"disposition"}
        )
        assert runtime._execution_model.requests == []
        assert runtime._loop_decisions().entries() == loop_decisions
        assert runtime._owner_decisions().snapshot() == owner_decisions
        assert _physical_snapshot(database) == records
        assert reopened_resources.require_original_provider().transfers == ()


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    raises=H1TerminalBoundaryUnavailable,
    reason="H1 terminal owner publication not implemented",
)
async def test_prepared_complete_seal_reopens_and_publishes_once(tmp_path: Path) -> None:
    database = tmp_path / "h1-prepared-replay.sqlite"
    custody = tmp_path / "dispatch-custody"
    request, complete = await _admit_complete_script(database, custody)
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1, custody_path=custody)
    async with open_common_cli_execution_runtime(
        database, resources=resources, responses=(complete,)
    ) as runtime:
        initial = await runtime.drive_input(request)
        assert initial.kind == "SELECTED_EXECUTION_RECEIPT_V1"
        assert initial.phase == "INITIALIZED"
        advance = _advance(initial, request)

        started = await runtime.begin_execution(
            "hermetic-ingress", initial.stable_run_lineage_id, initial.selected_run_head.head
        )
        captured = await runtime.capture_execution(
            "hermetic-ingress", initial.stable_run_lineage_id, started.head
        )
        sealed = await runtime.seal_execution_complete(
            "hermetic-ingress", initial.stable_run_lineage_id, captured.head
        )

        assert sealed.state == "ACTIVE"
        assert sealed.event == "ModelCompletionPrepared"
        assert len(runtime._execution_model.requests) == 1
        assert not any(
            isinstance(decision.prepared.request, CompleteDeliveryBatchV2)
            and decision.prepared.request.operation == COMPLETE_ACCEPTANCE_OPERATION
            for decision in runtime._owner_decisions().snapshot().decisions
        )
        assert resources.require_original_provider().transfers == ()

    reopened_resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=custody
    )
    async with open_common_cli_execution_runtime(database, resources=reopened_resources) as runtime:
        terminal = await runtime.advance_execution(advance)

        assert terminal.kind == "SELECTED_EXECUTION_RECEIPT_V1"
        _require_h1_terminal_boundary(terminal)
        assert terminal.phase == "TERMINAL"
        assert terminal.selected_run_state == "SUCCEEDED"
        assert terminal.terminal_detail is not None
        assert terminal.terminal_detail.kind == "ACCEPTED"
        assert runtime._execution_model.requests == []

        loop_decisions = runtime._loop_decisions().entries()
        owner_decisions = runtime._owner_decisions().snapshot()
        batch, command = _selected_h1_owner_command(runtime, terminal)
        with sqlite3.connect(database) as connection:
            rows = connection.execute(
                "SELECT record_id, owner, schema_id, canonical_bytes FROM records "
                "WHERE commit_sequence=? ORDER BY rowid",
                (terminal.commit_sequence,),
            ).fetchall()
        assert rows == [
            (member.record_id, member.owner, member.schema_id, member.canonical_bytes)
            for member in command.records
        ]
        _assert_h1_local_prepared_commentary_record(rows, complete, batch, command)
        assert reopened_resources.require_original_provider().transfers == ()

        records = _physical_snapshot(database)
        replay = await runtime.advance_execution(advance)
        assert replay.kind == "SELECTED_EXECUTION_RECEIPT_V1"
        assert replay.disposition == "EXACT_REPLAY"
        assert replay.model_dump(exclude={"disposition"}) == terminal.model_dump(
            exclude={"disposition"}
        )
        assert runtime._execution_model.requests == []
        assert runtime._loop_decisions().entries() == loop_decisions
        assert runtime._owner_decisions().snapshot() == owner_decisions
        assert _physical_snapshot(database) == records
        assert reopened_resources.require_original_provider().transfers == ()


@pytest.mark.asyncio
async def test_selected_inbox_rejects_changed_or_second_driver_identity(tmp_path: Path) -> None:
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1)
    async with open_common_cli_execution_runtime(
        tmp_path / "conflict.sqlite", resources=resources
    ) as runtime:
        _, request = await _admit(runtime)
        assert (await runtime.drive_input(request)).kind == "SELECTED_EXECUTION_RECEIPT_V1"
        changed = request.model_copy(
            update={"identity": request.identity.model_copy(update={"driver_command_id": "other"})}
        )
        result = await runtime.drive_input(changed)
        assert result.kind == "EXECUTION_DRIVER_REJECTED_V1"
        assert result.code == "CONFLICT"
        changed_fingerprint = request.model_copy(
            update={
                "selected_source": request.selected_source.model_copy(
                    update={
                        "expected_reader_id": "changed-reader",
                        "retained_source": request.selected_source.retained_source.model_copy(
                            update={"reader_id": "changed-reader"}
                        ),
                    }
                )
            }
        )
        result = await runtime.drive_input(changed_fingerprint)
        assert result.kind == "EXECUTION_DRIVER_REJECTED_V1"
        assert result.code == "CONFLICT"
        bad_reader = request.model_copy(
            update={
                "identity": request.identity.model_copy(
                    update={"driver_command_id": "forged-reader"}
                ),
                "selected_source": request.selected_source.model_copy(
                    update={
                        "expected_reader_id": "forged-reader",
                        "retained_source": request.selected_source.retained_source.model_copy(
                            update={"reader_id": "forged-reader"}
                        ),
                    }
                ),
            }
        )
        result = await runtime.drive_input(bad_reader)
        assert result.kind == "EXECUTION_DRIVER_REJECTED_V1"
        assert result.code == "DENIED"
