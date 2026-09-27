"""Installed E worker evidence is mounted and revoked with its worker owner."""

from __future__ import annotations

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
from chiplog.composition.common_cli_execution_runtime import open_installed_h1_runtime
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.r14_execution_complete_seal_records import RetainedExecutionCompleteSealV3
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from tests.support.h1_cli_execution import _admit
from tests.support.h1_installed_launch import TENANT, installed_slot, prepare_installed_slot


async def _selected_native_cut(runtime: Any) -> object:
    request = await _admit(runtime)
    initial = await runtime.drive_input(request)
    completion = DeliveryCompletion(
        tenant=TENANT,
        run_id=initial.stable_run_lineage_id,
        turn_id=initial.stable_run_lineage_id + "/turn/1",
        deliveries=(ProposedDelivery(payload=(Commentary(text="mounted E worker"),)),),
    )
    runtime._execution_model._responses = (completion.canonical_bytes(),)
    started = await runtime.begin_execution(
        "hermetic-ingress", initial.stable_run_lineage_id, initial.selected_run_head.head
    )
    captured = await runtime.capture_execution(
        "hermetic-ingress", initial.stable_run_lineage_id, started.head
    )
    sealed = await runtime.seal_execution_complete(
        "hermetic-ingress", initial.stable_run_lineage_id, captured.head, profile="H1_V3"
    )
    decision = next(
        json.loads(raw)
        for _, _, raw in runtime._loop_decisions().entries()
        if json.loads(raw).get("operation_id") == sealed.head
    )
    retained = RetainedExecutionCompleteSealV3.model_validate_json(
        decision["execution_complete_seal"]
    )
    response_seal = retained.exchange.proposal.fan_out.response_seal
    first_path = runtime._h1_first_path_sources.capture_current(
        original_identity=request.identity,
        original_fingerprint=request.original_driver_command_fingerprint(),
        selected_seal=CallSubjectHead(
            subject_id=response_seal.response_seal_id,
            revision=Present(
                head="record:" + response_seal.digest(), fingerprint=response_seal.digest()
            ),
        ),
    )
    return runtime._h1_native_member_sources.capture_current(first_path)


@pytest.mark.asyncio
async def test_installed_e_worker_mount_reads_exact_evidence_then_revokes_before_worker_exit(
    tmp_path: Path,
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=resources) as runtime:
            native_cap = await _selected_native_cut(runtime)
            worker_owner = runtime._h1_installed_worker_evidence_owner
            worker_cap = worker_owner._capture_for_pre_request(native_cap)
            issuer = runtime._h1_pre_request_worker_evidence
            journal = runtime._h1_delivery_evidence_journal

            receipt = issuer._issue_worker(native_cap, worker_cap)
            locator, record, _ = issuer._replay_worker(receipt)
            readback = journal.read_exact(locator)

            assert readback.journal_instance_id == journal._mount.journal_instance_id
            assert readback.raw_payload == record.canonical_bytes()
            assert readback.canonical_bytes() == record.canonical_bytes()

        assert not hasattr(runtime, "_h1_pre_request_worker_evidence")
        with pytest.raises(ValueError, match=r"closed|issuer-owned"):
            issuer._replay_worker(receipt)
