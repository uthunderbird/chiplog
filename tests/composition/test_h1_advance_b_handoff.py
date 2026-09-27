"""Installed advance owns the first bounded hand-off into enrolled H1 B."""

from __future__ import annotations

from pathlib import Path

import pytest

from chiplog.capabilities.agent_loop.delivery_preparation import (
    Commentary,
    DeliveryCompletion,
    ProposedDelivery,
)
from chiplog.composition.common_cli_execution_runtime import open_installed_h1_runtime
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from tests.support.h1_cli_execution import _admit, advance
from tests.support.h1_installed_launch import TENANT, installed_slot, prepare_installed_slot


def _resources(tmp_path: Path) -> HermeticDispatchResources:
    return HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )


@pytest.mark.asyncio
async def test_installed_advance_hands_its_h1_v2_seal_to_one_enrolled_b_session(
    tmp_path: Path,
) -> None:
    """The mounted owner, not a caller, creates B and all four real exchanges."""
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
                    deliveries=(ProposedDelivery(payload=(Commentary(text="B handoff"),)),),
                ).canonical_bytes(),
            )

            result = await runtime.advance_execution(advance(initial, request))

            assert result.phase == "RUNNING"
            enrollment = runtime._h1_live_completion_enrollment
            assert len(enrollment._records) == 1
            record = next(iter(enrollment._records.values()))
            session = record.session
            assert record.cut is session._cut
            assert session._cut is not None
            assert session._cut.original_identity is request.identity
            assert session._cut.original_fingerprint == request.original_driver_command_fingerprint()
            assert session._completion_exchange is not None
            assert session._conversation_exchange is not None
            assert session._effects_exchange is not None
            assert session._terminal_work_exchange is not None

            # An exact replay is receipt-only: it neither opens a second B nor
            # reruns any of the owner calls retained by the first invocation.
            replay = await runtime.advance_execution(advance(initial, request))
            assert replay.disposition == "EXACT_REPLAY"
            assert tuple(enrollment._records.values()) == (record,)
            assert session._completion_exchange is not None
            assert session._conversation_exchange is not None
            assert session._effects_exchange is not None
            assert session._terminal_work_exchange is not None
