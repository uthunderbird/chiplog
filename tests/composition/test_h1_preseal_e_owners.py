"""Mounted RED contract for the append-free preseal E owners.

The positive stays RED until the runtime mounts one exact preseal native
source and P exposes an owner-issued preseal scope capability.  It exercises
the real V3 Prepare/captured Run path only; no test supplies E or P DTOs.
"""

from __future__ import annotations

import hashlib
import importlib
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
    runtime._execution_model._responses = DeliveryCompletion(
        tenant=TENANT,
        run_id=initial.stable_run_lineage_id,
        turn_id=initial.stable_run_lineage_id + "/turn/1",
        deliveries=(ProposedDelivery(payload=(Commentary(text="preseal E owners"),)),),
    ).canonical_bytes()
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
        "runtime has not mounted one H1PresealNativeSource and P has not issued the exact "
        "preseal scope capability required by append-free E owner projections"
    ),
)
async def test_installed_preseal_e_owners_project_ordered_residuals_without_e_append(
    tmp_path: Path,
) -> None:
    """The source owner, never caller DTOs, drives all preseal E residuals."""
    source_module = importlib.import_module("chiplog.composition.h1_preseal_e_source")
    source_type = source_module.H1PresealESource
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            preflight = await _genuine_preflight(runtime)
            journal = runtime._h1_delivery_evidence_journal
            assert journal is not None
            before = journal._entries()

            residual = source_type(runtime).capture_and_recheck(preflight)

            members = residual.e_members
            native_members = preflight.captured_run.turns[0].attempts[0].manifest.members
            assert [member["member_index"] for member in members] == list(
                range(len(native_members))
            )
            assert [member["member_digest"] for member in members] == [
                hashlib.sha256(member.canonical_bytes()).hexdigest() for member in native_members
            ]
            assert all(
                set(member)
                == {"member_index", "member_digest", "provenance", "disclosure", "narrowing"}
                for member in members
            )
            assert residual.e_worker["run_head"] == preflight.captured_run.head
            assert journal._entries() == before
