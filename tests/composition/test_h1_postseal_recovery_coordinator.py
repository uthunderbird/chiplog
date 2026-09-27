"""Mounted RED witness for the H1 V2 seal-to-recovery handoff."""

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
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.h1_postseal_recovery import H1PostSealRecoveryJournal
from chiplog.composition.h1_postseal_recovery_source import H1PostSealRecoveryRootSource
from chiplog.composition.r14_execution_complete_seal_records import RetainedExecutionCompleteSealV2
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from tests.support.h1_cli_execution import _admit, advance
from tests.support.h1_installed_launch import TENANT, installed_slot, prepare_installed_slot


def _resources(tmp_path: Path) -> HermeticDispatchResources:
    return HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )


def _selected_seal_locator(runtime, run_head: str) -> CallSubjectHead:
    raw = next(
        raw
        for _, _, raw in runtime._loop_decisions().entries()
        if json.loads(raw).get("operation_id") == run_head
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
async def test_installed_advance_persists_its_independently_derived_root(
    tmp_path: Path,
) -> None:
    """A durable V2 seal independently creates its recovery ROOT."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            request = await _admit(runtime)
            initial = await runtime.drive_input(request)
            runtime._execution_model._responses = (
                DeliveryCompletion(
                    tenant=TENANT,
                    run_id=initial.stable_run_lineage_id,
                    turn_id=initial.stable_run_lineage_id + "/turn/1",
                    deliveries=(ProposedDelivery(payload=(Commentary(text="recover root"),)),),
                ).canonical_bytes(),
            )

            result = await runtime.advance_execution(advance(initial, request))

            locator = _selected_seal_locator(runtime, result.selected_run_head.head)
            expected_root = H1PostSealRecoveryRootSource(runtime).derive_on_restart(
                request.identity, request.original_driver_command_fingerprint(), locator
            )
            selected_roots = [
                state.root
                for _, state in runtime._h1_postseal_recovery_journal.scan().states_by_root
                if (
                    state.root.selected_seal_subject_id,
                    state.root.selected_seal_head,
                    state.root.selected_seal_fingerprint,
                )
                == (
                    locator.subject_id,
                    locator.revision.head,
                    locator.revision.fingerprint,
                )
            ]

            assert selected_roots == [expected_root]
            assert result.phase == "RUNNING"
            assert result.disposition == "COMMITTED"


@pytest.mark.asyncio
async def test_root_append_uncertainty_reopens_and_reconciles_without_a_second_append(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A possibly durable ROOT is reconciled from a fresh enrolled wrapper."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    original_append = H1PostSealRecoveryJournal.append_transition
    append_calls = 0

    def append_then_lose_readback(self, record, *, expected_global_tip):
        nonlocal append_calls
        append_calls += 1
        original_append(self, record, expected_global_tip=expected_global_tip)
        raise OSError("simulated post-append readback loss")

    monkeypatch.setattr(H1PostSealRecoveryJournal, "append_transition", append_then_lose_readback)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            request = await _admit(runtime)
            initial = await runtime.drive_input(request)
            runtime._execution_model._responses = (
                DeliveryCompletion(
                    tenant=TENANT,
                    run_id=initial.stable_run_lineage_id,
                    turn_id=initial.stable_run_lineage_id + "/turn/1",
                    deliveries=(ProposedDelivery(payload=(Commentary(text="uncertain root"),)),),
                ).canonical_bytes(),
            )

            result = await runtime.advance_execution(advance(initial, request))

            assert result.phase == "RUNNING"
            assert append_calls == 1
            assert len(runtime._h1_postseal_recovery_journal.scan().states_by_root) == 1
