"""Installed runtime lifecycle for the one H1 preseal-native source owner."""

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
    open_common_cli_execution_runtime,
    open_installed_h1_runtime,
)
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.h1_preseal_contracts import H1V2SealPreflight
from chiplog.composition.h1_preseal_native_source import H1PresealNativeSource
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
            deliveries=(ProposedDelivery(payload=(Commentary(text="mounted preseal source"),)),),
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
async def test_installed_runtime_mounts_one_exact_preseal_source_for_its_runtime(
    tmp_path: Path,
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)

    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            private_runtime = cast(Any, runtime)
            source = private_runtime._h1_preseal_native_source

            assert type(source) is H1PresealNativeSource
            assert source._runtime is runtime
            assert (
                private_runtime._h1_preissuance_registration_source_port._preseal_native_owner()
                is source
            )


@pytest.mark.asyncio
async def test_installed_p_and_e_owners_use_the_same_mounted_preseal_source(tmp_path: Path) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)

    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            private_runtime = cast(Any, runtime)
            preflight = await _genuine_preflight(runtime)
            source = private_runtime._h1_preseal_native_source
            port = private_runtime._h1_preissuance_registration_source_port
            native_cut = source.capture(preflight)
            capability = await port._capture_preseal_p_residual(native_cut)
            native_cut = port._preseal_scopes[id(capability)][1].native
            members = private_runtime._h1_pre_request_member_evidence
            worker = private_runtime._h1_installed_worker_evidence_owner

            assert native_cut is source._issued[id(native_cut)]
            assert members._port is port
            assert members._port._preseal_native_owner() is source
            assert worker._runtime._h1_preseal_native_source is source
            assert members._preseal_members(source, native_cut, capability)
            with runtime._authority_gate().hold():
                assert worker._current_preseal_issued(source, native_cut).runtime_instance_id


@pytest.mark.asyncio
async def test_ordinary_runtime_has_no_preseal_native_source_mount(tmp_path: Path) -> None:
    async with open_common_cli_execution_runtime(
        tmp_path / "ordinary.sqlite3", resources=_resources(tmp_path)
    ) as runtime:
        assert not hasattr(runtime, "_h1_preseal_native_source")


@pytest.mark.asyncio
async def test_restart_revokes_old_preseal_source_and_its_cut(tmp_path: Path) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)

    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            old_source = cast(Any, runtime)._h1_preseal_native_source
            old_cut = old_source.capture(await _genuine_preflight(runtime))

        with pytest.raises(ValueError, match="closed and revoked"):
            old_source.replay(old_cut)

        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as reopened:
            new_source = cast(Any, reopened)._h1_preseal_native_source
            assert new_source is not old_source
            with pytest.raises(ValueError, match="issuer-owned"):
                new_source.replay(old_cut)
