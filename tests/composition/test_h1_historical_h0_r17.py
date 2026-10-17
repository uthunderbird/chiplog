"""Real raw-journal checks for the bounded H0/R17 reader."""

from __future__ import annotations

import asyncio
import hashlib
import json
import struct
from pathlib import Path

import pytest

from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.adapters.driven.loop_prompts import OwnedStaticPrompts
from chiplog.capabilities.agent_loop.application import AgentLoop
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy, EndpointSelection
from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead
from chiplog.capabilities.deployment_trust.cli_custody_contracts import (
    CliCustodyOffer,
    CliCustodyResponse,
)
from chiplog.capabilities.deployment_trust.h1_broker_evidence_contracts import (
    H1RetainedSelectedWrapperV1,
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
from chiplog.composition.h1_historical_h0_r17 import read_historical_h0_r17
from chiplog.composition.r14_execution_inbox_records import (
    EXECUTION_INBOX_INITIALIZATION_OPERATION,
)
from chiplog.composition.r14_loop_store import R14LoopStore
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.composition.r16_effects import R16EffectsProducer
from chiplog.platform.ingress_authenticated_contracts import AuthenticatedCustodyRecord
from chiplog.platform.ingress_custody_records import digest
from chiplog.platform.ingress_record_contracts import canonical_ingress_record_bytes
from chiplog.platform.ingress_transition_contracts import (
    IngressCommandIdentity,
    RetainedIngressSource,
)
from tests.support.composition.plan_effect_publication import response


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


async def _retained(
    runtime: CommonCliExecutionRuntime, slot: str = "h0-r17"
) -> H1RetainedSelectedWrapperV1:
    runtime.provision_retained(slot, b"selected raw CLI input")
    await runtime.allocate_receipt(slot)
    await runtime.stage_receipt(slot)
    token = next(
        item.token.token_id
        for item in runtime.ingress_history().custody.entries
        if item.token.receive_slot == slot
    )
    async with runtime.cli_custody(slot) as custody:
        peer = asyncio.create_task(_client(custody.socket.path))
        assert (await custody.admit()).kind == "COMMITTED"
        await peer
    admitted = runtime.read_admitted_inbox(token)
    assert admitted is not None
    identity = IngressCommandIdentity(
        tenant_id="hermetic-tenant", database_id="hermetic-database", command_id=token
    )
    retained_source = RetainedIngressSource(
        source=admitted.record.command.retention.proof,
        reader_id=R17_RETAINED_READER_ID,
        schema_id="chiplog.ingress.retained-source-observation.v1",
        canonical_source_bytes=admitted.record.command.retention.observation_bytes,
    )
    wire = DriveInputRequestV1(
        identity=DriverCommandIdentityV1(
            tenant_id="hermetic-tenant",
            database_id="hermetic-database",
            driver_command_id="driver:" + token,
            original_ingress_identity=identity,
            original_ingress_request_fingerprint=admitted.record.inbox.authentication_request_fingerprint,
        ),
        selected_source=CliRetainedSelectedSourceV1(
            original_ingress_identity=identity,
            source_binding=admitted.record.command.token.source,
            selected_ingress_decision=admitted.selected_decision,
            source_head=retained_source.source,
            retained_source=retained_source,
            expected_reader_id=R17_RETAINED_READER_ID,
        ),
    )
    receipt = await runtime.drive_input(wire)
    assert isinstance(receipt, SelectedExecutionReceiptV1)
    initialization = next(
        raw
        for decision_id, _, raw in runtime._loop_decisions().entries()
        if decision_id == receipt.selected_journal_decision.head
        and json.loads(raw).get("kind") == "DECIDED"
        and json.loads(raw).get("operation_kind") == EXECUTION_INBOX_INITIALIZATION_OPERATION
    )
    raw_record = canonical_ingress_record_bytes(admitted.record)
    return H1RetainedSelectedWrapperV1(
        initialization_envelope_bytes=initialization,
        admitted_record_bytes=raw_record,
        selected_admitted_record_ref=ExactHead(**admitted.physical_record.model_dump()),
        authentication_result_bytes=admitted.record.command.authentication_result_bytes,
        admitted_record_digest=digest(raw_record),
    )


async def _publish_ordinary(database: Path, resources: HermeticDispatchResources) -> None:
    async with open_common_cli_execution_runtime(
        database, resources=resources, responses=(response(),)
    ) as runtime:
        model = HermeticModel((response(),))
        model.session = runtime
        loop = AgentLoop(
            R14LoopStore(runtime),
            model,
            OwnedStaticPrompts(),
            tenant="hermetic-tenant",
            principal="hermetic-principal",
            origin=EndpointSelection(
                kind="ORIGIN_EXACT",
                ingress_binding_head="hermetic-ingress-v1",
                endpoint_head="hermetic-endpoint-v1",
                endpoint_id="hermetic-local",
                provider="hermetic-local",
                recipient="hermetic-principal",
                canonical_address="local://hermetic-principal",
                credential_binding_head="hermetic-v1",
            ),
            contour_head="hermetic-contour-v1",
            policy_head="hermetic-policy-v1",
            worker_session=runtime.current_worker(),
            planning=runtime,
        )
        created = await loop.create("r", "Propose my exact hermetic action", BudgetPolicy())
        active = await loop.activate("r", created.head)
        await loop.step("r", active.head)
        display = await R16EffectsProducer(runtime).display_effect("r/turn/1/proposal/effect")
        await runtime.publish_effect(
            "hermetic-ingress", display.display_id, display.display_digest, display.adoption_act_id
        )


@pytest.mark.asyncio
async def test_reads_real_selected_cli_h0_and_r17_without_current_reader(tmp_path: Path) -> None:
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "custody"
    )
    async with open_common_cli_execution_runtime(
        tmp_path / "state.sqlite", resources=resources
    ) as runtime:
        retained = await _retained(runtime)
        result = read_historical_h0_r17(runtime, retained)

    assert result.initialization_decision_id
    assert result.admitted_decision.prepared.request.identity.tenant_id == "hermetic-tenant"


