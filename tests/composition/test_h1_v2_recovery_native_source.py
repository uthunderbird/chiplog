"""Historical native selection for an installed H1 V2 seal."""

from __future__ import annotations

import asyncio
import hashlib
import json

import pytest

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.delivery_preparation import (
    Commentary,
    DeliveryCompletion,
    ProposedDelivery,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.composition.common_cli_execution_runtime import (
    R17_RETAINED_READER_ID,
    open_installed_h1_runtime,
)
from chiplog.composition.common_execution_driver_contracts import (
    CliRetainedSelectedSourceV1,
    DriveInputRequestV1,
    DriverCommandIdentityV1,
)
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.h1_postseal_recovery_source import H1PostSealRecoveryRootSource
from chiplog.composition.h1_v2_recovery_native_source import H1V2RecoveryNativeSource
from chiplog.composition.r14_execution_complete_seal_records import RetainedExecutionCompleteSealV2
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform.ingress_transition_contracts import (
    IngressCommandIdentity,
    RetainedIngressSource,
)
from tests.support.h1_cli_execution import _admit, _client
from tests.support.h1_installed_launch import TENANT, installed_slot, prepare_installed_slot


async def _admit_other(runtime) -> DriveInputRequestV1:
    slot = "historical-later-unrelated"
    runtime.provision_retained(slot, b"later unrelated input")
    await runtime.allocate_receipt(slot)
    await runtime.stage_receipt(slot)
    token = runtime.ingress_history().custody.entries[-1].token.token_id
    async with runtime.cli_custody(slot) as custody:
        peer = asyncio.create_task(_client(custody.socket.path))
        assert (await custody.admit()).kind == "COMMITTED"
        await peer
    admitted = runtime.read_admitted_inbox(token)
    assert admitted is not None
    record = admitted.record
    identity = IngressCommandIdentity(
        tenant_id=TENANT, database_id="hermetic-database", command_id=token
    )
    retained = RetainedIngressSource(
        source=record.command.retention.proof,
        reader_id=R17_RETAINED_READER_ID,
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
            expected_reader_id=R17_RETAINED_READER_ID,
        ),
    )


@pytest.mark.asyncio
async def test_installed_v2_selector_replays_old_native_cut_after_seal(tmp_path) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=resources) as runtime:
            request = await _admit(runtime)
            initial = await runtime.drive_input(request)
            runtime._execution_model._responses = (
                DeliveryCompletion(
                    tenant=TENANT,
                    run_id=initial.stable_run_lineage_id,
                    turn_id=initial.stable_run_lineage_id + "/turn/1",
                    deliveries=(ProposedDelivery(payload=(Commentary(text="historical source"),)),),
                ).canonical_bytes(),
            )
            started = await runtime.begin_execution(
                "hermetic-ingress", initial.stable_run_lineage_id, initial.selected_run_head.head
            )
            captured = await runtime.capture_execution(
                "hermetic-ingress", initial.stable_run_lineage_id, started.head
            )
            sealed = await runtime.seal_execution_complete(
                "hermetic-ingress", initial.stable_run_lineage_id, captured.head, profile="H1_V2"
            )
            selected_id, raw = next(
                (decision_id, raw)
                for decision_id, _, raw in runtime._loop_decisions().entries()
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

            native = H1V2RecoveryNativeSource(runtime).select(
                original_identity=request.identity,
                original_fingerprint=request.original_driver_command_fingerprint(),
                selected_seal=locator,
            )

            assert native.seal.decision_id == selected_id
            assert native.seal.decision_fingerprint == hashlib.sha256(raw).hexdigest()
            assert native.source.selected_response_seal == locator
            assert native.source.complete_ordered_run_lineage[-1].head == sealed.head
            assert native.initialization.decision_id == native.lineage[0].decision_id
            assert native.lineage[-1].decision_id == selected_id
            assert [member.decision_id for member in native.physical_members][-2:] == [
                selected_id,
                selected_id,
            ]
            assert len(
                {(member.decision_id, member.record_id) for member in native.physical_members}
            ) == len(native.physical_members)
            root_reader = H1PostSealRecoveryRootSource(runtime)
            historical_root = root_reader._root_from_native(
                native=native,
                original_identity=request.identity,
                original_fingerprint=request.original_driver_command_fingerprint(),
                selected_seal=locator,
            )
            immediate_root = root_reader.derive_immediate(
                request.identity, request.original_driver_command_fingerprint(), locator
            )
            assert historical_root == immediate_root
        # Reopen the installation to obtain a fresh short-lived ingress
        # authentication before committing unrelated later loop state.
        later_resources = HermeticDispatchResources(
            scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody-later"
        )
        async with open_installed_h1_runtime(launch, resources=later_resources) as runtime:
            other_request = await _admit_other(runtime)
            await runtime.drive_input(other_request)
            root_reader = H1PostSealRecoveryRootSource(runtime)
            assert (
                H1V2RecoveryNativeSource(runtime).select(
                    original_identity=request.identity,
                    original_fingerprint=request.original_driver_command_fingerprint(),
                    selected_seal=locator,
                )
                == native
            )
            assert (
                root_reader.derive_on_restart(
                    request.identity, request.original_driver_command_fingerprint(), locator
                )
                == immediate_root
            )


def test_selector_rejects_checkpoint_before_reading_runtime() -> None:
    with pytest.raises(ValueError, match="checkpoint"):
        H1V2RecoveryNativeSource._decode_v2_seal(
            b'{"h1_historical_checkpoint":{},"kind":"DECIDED"}'
        )
