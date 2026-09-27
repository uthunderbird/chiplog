"""Locate an installed H1 V2 seal from its immutable original input."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from chiplog.capabilities.agent_loop.delivery_preparation import (
    Commentary,
    DeliveryCompletion,
    ProposedDelivery,
)
from chiplog.composition.common_cli_execution_runtime import (
    R17_RETAINED_READER_ID,
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
from chiplog.composition.h1_v2_recovery_native_source import (
    H1V2RecoveryNativeSource,
    H1V2RecoveryNativeSourceConflict,
    H1V2RecoveryNativeSourceError,
)
from chiplog.composition.r14_execution_complete_seal_records import RetainedExecutionCompleteSealV2
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform.ingress_transition_contracts import (
    IngressCommandIdentity,
    RetainedIngressSource,
)
from tests.support.h1_cli_execution import _admit, _client
from tests.support.h1_installed_launch import TENANT, installed_slot, prepare_installed_slot


async def _admit_other(runtime: CommonCliExecutionRuntime) -> DriveInputRequestV1:
    slot = "locator-later-unrelated"
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
    ingress = IngressCommandIdentity(
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
            original_ingress_identity=ingress,
            original_ingress_request_fingerprint=record.inbox.authentication_request_fingerprint,
        ),
        selected_source=CliRetainedSelectedSourceV1(
            original_ingress_identity=ingress,
            source_binding=record.command.token.source,
            selected_ingress_decision=admitted.selected_decision,
            source_head=retained.source,
            retained_source=retained,
            expected_reader_id=R17_RETAINED_READER_ID,
        ),
    )


@pytest.mark.asyncio
async def test_locator_reopens_installed_v2_seal_after_later_unrelated_input(
    tmp_path: Path,
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=resources) as runtime:
            request = await _admit(runtime)
            initial = await runtime.drive_input(request)
            assert isinstance(initial, SelectedExecutionReceiptV1)
            runtime._execution_model._responses = (
                DeliveryCompletion(
                    tenant=TENANT,
                    run_id=initial.stable_run_lineage_id,
                    turn_id=initial.stable_run_lineage_id + "/turn/1",
                    deliveries=(ProposedDelivery(payload=(Commentary(text="locator source"),)),),
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
            raw = next(
                raw
                for _, _, raw in runtime._loop_decisions().entries()
                if json.loads(raw).get("operation_id") == sealed.head
            )
            retained = RetainedExecutionCompleteSealV2.model_validate_json(
                json.loads(raw)["execution_complete_seal"]
            )
            expected_locator = retained.exchange.proposal.fan_out.response_seal
            expected_head = "record:" + expected_locator.digest()
            located = H1V2RecoveryNativeSource(runtime).locate_selected_seal(
                original_identity=request.identity,
                original_fingerprint=request.original_driver_command_fingerprint(),
            )
            assert located.subject_id == expected_locator.response_seal_id
            assert located.revision.head == expected_head
            assert located.revision.fingerprint == expected_locator.digest()
            with pytest.raises(H1V2RecoveryNativeSourceConflict, match="immutable fingerprint"):
                H1V2RecoveryNativeSource(runtime).locate_selected_seal(
                    original_identity=request.identity,
                    original_fingerprint="0" * 64,
                )

        later_resources = HermeticDispatchResources(
            scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody-later"
        )
        async with open_installed_h1_runtime(launch, resources=later_resources) as runtime:
            other = await _admit_other(runtime)
            await runtime.drive_input(other)
            assert H1V2RecoveryNativeSource(runtime).locate_selected_seal(
                original_identity=request.identity,
                original_fingerprint=request.original_driver_command_fingerprint(),
            ) == located


@pytest.mark.asyncio
async def test_locator_reports_retained_initialization_tampering_as_integrity_not_absence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=resources) as runtime:
            request = await _admit(runtime)
            await runtime.drive_input(request)
            entries = list(runtime._loop_decisions().entries())
            index = next(
                index
                for index, (_, _, raw) in enumerate(entries)
                if "inbox_initialization" in json.loads(raw)
            )
            decision_id, predecessor, raw = entries[index]
            entry = json.loads(raw)
            retained = json.loads(entry["inbox_initialization"])
            retained["predecessor_commitment"] = hashlib.sha256(b"tampered").hexdigest()
            entry["inbox_initialization"] = json.dumps(
                retained, separators=(",", ":"), ensure_ascii=False
            )
            entries[index] = (
                decision_id,
                predecessor,
                json.dumps(entry, sort_keys=True, separators=(",", ":")).encode(),
            )

            class _TamperedJournal:
                def entries(self) -> tuple[tuple[str, str | None, bytes], ...]:
                    return tuple(entries)

            monkeypatch.setattr(runtime, "_loop_decisions", lambda: _TamperedJournal())
            with pytest.raises(H1V2RecoveryNativeSourceError, match="initialization"):
                H1V2RecoveryNativeSource(runtime).locate_selected_seal(
                    original_identity=request.identity,
                    original_fingerprint=request.original_driver_command_fingerprint(),
                )
