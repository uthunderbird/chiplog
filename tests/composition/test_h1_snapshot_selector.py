"""V3 selected-seal replay is bound to its authenticated checkpoint cut."""

from __future__ import annotations

import asyncio
import hashlib
import json
import struct
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast

import pytest

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy
from chiplog.capabilities.agent_loop.delivery_preparation import (
    Commentary,
    DeliveryCompletion,
    ProposedDelivery,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Present
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
from chiplog.composition.h1_selected_prepare import select_h1_v3_prepare_for_seal
from chiplog.composition.r14_execution_complete_seal_records import RetainedExecutionCompleteSealV3
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform.ingress_transition_contracts import (
    IngressCommandIdentity,
    RetainedIngressSource,
)


async def _custody_client(path: Path) -> None:
    reader, writer = await asyncio.open_unix_connection(path)
    try:
        size = struct.unpack("!I", await reader.readexactly(4))[0]
        from chiplog.capabilities.deployment_trust.cli_custody_contracts import (
            CliCustodyOffer,
            CliCustodyResponse,
        )

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


async def _admit_complete_script(
    database: Path, custody: Path
) -> tuple[DriveInputRequestV1, bytes]:
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1, custody_path=custody)
    async with open_common_cli_execution_runtime(database, resources=resources) as runtime:
        runtime.provision_retained("slot", b"make a native run")
        await runtime.allocate_receipt("slot")
        await runtime.stage_receipt("slot")
        token = runtime.ingress_history().custody.entries[0].token.token_id
        async with runtime.cli_custody("slot") as session:
            peer = asyncio.create_task(_custody_client(session.socket.path))
            assert (await session.admit()).kind == "COMMITTED"
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
        admitted_input = runtime._selected_input(request, resources.observe())
        run_id = runtime._run_id(admitted_input)
    complete = DeliveryCompletion(
        tenant="hermetic-tenant",
        run_id=run_id,
        turn_id=run_id + "/turn/1",
        deliveries=(ProposedDelivery(payload=(Commentary(text="Hello"),)),),
    )
    return request, complete.canonical_bytes()


@asynccontextmanager
async def _selected_v3_runtime(
    tmp_path: Path,
) -> AsyncIterator[tuple[CommonCliExecutionRuntime, CallSubjectHead]]:
    database = tmp_path / "historical-v3.sqlite"
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
        await runtime.seal_execution_complete(
            "hermetic-ingress", initial.stable_run_lineage_id, captured.head, profile="H1_V3"
        )
        decision = next(
            json.loads(raw)
            for _, _, raw in runtime._loop_decisions().entries()
            if json.loads(raw).get("execution_complete_seal") is not None
            and json.loads(raw)["execution_complete_seal"].find("V3") >= 0
        )
        retained = RetainedExecutionCompleteSealV3.model_validate_json(
            cast(str, decision["execution_complete_seal"])
        )
        seal = retained.exchange.proposal.fan_out.response_seal
        locator = CallSubjectHead(
            subject_id=seal.response_seal_id,
            revision=Present(head="record:" + seal.digest(), fingerprint=seal.digest()),
        )
        yield runtime, locator


@pytest.mark.asyncio
async def test_historical_v3_selector_uses_checkpoint_after_later_same_tenant_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _selected_v3_runtime(tmp_path) as (runtime, locator):
        expected = select_h1_v3_prepare_for_seal(runtime, selected_seal=locator, historical=True)
        await runtime.create_execution("hermetic-ingress", "later", "Later", BudgetPolicy())

        def no_current_authority_sql(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("historical selector opened current authority SQLite")

        with monkeypatch.context() as scoped:
            scoped.setattr(
                "chiplog.composition.h1_selected_prepare.sqlite3.connect", no_current_authority_sql
            )
            actual = select_h1_v3_prepare_for_seal(runtime, selected_seal=locator, historical=True)

        assert actual == expected
