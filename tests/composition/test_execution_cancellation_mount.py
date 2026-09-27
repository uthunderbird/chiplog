"""Reached RED witnesses for the native selected execution-cancellation mount.

The fixture deliberately uses the installed common CLI assembly.  It proves that
an admitted selected input has reached the native initialized-fanout state before
calling the public J3 port, so an ``AttributeError`` is evidence of the missing
mount rather than a fabricated authority path or an incomplete fixture.
"""

from __future__ import annotations

import asyncio
import hashlib
import sqlite3
import struct
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.contracts import ToolCall
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionContinue
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
from chiplog.composition.r14_cancellation_contracts import (
    CANCELLATION_SCHEMA,
    NOT_EXECUTED_SCHEMA,
    CancelCallSubmission,
)
from chiplog.composition.r14_execution_cancellation_contracts import (
    ExecutionCallCancellationPort,
    ExecutionCancelledCallReceipt,
)
from chiplog.composition.r14_fanout_records import reference
from chiplog.composition.r14_loop_history import read_execution_call_history
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform.ingress_transition_contracts import (
    IngressCommandIdentity,
    RetainedIngressSource,
)


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
    runtime.provision_retained("slot", b"prepare a cancellable native call")
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
class _MountedCall:
    runtime: CommonCliExecutionRuntime
    submission: CancelCallSubmission
    database: Path


@asynccontextmanager
async def _initialized_selected_call(tmp_path: Path) -> AsyncIterator[_MountedCall]:
    """Materialize a call-bearing native fanout through the installed CLI runtime."""
    database = tmp_path / "execution-cancellation.sqlite"
    custody = tmp_path / "dispatch-custody"
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1, custody_path=custody)
    response = (
        ExecutionContinue(
            kind="Continue",
            tool_calls=(ToolCall(call_id="plan", tool="propose_planning", text="Plan"),),
        )
        .model_dump_json()
        .encode()
    )
    async with open_common_cli_execution_runtime(
        database, resources=resources, responses=(response,)
    ) as runtime:
        request = await _admit_selected_input(runtime)
        initial = await runtime.drive_input(request)
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
        assert inventory.ordered_calls
        row = inventory.ordered_calls[0]
        assert isinstance(row.terminal, Absent)
        submission = CancelCallSubmission(
            act_id="cancel-native-selected-call",
            original_call_id=row.original_call_id,
            initialized=reference(row.initialized_record.original_call_id, row.initialized_record),
            current_run=CallSubjectHead(
                subject_id=sealed.run_id,
                revision=Present(head=sealed.head, fingerprint=sealed.digest()),
            ),
        )
        yield _MountedCall(runtime=runtime, submission=submission, database=database)


async def _cancel(mounted: _MountedCall) -> ExecutionCancelledCallReceipt:
    """Call the real declared public port; no substitute or mock is installed here."""
    return await cast(ExecutionCallCancellationPort, mounted.runtime).cancel_execution_call(
        "hermetic-ingress", mounted.submission
    )


async def test_native_cancel_selects_exact_two_members(tmp_path: Path) -> None:
    async with _initialized_selected_call(tmp_path) as mounted:
        receipt = await _cancel(mounted)

        assert receipt.original_call_id == mounted.submission.original_call_id
        _, inventory, _ = read_execution_call_history(mounted.runtime)
        row = next(
            item
            for item in inventory.ordered_calls
            if item.original_call_id == receipt.original_call_id
        )
        assert row.terminal == receipt.terminal.revision
        with sqlite3.connect(mounted.database) as connection:
            members = connection.execute(
                "SELECT record_id, schema_id, commit_sequence FROM records "
                "WHERE tenant_id=? AND record_id IN (?, ?) ORDER BY rowid",
                (
                    "hermetic-tenant",
                    receipt.terminal.subject_id,
                    receipt.result.subject_id,
                ),
            ).fetchall()
            assert [member[:2] for member in members] == [
                (receipt.terminal.subject_id, CANCELLATION_SCHEMA),
                (receipt.result.subject_id, NOT_EXECUTED_SCHEMA),
            ]
            assert len({member[2] for member in members}) == 1
            assert connection.execute(
                "SELECT count(*) FROM records WHERE tenant_id=? AND commit_sequence=?",
                ("hermetic-tenant", members[0][2]),
            ).fetchone() == (2,)
