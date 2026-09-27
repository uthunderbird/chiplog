"""Native H1 CLI execution inputs shared by composition tests."""

from __future__ import annotations

import asyncio
import hashlib
import struct
from pathlib import Path

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
    CommonCliExecutionRuntime,
    open_common_cli_execution_runtime,
)
from chiplog.composition.common_execution_driver_contracts import (
    AdvanceExecutionRequestV1,
    CliRetainedSelectedSourceV1,
    DriveInputRequestV1,
    DriverCommandIdentityV1,
    SelectedExecutionReceiptV1,
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


async def admit_complete_script(database: Path, custody: Path) -> tuple[DriveInputRequestV1, bytes]:
    """Persist a selected CLI input and derive its sole native complete response."""
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


def advance(
    initial: SelectedExecutionReceiptV1, request: DriveInputRequestV1
) -> AdvanceExecutionRequestV1:
    """Advance exactly from the selected head in an admitted CLI request."""
    return AdvanceExecutionRequestV1(
        identity=request.identity,
        original_driver_command_fingerprint=request.original_driver_command_fingerprint(),
        expected_selected_run_head=initial.selected_run_head,
    )
