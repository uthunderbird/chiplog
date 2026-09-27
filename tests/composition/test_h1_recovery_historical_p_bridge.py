"""RED acceptance witness for the historical P/E completion projection.

The V2 seal survives a process restart, whereas the ordinary P delivery
receipt is deliberately process-local.  This test therefore specifies the
missing owner bridge at the only useful boundary: a reopened installed source
must construct the semantic COMPLETION request itself, with no caller-shaped
delivery or fence input.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from chiplog.composition.common_cli_execution_runtime import open_installed_h1_runtime
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.h1_recovery_stage_source import (
    H1RecoveryStageSource,
    H1RecoveryStageSourceError,
)
from tests.composition.test_h1_recovery_stage_source import (
    _admit_other,
    _resources,
    _v2_seal_with_historical_v3_prepare,
)
from tests.support.h1_installed_launch import installed_slot, prepare_installed_slot


@pytest.mark.asyncio
async def test_installed_recovery_reopens_authenticated_historical_p_delivery_and_fence(
    tmp_path: Path,
) -> None:
    """A sealed V3 Prepare/V2 seal remains reconstructible after restart only by P/E.

    The request-producing seam receives solely the stage source's issuer-held
    token.  A copied token and a token issued by another runtime are not P/E
    authority; a later unrelated publication must not alter the selected
    request's canonical bytes.
    """
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)

    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            original, locator, _selected_prepare = await _v2_seal_with_historical_v3_prepare(
                runtime
            )

        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as reopened:
            source = H1RecoveryStageSource(reopened)
            issued = source.issue_completion_native(
                original_identity=original.identity,
                original_fingerprint=original.original_driver_command_fingerprint(),
                selected_seal=locator,
            )

            request = source.reconstruct_completion_input(issued)
            assert request.run == source.replay_completion_native(issued).run
            assert request.delivery.captured_response == request.exact_captured_response
            assert request.fence.run_id == request.run.run_id
            assert request.fence.run_head == request.run.head
            assert request.fence.worker_session_id == request.run.worker_session

            # A visible-field clone cannot substitute for the source-owned
            # token that P uses to re-open its own historical evidence.
            with pytest.raises(H1RecoveryStageSourceError, match="issuer-owned"):
                source.reconstruct_completion_input(dataclasses.replace(issued))

            before = request.canonical_bytes()

        async with open_installed_h1_runtime(
            launch, resources=_resources(tmp_path, "-later")
        ) as later:
            await later.drive_input(await _admit_other(later))
            later_source = H1RecoveryStageSource(later)

            # The receipt table is runtime-local.  A token from the previous
            # runtime cannot be used to recover an old P/E projection.
            with pytest.raises(H1RecoveryStageSourceError, match="issuer-owned"):
                later_source.reconstruct_completion_input(issued)

            replayed = later_source.issue_completion_native(
                original_identity=original.identity,
                original_fingerprint=original.original_driver_command_fingerprint(),
                selected_seal=locator,
            )
            assert later_source.reconstruct_completion_input(replayed).canonical_bytes() == before
