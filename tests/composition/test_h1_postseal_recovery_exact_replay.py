"""RED coverage for recovering an H1 V2 seal that predates its ROOT."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.delivery_preparation import (
    Commentary,
    DeliveryCompletion,
    ProposedDelivery,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.composition.common_cli_execution_runtime import open_installed_h1_runtime
from chiplog.composition.common_execution_driver_contracts import ExecutionDriverRejectedV1
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.h1_postseal_recovery import (
    H1PostSealRecoveryState,
    H1PostSealRecoveryTransition,
)
from chiplog.composition.h1_postseal_recovery_source import H1PostSealRecoveryRootSource
from chiplog.composition.r14_execution_complete_seal_records import RetainedExecutionCompleteSealV2
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from tests.support.h1_cli_execution import _admit, advance
from tests.support.h1_installed_launch import TENANT, installed_slot, prepare_installed_slot


def _resources(tmp_path: Path) -> HermeticDispatchResources:
    return HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )


def _selected_seal_locator(runtime, seal_head: str) -> CallSubjectHead:
    raw = next(
        raw
        for _, _, raw in runtime._loop_decisions().entries()
        if json.loads(raw).get("operation_id") == seal_head
    )
    retained = RetainedExecutionCompleteSealV2.model_validate_json(
        json.loads(raw)["execution_complete_seal"]
    )
    response_seal = retained.exchange.proposal.fan_out.response_seal
    return CallSubjectHead(
        subject_id=response_seal.response_seal_id,
        revision=Present(
            head="record:" + response_seal.digest(), fingerprint=response_seal.digest()
        ),
    )


@pytest.mark.asyncio
async def test_installed_exact_replay_recovers_root_from_preexisting_h1_v2_seal(
    tmp_path: Path,
) -> None:
    """A closed installed runtime resumes a real sealed run whose ROOT is absent."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    resources = _resources(tmp_path)

    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=resources) as runtime:
            request = await _admit(runtime)
            initial = await runtime.drive_input(request)
            runtime._execution_model._responses = (
                DeliveryCompletion(
                    tenant=TENANT,
                    run_id=initial.stable_run_lineage_id,
                    turn_id=initial.stable_run_lineage_id + "/turn/1",
                    deliveries=(ProposedDelivery(payload=(Commentary(text="post-seal restart"),)),),
                ).canonical_bytes(),
            )

            # Deliberately perform only the native first path.  In particular, this
            # bypasses the future recovery coordinator so no ROOT can preexist.
            started = await runtime.begin_execution(
                "hermetic-ingress", initial.stable_run_lineage_id, initial.selected_run_head.head
            )
            captured = await runtime.capture_execution(
                "hermetic-ingress", initial.stable_run_lineage_id, started.head
            )
            sealed = await runtime.seal_execution_complete(
                "hermetic-ingress", initial.stable_run_lineage_id, captured.head, profile="H1_V2"
            )
            locator = _selected_seal_locator(runtime, sealed.head)
            expected_root = H1PostSealRecoveryRootSource(runtime).derive_on_restart(
                request.identity, request.original_driver_command_fingerprint(), locator
            )
            assert runtime._h1_postseal_recovery_journal.scan().states_by_root == ()
            assert len(runtime._execution_model.requests) == 1
            before_decisions = runtime._loop_decisions().entries()

        async with open_installed_h1_runtime(launch, resources=resources) as reopened:
            replay = await reopened.advance_execution(advance(initial, request))
            roots = [
                state.root
                for _, state in reopened._h1_postseal_recovery_journal.scan().states_by_root
            ]

            assert roots == [expected_root]
            assert replay.phase == "RUNNING"
            assert replay.disposition == "EXACT_REPLAY"
            assert reopened._execution_model.requests == []
            assert reopened._loop_decisions().entries() == before_decisions


@pytest.mark.asyncio
async def test_installed_exact_replay_holds_on_a_forged_selected_root(tmp_path: Path) -> None:
    """A canonical ROOT is evidence only when every derived field agrees."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    resources = _resources(tmp_path)

    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=resources) as runtime:
            request = await _admit(runtime)
            initial = await runtime.drive_input(request)
            runtime._execution_model._responses = (
                DeliveryCompletion(
                    tenant=TENANT,
                    run_id=initial.stable_run_lineage_id,
                    turn_id=initial.stable_run_lineage_id + "/turn/1",
                    deliveries=(ProposedDelivery(payload=(Commentary(text="forged root"),)),),
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
            locator = _selected_seal_locator(runtime, sealed.head)
            derived = H1PostSealRecoveryRootSource(runtime).derive_on_restart(
                request.identity, request.original_driver_command_fingerprint(), locator
            )
            forged = derived.model_copy(
                publication_command_id="forged-root",
                publication_command_fingerprint="f" * 64,
            )
            scan = runtime._h1_postseal_recovery_journal.scan()
            runtime._h1_postseal_recovery_journal.append_transition(
                H1PostSealRecoveryTransition.begin(H1PostSealRecoveryState.empty(forged)),
                expected_global_tip=scan.tip,
            )

            replay = await runtime.advance_execution(advance(initial, request))

            assert isinstance(replay, ExecutionDriverRejectedV1)
            assert replay.code == "HOLD"
