"""Installed P-source witnesses for H1 preseal capture and replay."""

from __future__ import annotations

import copy
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
from chiplog.composition.h1_preissuance_registration import H1PreissuanceSourceViolation
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
            deliveries=(ProposedDelivery(payload=(Commentary(text="preseal P owner"),)),),
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
async def test_installed_p_owner_captures_and_replays_preseal_scope_without_new_issue(
    tmp_path: Path,
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            preflight = await _genuine_preflight(runtime)
            port = runtime._h1_preissuance_registration_source_port
            native_owner = getattr(runtime, "_h1_preseal_native_source", None)
            assert port is not None
            assert native_owner is not None
            before = runtime._trust._journal.entries()

            native = native_owner.capture(preflight)
            capability = await port._capture_preseal_p_residual(native)
            issued = port._preseal_scopes[id(capability)][1]
            with runtime._authority_gate().hold():
                projection = port._replay_preseal_scope(capability, issued.native)
                issue_wire, current_wire = port._replay_preseal_scope_wires(
                    capability, issued.native
                )

            entry = launch.custody.select(
                projection.scope.tenant_id, projection.scope.principal_id, "hermetic-local"
            )
            assert issued.preflight is preflight
            assert issued.native._preflight is preflight
            assert projection.scope.worker_session_id == preflight.worker_session
            assert projection.recipient.endpoint == projection.scope.recipient.endpoint
            assert (
                projection.policy_bytes == projection.scope.disclosure_policy.canonical_source_bytes
            )
            assert projection.custody_entry_generation == entry.generation
            assert issue_wire is issued.cut.scope_issue_wire
            assert current_wire is issued.current_wire
            assert runtime._trust._journal.entries() == before


@pytest.mark.asyncio
async def test_preseal_p_capability_rejects_copy_and_foreign_native_cut(tmp_path: Path) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            preflight = await _genuine_preflight(runtime)
            port = runtime._h1_preissuance_registration_source_port
            assert port is not None
            native_owner = getattr(runtime, "_h1_preseal_native_source", None)
            assert native_owner is not None
            native = native_owner.capture(preflight)
            capability = await port._capture_preseal_p_residual(native)

            with pytest.raises(TypeError, match="cannot be copied"):
                copy.copy(capability)
            with runtime._authority_gate().hold():
                with pytest.raises(H1PreissuanceSourceViolation, match="identity"):
                    port._replay_preseal_scope(capability, object())
                assert (
                    port._replay_preseal_scope(capability, native).scope.tenant_id
                    == preflight.captured_run.tenant
                )
