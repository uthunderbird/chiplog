"""Shared builders extracted without changing their behavior."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from tests.support.h1_cli_execution import _admit, _client
from tests.support.h1_installed_launch import TENANT

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.delivery_preparation import (
    Commentary,
    DeliveryCompletion,
    ProposedDelivery,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.composition.common_cli_execution_runtime import (
    CommonCliExecutionRuntime,
)
from chiplog.composition.common_execution_driver_contracts import (
    CliRetainedSelectedSourceV1,
    DriveInputRequestV1,
    DriverCommandIdentityV1,
    SelectedExecutionReceiptV1,
)
from chiplog.composition.h1_preseal_contracts import H1SelectedPrepare
from chiplog.composition.h1_selected_prepare import select_h1_v3_prepare_for_candidate
from chiplog.composition.r14_execution_complete_seal_records import RetainedExecutionCompleteSealV2
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform.ingress_transition_contracts import (
    IngressCommandIdentity,
    RetainedIngressSource,
)


def _resources(tmp_path: Path, label: str = "") -> HermeticDispatchResources:
    return HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / f"dispatch-custody{label}"
    )


async def _admit_other(runtime: CommonCliExecutionRuntime) -> DriveInputRequestV1:
    slot = "recovery-stage-source-later"
    runtime.provision_retained(slot, b"later unrelated input")
    await runtime.allocate_receipt(slot)
    await runtime.stage_receipt(slot)
    async with runtime.cli_custody(slot) as custody:
        peer = asyncio.create_task(_client(custody.socket.path))
        assert (await custody.admit()).kind == "COMMITTED"
        await peer
    token = runtime.ingress_history().custody.entries[-1].token.token_id
    admitted = runtime.read_admitted_inbox(token)
    assert admitted is not None
    record = admitted.record
    identity = IngressCommandIdentity(
        tenant_id=TENANT, database_id="hermetic-database", command_id=token
    )
    retained = RetainedIngressSource(
        source=record.command.retention.proof,
        reader_id="r17-retained-reader.v1",
        schema_id="chiplog.ingress.retained-source-observation.v1",
        canonical_source_bytes=record.command.retention.observation_bytes,
    )
    return DriveInputRequestV1(
        identity=DriverCommandIdentityV1(
            tenant_id=TENANT,
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
            expected_reader_id="r17-retained-reader.v1",
        ),
    )


async def _v2_seal_with_historical_v3_prepare(
    runtime: CommonCliExecutionRuntime,
) -> tuple[DriveInputRequestV1, CallSubjectHead, H1SelectedPrepare]:
    request = await _admit(runtime)
    initial = await runtime.drive_input(request)
    assert isinstance(initial, SelectedExecutionReceiptV1)
    runtime._execution_model._responses = (
        DeliveryCompletion(
            tenant=TENANT,
            run_id=initial.stable_run_lineage_id,
            turn_id=initial.stable_run_lineage_id + "/turn/1",
            deliveries=(ProposedDelivery(payload=(Commentary(text="historical completion"),)),),
        ).canonical_bytes(),
    )
    started = await runtime.begin_execution(
        "hermetic-ingress", initial.stable_run_lineage_id, initial.selected_run_head.head
    )
    captured = await runtime.capture_execution(
        "hermetic-ingress", initial.stable_run_lineage_id, started.head
    )
    # The V3 Prepare is selected pre-seal; recovery must reopen it from native
    # history, never receive it from this test or from the V2 journal record.
    selected_prepare = select_h1_v3_prepare_for_candidate(
        runtime, captured, expected_head=captured.head
    )
    sealed = await runtime.seal_execution_complete(
        "hermetic-ingress", initial.stable_run_lineage_id, captured.head, profile="H1_V2"
    )
    raw = next(
        raw
        for _, _, raw in runtime._loop_decisions().entries()
        if json.loads(raw).get("operation_id") == sealed.head
    )
    retained = RetainedExecutionCompleteSealV2.model_validate_json(
        json.loads(raw)["execution_complete_seal"]
    )
    response_seal = retained.exchange.proposal.fan_out.response_seal
    locator = CallSubjectHead(
        subject_id=response_seal.response_seal_id,
        revision=Present(
            head="record:" + response_seal.digest(), fingerprint=response_seal.digest()
        ),
    )
    return request, locator, selected_prepare
