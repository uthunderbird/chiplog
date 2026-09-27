"""RED witnesses for final H1 recovery issuance and physical publication.

The installed route is intentional: no test fabricates a completion marker or
an issuance record.  The final authority may see a marker only after recovery
has re-run the four enrolled owner exchanges and captured its historical P
fence.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from chiplog.capabilities.agent_loop.delivery_preparation import (
    Commentary,
    DeliveryCompletion,
    ProposedDelivery,
)
from chiplog.composition.common_cli_execution_runtime import open_installed_h1_runtime
from chiplog.composition.common_execution_driver_contracts import SelectedExecutionReceiptV1
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform._owner_publication_contracts import CompleteDeliveryBatchV2
from tests.support.h1_cli_execution import _admit, advance
from tests.support.h1_installed_launch import TENANT, installed_slot, prepare_installed_slot


class H1FinalIssuanceUnavailable(RuntimeError):
    """Only the unimplemented B-to-authority seam is an expected RED."""


_RED = pytest.mark.xfail(
    strict=True,
    raises=H1FinalIssuanceUnavailable,
    reason="H1 recovery B final issuance and authority admission are not implemented",
)


def _resources(tmp_path: Path) -> HermeticDispatchResources:
    return HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )


async def _advance_to_durable_recovery(runtime: Any) -> tuple[Any, SelectedExecutionReceiptV1]:
    """Drive a genuine selected input to the durable, non-final recovery cut."""
    request = await _admit(runtime)
    initial = await runtime.drive_input(request)
    assert isinstance(initial, SelectedExecutionReceiptV1)
    runtime._execution_model._responses = DeliveryCompletion(
        tenant=TENANT,
        run_id=initial.stable_run_lineage_id,
        turn_id=initial.stable_run_lineage_id + "/turn/1",
        deliveries=(ProposedDelivery(payload=(Commentary(text="final issuance"),)),),
    ).canonical_bytes()
    recovered = await runtime.advance_execution(advance(initial, request))
    if not isinstance(recovered, SelectedExecutionReceiptV1) or recovered.phase != "RUNNING":
        raise H1FinalIssuanceUnavailable(
            "recovery did not reach the durable four-stage cut: expected selected RUNNING receipt"
        )
    state = runtime._h1_postseal_recovery_journal.scan().states_by_root[0][1]
    if len(state.inputs) != 4 or len(state.results) != 4:
        raise H1FinalIssuanceUnavailable("recovery did not durably retain all four owner exchanges")
    return request, recovered


@pytest.mark.asyncio
@_RED
async def test_installed_recovery_selects_one_physical_v2_terminal_batch(tmp_path: Path) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            _request, recovery = await _advance_to_durable_recovery(runtime)
            assert recovery.phase == "RUNNING"
            raise H1FinalIssuanceUnavailable(
                "installed finalization operation is not frozen; durable recovery cannot mint"
            )


@pytest.mark.asyncio
@_RED
async def test_final_authority_denies_caller_copied_and_foreign_markers(tmp_path: Path) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            await _advance_to_durable_recovery(runtime)
            authority = runtime._h1_live_publication_authority
            admit = getattr(authority, "_admit_issuance", None)
            if not callable(admit):
                raise H1FinalIssuanceUnavailable("authority._admit_issuance is absent")
            with pytest.raises((TypeError, ValueError)):
                admit(object())

            raise H1FinalIssuanceUnavailable(
                "finalization operation has not supplied an enrollment-issued marker"
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ("missing", "reordered", "stale_final_p"))
@_RED
async def test_final_issuance_rejects_missing_reordered_or_stale_recovery_exchanges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    """The future B issuer must reject mutations before authority admission."""
    from chiplog.composition.h1_completion_preparation_session import H1CompletionPreparationSession

    original = getattr(H1CompletionPreparationSession, "_issue_completion", None)
    if not callable(original):
        raise H1FinalIssuanceUnavailable("recovery B _issue_completion is absent")

    original_fence = H1CompletionPreparationSession._replay_final_fence_inputs

    def stale_final_p(self: Any, port: object) -> object:
        inputs = original_fence(self, port)
        # P owns this fence.  Changing the capability only after its retained
        # exchanges exist models a stale final-P observation, rather than a
        # caller-provided recovery request.
        return replace(inputs, scope_cap=object())

    async def mutated(self: Any) -> object:
        if mutation == "missing":
            self._effects_exchange = None
        elif mutation == "reordered":
            self._conversation_exchange, self._effects_exchange = (
                self._effects_exchange,
                self._conversation_exchange,
            )
        return await original(self)

    if mutation == "stale_final_p":
        monkeypatch.setattr(
            H1CompletionPreparationSession, "_replay_final_fence_inputs", stale_final_p
        )
    monkeypatch.setattr(H1CompletionPreparationSession, "_issue_completion", mutated)
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            with pytest.raises((TypeError, ValueError, RuntimeError)):
                await _advance_to_durable_recovery(runtime)
            assert not any(
                isinstance(item.prepared.request, CompleteDeliveryBatchV2)
                for item in runtime._owner_decisions().snapshot().decisions
            )


@pytest.mark.asyncio
@_RED
async def test_final_marker_is_consumed_once_under_concurrent_admission(tmp_path: Path) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            await _advance_to_durable_recovery(runtime)
            authority = runtime._h1_live_publication_authority
            admit = getattr(authority, "_admit_issuance", None)
            if not callable(admit):
                raise H1FinalIssuanceUnavailable("authority._admit_issuance is absent")
            raise H1FinalIssuanceUnavailable(
                "finalization operation has not supplied a marker for concurrent admission"
            )


@pytest.mark.asyncio
@_RED
async def test_fully_durable_recovery_replays_four_frames_before_final_mint(tmp_path: Path) -> None:
    """Durable stage bytes are equality witnesses, never a mint credential."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            _request, first = await _advance_to_durable_recovery(runtime)
            assert first.phase == "RUNNING"
            state = runtime._h1_postseal_recovery_journal.scan().states_by_root[0][1]
            assert tuple(stage for stage, _raw, _command_id in state.inputs) == (
                "COMPLETION",
                "CONVERSATION",
                "EFFECTS",
                "TERMINAL_WORK",
            )
            assert tuple(stage for stage, _raw in state.results) == (
                "COMPLETION",
                "CONVERSATION",
                "EFFECTS",
                "TERMINAL_WORK",
            )
            # Existing recovery remains an exact no-IPC RUNNING replay.  The
            # later finalization operation, not this branch, must do the fresh
            # four-frame replay before it can mint.
            replay = await runtime.advance_execution(advance(first, _request))
            assert isinstance(replay, SelectedExecutionReceiptV1)
            assert replay.disposition == "EXACT_REPLAY"
            assert replay.phase == "RUNNING"
            raise H1FinalIssuanceUnavailable("installed finalization operation is not frozen")
