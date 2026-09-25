"""Mounted native H0/R17 input-role witness checks."""

from __future__ import annotations

import asyncio
import hashlib
import struct
from pathlib import Path
from typing import cast

import pytest

from chiplog.capabilities.agent_loop.delivery_preparation import (
    Commentary,
    DeliveryCompletion,
    ProposedDelivery,
)
from chiplog.capabilities.deployment_trust.cli_custody_contracts import (
    CliCustodyOffer,
    CliCustodyResponse,
)
from chiplog.composition.common_cli_execution_runtime import (
    R17_RETAINED_READER_ID,
    open_common_cli_execution_runtime,
)
from chiplog.composition.common_execution_driver_contracts import (
    CliRetainedSelectedSourceV1,
    DriveInputRequestV1,
    DriverCommandIdentityV1,
    SelectedExecutionReceiptV1,
)
from chiplog.composition.h1_selected_input_role import read_h1_selected_input_role
from chiplog.composition.h1_selected_prepare import select_h1_v3_prepare_for_candidate
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform.ingress_custody_records import canonical
from chiplog.platform.ingress_transition_contracts import (
    IngressCommandIdentity,
    RetainedIngressSource,
)


async def _client(path: Path) -> None:
    reader, writer = await asyncio.open_unix_connection(path)
    try:
        size = struct.unpack("!I", await reader.readexactly(4))[0]
        offer = CliCustodyOffer.model_validate_json(await reader.readexactly(size))
        reply = CliCustodyResponse(
            challenge_fingerprint=hashlib.sha256(offer.challenge.canonical_bytes()).hexdigest()
        ).canonical_bytes()
        writer.write(struct.pack("!I", len(reply)) + reply)
        await writer.drain()
        writer.write_eof()
    finally:
        writer.close()
        await writer.wait_closed()


async def _admit(runtime: object) -> DriveInputRequestV1:
    runtime.provision_retained("selected", b"selected original input")  # type: ignore[attr-defined]
    await runtime.allocate_receipt("selected")  # type: ignore[attr-defined]
    await runtime.stage_receipt("selected")  # type: ignore[attr-defined]
    token = runtime.ingress_history().custody.entries[0].token.token_id  # type: ignore[attr-defined]
    async with runtime.cli_custody("selected") as custody:  # type: ignore[attr-defined]
        peer = asyncio.create_task(_client(custody.socket.path))
        assert (await custody.admit()).kind == "COMMITTED"
        await peer
    admitted = runtime.read_admitted_inbox(token)  # type: ignore[attr-defined]
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


async def _mounted_candidate(database: Path, custody: Path) -> tuple[DriveInputRequestV1, bytes]:
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


@pytest.mark.asyncio
async def test_mounted_selected_h0_r17_witness_uses_retained_not_current_input(
    tmp_path: Path,
) -> None:
    database, custody = tmp_path / "state.sqlite", tmp_path / "custody"
    request, complete = await _mounted_candidate(database, custody)
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1, custody_path=custody)
    async with open_common_cli_execution_runtime(
        database, resources=resources, responses=(complete,)
    ) as runtime:
        initial = cast(SelectedExecutionReceiptV1, await runtime.drive_input(request))
        started = await runtime.begin_execution(
            "hermetic-ingress", initial.stable_run_lineage_id, initial.selected_run_head.head
        )
        captured = await runtime.capture_execution(
            "hermetic-ingress", initial.stable_run_lineage_id, started.head
        )
        selected = select_h1_v3_prepare_for_candidate(
            runtime, captured, expected_head=captured.head
        )
        object.__setattr__(
            runtime,
            "_selected_input",
            lambda *_args: (_ for _ in ()).throw(AssertionError("live read")),
        )
        witness = read_h1_selected_input_role(runtime, selected, captured)

    assert tuple(item.reason for item in witness.occurrences) == (
        "H0_NATIVE_INPUT",
        "R17_ALLOCATION_INPUT",
        "R17_STAGED_INPUT",
        "R17_ADMITTED_INPUT",
    )
    assert tuple(
        decision.prepared.request.operation for decision in witness.inbound_owner_decisions
    ) == (
        "ingress.allocate_receipt_token",
        "ingress.stage_raw_bytes",
        "ingress.publish_custody_successor",
    )
    assert witness.occurrences[0].record_id == witness.initialization.proposal.run.head
    allocation, staging, admission = witness.inbound_owner_decisions
    assert (
        witness.occurrences[1].canonical_bytes
        == allocation.prepared.request.complete_records[0].canonical_bytes
    )
    assert (
        witness.occurrences[2].canonical_bytes
        == staging.prepared.request.complete_records[0].canonical_bytes
    )
    assert (
        witness.occurrences[3].canonical_bytes
        == admission.prepared.request.complete_records[0].canonical_bytes
    )
    assert witness.occurrences[3].canonical_bytes == canonical(witness.admitted_record)


@pytest.mark.asyncio
async def test_selected_input_role_rejects_a_captured_run_not_in_physical_ancestry(
    tmp_path: Path,
) -> None:
    database, custody = tmp_path / "state.sqlite", tmp_path / "custody"
    request, complete = await _mounted_candidate(database, custody)
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1, custody_path=custody)
    async with open_common_cli_execution_runtime(
        database, resources=resources, responses=(complete,)
    ) as runtime:
        initial = cast(SelectedExecutionReceiptV1, await runtime.drive_input(request))
        started = await runtime.begin_execution(
            "hermetic-ingress", initial.stable_run_lineage_id, initial.selected_run_head.head
        )
        captured = await runtime.capture_execution(
            "hermetic-ingress", initial.stable_run_lineage_id, started.head
        )
        selected = select_h1_v3_prepare_for_candidate(
            runtime, captured, expected_head=captured.head
        )
        with pytest.raises(ValueError, match="captured Run is absent"):
            read_h1_selected_input_role(
                runtime, selected, captured.model_copy(update={"head": "loop:forged"})
            )
