from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import struct
from pathlib import Path

import pytest

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
    LookupExecutionRequestV1,
)
from chiplog.composition.r14_execution_inbox_records import (
    EXECUTION_INBOX_INITIALIZATION_OPERATION,
)
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
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
