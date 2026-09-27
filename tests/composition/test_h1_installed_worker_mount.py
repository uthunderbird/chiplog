"""The installed H1 runtime mounts an identity-bound worker source owner."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.delivery_preparation import (
    Commentary,
    DeliveryCompletion,
    ProposedDelivery,
)
from chiplog.capabilities.agent_loop.recovery_contracts import NotApplicable, Present
from chiplog.composition.common_cli_execution_runtime import open_installed_h1_runtime
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.h1_native_member_sources import H1CurrentNativeMemberSourceCut
from chiplog.composition.h1_worker_evidence import (
    H1WorkerSourceCapability,
    _H1InstalledWorkerEvidenceOwner,
)
from chiplog.composition.r14_execution_complete_seal_records import RetainedExecutionCompleteSealV3
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from tests.support.h1_cli_execution import _admit
from tests.support.h1_installed_launch import TENANT, installed_slot, prepare_installed_slot


async def _current_native_cut(runtime: Any) -> H1CurrentNativeMemberSourceCut:
    request = await _admit(runtime)
    initial = await runtime.drive_input(request)
    completion = DeliveryCompletion(
        tenant=TENANT,
        run_id=initial.stable_run_lineage_id,
        turn_id=initial.stable_run_lineage_id + "/turn/1",
        deliveries=(ProposedDelivery(payload=(Commentary(text="mounted worker"),)),),
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
    first_capture = runtime._h1_first_path_sources.capture_current(
        original_identity=request.identity,
        original_fingerprint=request.original_driver_command_fingerprint(),
        selected_seal=CallSubjectHead(
            subject_id=response_seal.response_seal_id,
            revision=Present(
                head="record:" + response_seal.digest(), fingerprint=response_seal.digest()
            ),
        ),
    )
    return cast(
        H1CurrentNativeMemberSourceCut,
        runtime._h1_native_member_sources.capture_current(first_capture),
    )


@pytest.mark.asyncio
async def test_installed_h1_worker_replays_only_its_current_native_cut(tmp_path: Path) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=resources) as runtime:
            native_cut = await _current_native_cut(runtime)
            owner = cast(
                _H1InstalledWorkerEvidenceOwner, runtime._h1_installed_worker_evidence_owner
            )
            capability = owner._capture_for_pre_request(native_cut)
            verified = owner._replay_for_pre_request(capability, native_cut)

            assert verified.run == native_cut._native.source.complete_ordered_run_lineage[-1]
            assert verified.fence.run_id == verified.run.run_id
            assert verified.fence.run_head == verified.run.head
            assert verified.fence.worker_session_id == verified.worker_session_id
            assert all(
                isinstance(value, NotApplicable)
                for value in (
                    verified.fence.lineage,
                    verified.fence.physical_root,
                    verified.fence.lease,
                    verified.fence.clock_proof,
                )
            )
            with pytest.raises(ValueError, match="capability"):
                owner._replay_for_pre_request(cast(H1WorkerSourceCapability, object()), native_cut)
            with pytest.raises(ValueError, match="differs"):
                owner._replay_for_pre_request(
                    capability, cast(H1CurrentNativeMemberSourceCut, object())
                )
            object.__setattr__(native_cut, "_occurrences", ())
            with pytest.raises(ValueError, match=r"stale|current"):
                owner._replay_for_pre_request(capability, native_cut)

        with pytest.raises(ValueError, match=r"closed|capability"):
            owner._replay_for_pre_request(capability, native_cut)
        assert not hasattr(runtime, "_h1_installed_worker_evidence_owner")
        assert not hasattr(runtime, "_h1_native_member_sources")
        assert not hasattr(runtime, "_h1_first_path_sources")
