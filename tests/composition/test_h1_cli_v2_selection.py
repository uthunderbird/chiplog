"""Public CLI selection coverage for the H1 V2 complete-seal profile."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import sqlite3
import struct
from pathlib import Path
from typing import Literal, cast

import pytest

from chiplog.capabilities.agent_loop.contracts import ToolCall
from chiplog.capabilities.agent_loop.delivery_preparation import (
    Commentary,
    DeliveryCompletion,
    ProposedDelivery,
)
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionContinue
from chiplog.capabilities.agent_loop.recovery_frontier_registry_contracts import (
    execution_h1_zero_call_frontier_registry_v2,
    execution_zero_call_frontier_registry,
)
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
    AdvanceExecutionRequestV1,
    CliRetainedSelectedSourceV1,
    DriveInputRequestV1,
    DriverCommandIdentityV1,
    ExecutionDriverRejectedV1,
    SelectedExecutionReceiptV1,
)
from chiplog.composition.r14_execution_complete_seal_records import (
    ExecutionCompleteSealPhysicalEnvelopeV2,
    RetainedExecutionCompleteSeal,
    RetainedExecutionCompleteSealV2,
    complete_seal_physical_command,
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


async def _admit(runtime: CommonCliExecutionRuntime) -> DriveInputRequestV1:
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


async def _admit_complete_script(
    database: Path, custody: Path
) -> tuple[DriveInputRequestV1, bytes]:
    """Create an admitted input, then derive the only response for its native Run."""
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1, custody_path=custody)
    async with open_common_cli_execution_runtime(database, resources=resources) as runtime:
        request = await _admit(runtime)
        admitted = runtime._selected_input(request, resources.observe())
        run_id = runtime._run_id(admitted)
    complete = DeliveryCompletion(
        tenant="hermetic-tenant",
        run_id=run_id,
        turn_id=run_id + "/turn/1",
        deliveries=(ProposedDelivery(payload=(Commentary(text="Hello"),)),),
    )
    return request, complete.canonical_bytes()


async def _admit_continue_script(
    database: Path, custody: Path
) -> tuple[DriveInputRequestV1, bytes]:
    """Create a real selected CLI candidate whose captured response has a call."""
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1, custody_path=custody)
    async with open_common_cli_execution_runtime(database, resources=resources) as runtime:
        request = await _admit(runtime)
    return request, ExecutionContinue(
        kind="Continue",
        tool_calls=(ToolCall(call_id="plan", tool="propose_planning", text="Plan"),),
    ).model_dump_json().encode()


def _advance(
    initial: SelectedExecutionReceiptV1, request: DriveInputRequestV1
) -> AdvanceExecutionRequestV1:
    return AdvanceExecutionRequestV1(
        identity=request.identity,
        original_driver_command_fingerprint=request.original_driver_command_fingerprint(),
        expected_selected_run_head=initial.selected_run_head,
    )


def _selected_complete_seal(runtime: CommonCliExecutionRuntime) -> dict[str, object]:
    seals = [
        json.loads(raw)
        for _, _, raw in runtime._loop_decisions().entries()
        if json.loads(raw).get("execution_complete_seal") is not None
    ]
    assert len(seals) == 1
    return cast(dict[str, object], seals[0])


def _physical_records(database: Path, commit_sequence: int) -> list[tuple[str, str, str, bytes]]:
    with sqlite3.connect(database) as connection:
        return connection.execute(
            "SELECT record_id, owner, schema_id, canonical_bytes FROM records "
            "WHERE commit_sequence=? ORDER BY rowid",
            (commit_sequence,),
        ).fetchall()


@pytest.mark.asyncio
async def test_cli_h1_v2_ineligible_complete_rejects_without_v1_fallback(tmp_path: Path) -> None:
    """A real call-bearing CLI response cannot silently choose a V1 complete seal."""
    database = tmp_path / "ineligible.sqlite"
    custody = tmp_path / "dispatch-custody"
    request, continue_response = await _admit_continue_script(database, custody)
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1, custody_path=custody)
    async with open_common_cli_execution_runtime(
        database, resources=resources, responses=(continue_response,)
    ) as runtime:
        initial = await runtime.drive_input(request)
        assert isinstance(initial, SelectedExecutionReceiptV1)

        result = await runtime.advance_execution(_advance(initial, request))

        assert isinstance(result, ExecutionDriverRejectedV1)
        assert result.code == "STALE"
        assert "eligible zero-call Complete" in result.reason
        assert len(runtime._execution_model.requests) == 1
        assert not [
            raw
            for _, _, raw in runtime._loop_decisions().entries()
            if json.loads(raw).get("execution_complete_seal") is not None
        ]


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", [None, "V1"])
async def test_complete_seal_default_and_explicit_v1_keep_v1_registry_and_replay(
    tmp_path: Path, profile: Literal["V1"] | None
) -> None:
    """The public complete-seal API retains its V1 default and explicit V1 bytes."""
    database = tmp_path / f"v1-{profile or 'default'}.sqlite"
    custody = tmp_path / "dispatch-custody"
    request, complete = await _admit_complete_script(database, custody)
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1, custody_path=custody)
    async with open_common_cli_execution_runtime(
        database, resources=resources, responses=(complete,)
    ) as runtime:
        initial = await runtime.drive_input(request)
        assert isinstance(initial, SelectedExecutionReceiptV1)
        started = await runtime.begin_execution(
            "hermetic-ingress", initial.stable_run_lineage_id, initial.selected_run_head.head
        )
        captured = await runtime.capture_execution(
            "hermetic-ingress", initial.stable_run_lineage_id, started.head
        )
        if profile is None:
            sealed = await runtime.seal_execution_complete(
                "hermetic-ingress", initial.stable_run_lineage_id, captured.head
            )
        else:
            sealed = await runtime.seal_execution_complete(
                "hermetic-ingress", initial.stable_run_lineage_id, captured.head, profile=profile
            )
        selected = _selected_complete_seal(runtime)
        retained = RetainedExecutionCompleteSeal.model_validate_json(
            cast(str, selected["execution_complete_seal"])
        )
        assert type(retained) is RetainedExecutionCompleteSeal
        assert (
            retained.canonical_registry_base64
            == base64.b64encode(execution_zero_call_frontier_registry().canonical_bytes()).decode()
        )
        records = _physical_records(database, cast(int, selected["expected_head"]) + 1)
        assert len(records) == 3
        assert records[-1][3] == execution_zero_call_frontier_registry().canonical_bytes()

    async with open_common_cli_execution_runtime(database, resources=resources) as reopened:
        replay = await reopened.advance_execution(_advance(initial, request))
        assert isinstance(replay, SelectedExecutionReceiptV1)
        assert replay.disposition == "EXACT_REPLAY"
        assert replay.selected_run_head.head == sealed.head
        assert len(reopened._execution_model.requests) == 0
        assert _selected_complete_seal(reopened) == selected


@pytest.mark.asyncio
async def test_cli_h1_v2_selected_complete_publishes_v2_and_reopens_once(tmp_path: Path) -> None:
    """The real mounted H1 route selects exactly one V2 physical complete seal."""
    database = tmp_path / "h1-v2.sqlite"
    custody = tmp_path / "dispatch-custody"
    request, complete = await _admit_complete_script(database, custody)
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1, custody_path=custody)
    async with open_common_cli_execution_runtime(
        database, resources=resources, responses=(complete,)
    ) as runtime:
        initial = await runtime.drive_input(request)
        assert isinstance(initial, SelectedExecutionReceiptV1)
        committed = await runtime.advance_execution(_advance(initial, request))

        assert isinstance(committed, SelectedExecutionReceiptV1)
        selected = _selected_complete_seal(runtime)
        retained = RetainedExecutionCompleteSealV2.model_validate_json(
            cast(str, selected["execution_complete_seal"])
        )
        assert type(retained) is RetainedExecutionCompleteSealV2
        assert cast(str, selected["execution_complete_seal"]) == retained.canonical_bytes().decode()
        expected_registry = execution_h1_zero_call_frontier_registry_v2().canonical_bytes()
        envelope = ExecutionCompleteSealPhysicalEnvelopeV2.model_validate_json(
            cast(str, selected["execution_complete_seal_envelope"])
        )
        assert (
            cast(str, selected["execution_complete_seal_envelope"])
            == envelope.canonical_bytes().decode()
        )
        expected_command = complete_seal_physical_command(envelope)
        records = _physical_records(database, committed.commit_sequence)
        assert len(records) == 3
        assert records == [
            (member.record_id, member.owner, member.schema_id, member.canonical_bytes)
            for member in expected_command.records
        ]
        assert records[-1][3] == expected_registry
        before_decisions = runtime._loop_decisions().entries()
        before_records = records

    async with open_common_cli_execution_runtime(database, resources=resources) as reopened:
        replay = await reopened.advance_execution(_advance(initial, request))
        assert isinstance(replay, SelectedExecutionReceiptV1)
        assert replay.disposition == "EXACT_REPLAY"
        assert replay.selected_journal_decision == committed.selected_journal_decision
        assert reopened._execution_model.requests == []
        assert reopened._loop_decisions().entries() == before_decisions
        assert _physical_records(database, committed.commit_sequence) == before_records
