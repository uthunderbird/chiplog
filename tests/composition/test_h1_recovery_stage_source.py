"""RED contracts for the historical H1 COMPLETION-stage source boundary."""

from __future__ import annotations

import asyncio
import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.delivery_preparation import (
    Commentary,
    DeliveryCompletion,
    ProposedDelivery,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.composition.common_cli_execution_runtime import (
    CommonCliExecutionRuntime,
    open_installed_h1_runtime,
)
from chiplog.composition.common_execution_driver_contracts import (
    CliRetainedSelectedSourceV1,
    DriveInputRequestV1,
    DriverCommandIdentityV1,
    SelectedExecutionReceiptV1,
)
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.h1_preseal_contracts import H1SelectedPrepare
from chiplog.composition.h1_recovery_stage_source import (
    H1RecoveryStageSource,
    H1RecoveryStageSourceError,
)
from chiplog.composition.h1_selected_prepare import select_h1_v3_prepare_for_candidate
from chiplog.composition.h1_v2_recovery_native_source import (
    H1V2RecoveryNativeCut,
    H1V2RecoveryNativeSource,
)
from chiplog.composition.r14_execution_complete_seal_records import RetainedExecutionCompleteSealV2
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform.ingress_transition_contracts import (
    IngressCommandIdentity,
    RetainedIngressSource,
)
from tests.support.h1_cli_execution import _admit, _client
from tests.support.h1_installed_launch import TENANT, installed_slot, prepare_installed_slot


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


@pytest.mark.asyncio
async def test_stage_source_reopens_v3_prepare_and_v2_seal_as_one_issued_native_cut(
    tmp_path: Path,
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            request, locator, selected_prepare = await _v2_seal_with_historical_v3_prepare(runtime)

            reader = H1RecoveryStageSource(runtime)
            issued = reader.issue_completion_native(
                original_identity=request.identity,
                original_fingerprint=request.original_driver_command_fingerprint(),
                selected_seal=locator,
            )
            native = reader.replay_completion_native(issued)

            assert native.selected_response_seal == locator
            assert native.run.head == issued.native_cut.source.complete_ordered_run_lineage[-1].head
            assert selected_prepare.decision_id

            # A dataclass copy has no issuer identity and cannot be used as a
            # recovery capability even when every visible field is identical.
            with pytest.raises(H1RecoveryStageSourceError, match="issuer-owned"):
                reader.replay_completion_native(dataclasses.replace(issued))


@pytest.mark.asyncio
async def test_stage_source_replays_selected_prefix_after_later_unrelated_publication(
    tmp_path: Path,
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            request, locator, _prepare = await _v2_seal_with_historical_v3_prepare(runtime)
            reader = H1RecoveryStageSource(runtime)
            issued = reader.issue_completion_native(
                original_identity=request.identity,
                original_fingerprint=request.original_driver_command_fingerprint(),
                selected_seal=locator,
            )
            before = reader.replay_completion_native(issued)

        async with open_installed_h1_runtime(
            launch, resources=_resources(tmp_path, "-later")
        ) as runtime:
            await runtime.drive_input(await _admit_other(runtime))
            reader = H1RecoveryStageSource(runtime)
            replayed = reader.issue_completion_native(
                original_identity=request.identity,
                original_fingerprint=request.original_driver_command_fingerprint(),
                selected_seal=locator,
            )

            assert reader.replay_completion_native(replayed) == before


@pytest.mark.asyncio
async def test_stage_source_rejects_a_changed_replayed_native_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            request, locator, _prepare = await _v2_seal_with_historical_v3_prepare(runtime)
            reader = H1RecoveryStageSource(runtime)
            issued = reader.issue_completion_native(
                original_identity=request.identity,
                original_fingerprint=request.original_driver_command_fingerprint(),
                selected_seal=locator,
            )
            select = H1V2RecoveryNativeSource.select

            def changed_select(
                self: H1V2RecoveryNativeSource, **kwargs: Any
            ) -> H1V2RecoveryNativeCut:
                selected = select(self, **kwargs)
                return dataclasses.replace(
                    selected,
                    seal=dataclasses.replace(selected.seal, raw_bytes=b'{"changed":true}'),
                )

            monkeypatch.setattr(H1V2RecoveryNativeSource, "select", changed_select)

            with pytest.raises(H1RecoveryStageSourceError, match="selected native source"):
                reader.replay_completion_native(issued)


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason=(
        "P owns the historical delivery/fence reconstruction and exposes only a current, "
        "opaque receipt; no installed historical P bridge exists"
    ),
)
async def test_stage_source_derives_canonical_completion_input_from_historical_native_and_p(
    tmp_path: Path,
) -> None:
    """The eventual seam must return a canonical request without caller DTO input."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            request, locator, _prepare = await _v2_seal_with_historical_v3_prepare(runtime)
            reader = H1RecoveryStageSource(runtime)
            issued = reader.issue_completion_native(
                original_identity=request.identity,
                original_fingerprint=request.original_driver_command_fingerprint(),
                selected_seal=locator,
            )

            completion = reader.reconstruct_completion_input(issued)

            facts = reader.replay_completion_native(issued)
            assert completion.kind == "PREPARE_EXECUTION_COMPLETION_FIRST_PATH_V2"
            assert completion.run == facts.run
            assert completion.selected_attempt == facts.selected_attempt
            assert completion.exact_captured_response == facts.exact_captured_response
            assert completion.delivery.captured_response == facts.exact_captured_response
            assert completion.fence.run_head == facts.run.head