@pytest.mark.asyncio
async def test_historical_r17_after_restart_accepts_prior_ordinary_claim(tmp_path: Path) -> None:
    database, custody = tmp_path / "state.sqlite", tmp_path / "custody"
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM", "CONFIRM"), cap=2, custody_path=custody
    )
    await _publish_ordinary(database, resources)
    async with open_common_cli_execution_runtime(database, resources=resources) as runtime:
        retained = await _retained(runtime)
    async with open_common_cli_execution_runtime(database, resources=resources) as reopened:
        result = read_historical_h0_r17(reopened, retained)

    assert result.admitted_record.command.token.receive_slot == "h0-r17"


@pytest.mark.asyncio
async def test_rehashed_r17_record_not_selected_is_rejected(tmp_path: Path) -> None:
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "custody"
    )
    async with open_common_cli_execution_runtime(
        tmp_path / "state.sqlite", resources=resources
    ) as runtime:
        retained = await _retained(runtime)
        original = AuthenticatedCustodyRecord.model_validate_json(retained.admitted_record_bytes)
        forged_record = original.model_copy(
            update={
                "command": original.command.model_copy(
                    update={"raw_bytes": b"rehashed but unselected"}
                )
            }
        )
        forged = canonical_ingress_record_bytes(forged_record)
        fingerprint = digest(forged)
        ref = retained.selected_admitted_record_ref
        forged_retained = retained.model_copy(
            update={
                "admitted_record_bytes": forged,
                "admitted_record_digest": fingerprint,
                "selected_admitted_record_ref": ExactHead(
                    identity=ref.identity,
                    head=ref.identity + "/" + fingerprint,
                    fingerprint=fingerprint,
                ),
            }
        )
        with pytest.raises(ValueError):
            read_historical_h0_r17(runtime, forged_retained)


@pytest.mark.asyncio
async def test_second_selected_r17_does_not_shadow_the_first(tmp_path: Path) -> None:
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM", "CONFIRM"), cap=2, custody_path=tmp_path / "custody"
    )
    async with open_common_cli_execution_runtime(
        tmp_path / "state.sqlite", resources=resources
    ) as runtime:
        await _retained(runtime, "first")
        second = await _retained(runtime, "second")
        result = read_historical_h0_r17(runtime, second)

    assert result.admitted_record.command.token.receive_slot == "second"
