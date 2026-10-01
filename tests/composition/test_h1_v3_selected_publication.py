"""Mounted selected SCOPED_V3 publication through the installed writer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from chiplog.composition.common_cli_execution_runtime import open_installed_h1_scoped_runtime
from chiplog.composition.h1_completion_issuance import H1CompletionIssuanceV3
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.h1_producer_history import interpret_h1_producer_effects_history
from chiplog.composition.r16_effects import _read_materialized_effects_with_history
from chiplog.platform._owner_publication_contracts import CompleteDeliveryBatchV2
from tests.support.h1_installed_launch import installed_slot, prepare_installed_slot
from tests.support.h1_scoped_first_b import scoped_resources, write_scoped_pins
from tests.support.h1_v3_recovery import (
    _issue_reconstructed_grant,
    _recover_b_prefix,
    _selected_scoped_root,
)


@pytest.mark.asyncio
async def test_selected_v3_writer_publishes_once_and_reopens_through_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    write_scoped_pins(slot.database_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_scoped_runtime(
            launch, resources=scoped_resources(tmp_path)
        ) as runtime:
            request, locator, state = await _selected_scoped_root(runtime)
            state = await _recover_b_prefix(runtime, request, locator, state)
            await _issue_reconstructed_grant(runtime, request, locator)
            coordinator = runtime._h1_postseal_recovery_coordinator
            assert coordinator is not None
            completed = await coordinator.resume_selected(
                request.identity, request.original_driver_command_fingerprint(), locator
            )
            assert len(completed.inputs) == len(completed.results) == 4

            calls: list[str] = []
            broker = runtime._supervisor.runtime()
            original = broker._call_with_admission_guard

            async def counted(sent: Any, **kwargs: Any) -> Any:
                calls.append(sent.operation_id)
                return await original(sent, **kwargs)

            monkeypatch.setattr(broker, "_call_with_admission_guard", counted)
            receipt = await runtime.finalize_execution(
                request.identity, request.original_driver_command_fingerprint()
            )
            assert receipt.disposition == "COMMITTED"
            assert calls.count("agent_loop.prepare_first_path_completion") == 1
            assert calls.count("projections.prepare_conversation_completion") == 1
            assert calls.count("effects.prepare_h1_scoped_delivery") == 1
            assert calls.count("agent_loop.prepare_terminal_work") == 1

            selected = tuple(
                decision
                for decision in runtime._owner_decisions().snapshot().decisions
                if type(decision.prepared.request) is CompleteDeliveryBatchV2
            )
            assert len(selected) == 1
            batch = selected[0].prepared.request
            enrollment = runtime._h1_live_completion_enrollment
            assert enrollment is not None
            marker = tuple(enrollment._issuances.values())
            assert len(marker) == 1
            assert type(marker[0].issuance) is H1CompletionIssuanceV3
            assert marker[0].batch == batch and marker[0].state == "CONSUMED"
            physical_state = runtime._selected_physical_state(runtime._owner_command(selected[0]))
            assert physical_state[0] == "COMPLETE"

            replay = await runtime.finalize_execution(
                request.identity, request.original_driver_command_fingerprint()
            )
            assert replay.disposition == "EXACT_REPLAY"
            assert calls.count("agent_loop.prepare_first_path_completion") == 1
            assert calls.count("effects.prepare_h1_scoped_delivery") == 1

    with _open_installed_h1_launch(slot) as reopened_launch:
        async with open_installed_h1_scoped_runtime(
            reopened_launch, resources=scoped_resources(tmp_path)
        ) as reopened:
            replay = await reopened.finalize_execution(
                request.identity, request.original_driver_command_fingerprint()
            )
            assert replay.disposition == "EXACT_REPLAY"
            cut, journal = _read_materialized_effects_with_history(
                reopened, reopened._owner_decisions()
            )
            history = interpret_h1_producer_effects_history(cut, journal, runtime=reopened)
            assert len(history.ordered_members) == 1
