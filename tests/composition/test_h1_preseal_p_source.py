"""RED contract for the missing installed P pre-seal capture port.

The capture must originate at P before a V2 response seal is selected.  This
test is deliberately strict-xfail until that owner provides a pre-seal API;
the existing completion-scope API is post-seal by construction and is not a
substitute.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from chiplog.capabilities.agent_loop.delivery_preparation import (
    Commentary,
    DeliveryCompletion,
    ProposedDelivery,
)
from chiplog.composition import h1_preseal
from chiplog.composition.common_cli_execution_runtime import (
    CommonCliExecutionRuntime,
    open_installed_h1_runtime,
)
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.h1_preseal_contracts import H1V2SealPreflight
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from tests.support.h1_cli_execution import _admit
from tests.support.h1_installed_launch import TENANT, installed_slot, prepare_installed_slot


def _resources(tmp_path: Path) -> HermeticDispatchResources:
    return HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )


async def _genuine_preflight(runtime: CommonCliExecutionRuntime) -> H1V2SealPreflight:
    request = await _admit(runtime)
    initial = cast(Any, await runtime.drive_input(request))
    runtime._execution_model._responses = (
        DeliveryCompletion(
            tenant=TENANT,
            run_id=initial.stable_run_lineage_id,
            turn_id=initial.stable_run_lineage_id + "/turn/1",
            deliveries=(ProposedDelivery(payload=(Commentary(text="preseal P source"),)),),
        ).canonical_bytes(),
    )
    started = await runtime.begin_execution(
        "hermetic-ingress", initial.stable_run_lineage_id, initial.selected_run_head.head
    )
    captured = await runtime.capture_execution(
        "hermetic-ingress", initial.stable_run_lineage_id, started.head
    )
    return h1_preseal.preflight_h1_v2_seal(runtime, captured, expected_head=captured.head)


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason=(
        "P owner has no pre-seal capture API: its only scope capture requires a "
        "post-seal H1CurrentNativeMemberSourceCut"
    ),
)
async def test_installed_p_owner_must_capture_residual_facts_from_a_native_preseal_cut(
    tmp_path: Path,
) -> None:
    """A genuine preflight, not a selected seal or caller DTO, is P's input."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            preflight = await _genuine_preflight(runtime)
            owner = runtime._h1_preissuance_registration_source_port

            assert preflight is not None
            assert owner is not None
            assert not hasattr(preflight, "selected_response_seal")
            # The required owner-private API must issue a non-transferable cut
            # containing the actual accepted scope/policy/recipient/custody,
            # source-signature and ISSUE/current wire commitments.  It must not
            # use the current post-seal completion-scope chain.
            capture_name = "_capture_preseal_p_residual"
            capture = getattr(owner, capture_name)
            cut = await capture(preflight)

            assert type(cut).__module__ == type(owner).__module__
            assert not hasattr(cut, "selected_response_seal")
