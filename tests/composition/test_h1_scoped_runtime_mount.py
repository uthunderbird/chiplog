"""The v20 scoped owner route mounts without changing the v19 installed graph."""

from __future__ import annotations

from pathlib import Path

import pytest

from chiplog.composition.common_cli_execution_runtime import (
    open_installed_h1_runtime,
    open_installed_h1_scoped_runtime,
)
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from tests.composition.test_j7_mounted_external_delivery_grant import _write_grant_pin
from tests.platform.test_prepared_policy_owner_route import _write_pin
from tests.support.h1_installed_launch import installed_slot, prepare_installed_slot


@pytest.mark.asyncio
async def test_scoped_runtime_mount_preserves_local_route(tmp_path: Path) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    _write_pin(slot.database_path)
    _write_grant_pin(slot.database_path)
    with _open_installed_h1_launch(slot) as launch:
        local_resources = HermeticDispatchResources(
            scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "local-custody"
        )
        async with open_installed_h1_runtime(launch, resources=local_resources) as local:
            local_manifest = local._supervisor._manifest
            assert local_manifest.manifest_version == 19
            local_routes = {route.operation_id for route in local_manifest.routes}
            assert "effects.prepare_h1_local_commentary" in local_routes
            assert "effects.prepare_h1_scoped_delivery" not in local_routes

        scoped_resources = HermeticDispatchResources(
            scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "scoped-custody"
        )
        async with open_installed_h1_scoped_runtime(launch, resources=scoped_resources) as scoped:
            scoped_manifest = scoped._supervisor._manifest
            assert scoped_manifest.manifest_version == 20
            scoped_routes = {route.operation_id for route in scoped_manifest.routes}
            assert scoped_routes == local_routes | {"effects.prepare_h1_scoped_delivery"}
            effects = next(owner for owner in scoped_manifest.owners if owner.owner_id == "effects")
            assert effects.target_ids == (
                "chiplog.capabilities.effects._h1_scoped_process:dispatch",
            )
            assert scoped._supervisor._j7_operator_policy_key_pin is not None
            assert scoped._supervisor._j7_operator_grant_key_pin is not None
