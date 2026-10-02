"""The v21 effects extension mounts without changing the historical v20 graph."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from chiplog.composition.common_cli_execution_runtime import (
    open_installed_h1_scoped_runtime,
    open_installed_h1_scoped_v21_runtime,
)
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from tests.support.h1_installed_launch import installed_slot, prepare_installed_slot
from tests.support.h1_scoped_first_b import (
    issue_real_h1_scoped_authority,
    scoped_resources,
    write_scoped_pins,
)


@pytest.mark.asyncio
async def test_v21_scoped_runtime_mount_preserves_v20_and_mounts_producer_reader(
    tmp_path: Path,
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    write_scoped_pins(slot.database_path)
    with _open_installed_h1_launch(slot) as launch:
        v20_resources = scoped_resources(tmp_path / "v20")
        async with open_installed_h1_scoped_runtime(launch, resources=v20_resources) as v20:
            v20_manifest = v20._supervisor._manifest
            assert v20_manifest.manifest_version == 20
            assert "effects.prepare_scoped_dispatch_v3" not in {
                route.operation_id for route in v20_manifest.routes
            }
            assert getattr(v20, "_h1_producer_source_reader", None) is not None

        v21_resources = scoped_resources(tmp_path / "v21")
        async with open_installed_h1_scoped_v21_runtime(launch, resources=v21_resources) as v21:
            manifest = v21._supervisor._manifest
            assert manifest.manifest_version == 21
            assert {route.operation_id for route in manifest.routes} == {
                *{route.operation_id for route in v20_manifest.routes},
                "effects.prepare_scoped_dispatch_v3",
            }
            effects = next(owner for owner in manifest.owners if owner.owner_id == "effects")
            assert effects.target_ids == (
                "chiplog.capabilities.effects._scoped_dispatch_process:dispatch",
            )
            assert v21._supervisor._j7_operator_policy_key_pin is not None
            assert v21._supervisor._j7_operator_grant_key_pin is not None
            assert getattr(v21, "_h1_scoped_authorization_delivery_work_reader_v1", None) is None
            assert getattr(v21, "_h1_scoped_authorization_resource_reader_v4", None) is None
            assert getattr(v21, "_h1_scoped_authorization_clock_reader_v1", None) is None
            assert getattr(v21, "_h1_scoped_authorization_runtime_reader_v1", None) is None
            reader = v21._h1_producer_source_reader
            assert reader is not None
            _authority_reader, authority_capture = await issue_real_h1_scoped_authority(v21)
            capture = await reader.read(
                authority_capture=authority_capture,
                intent_id="v21-scoped-dispatch-intent",
            )
            with v21._authority_gate().hold():
                reader._require_mounted_held()
                assert reader.recheck_held(capture) is capture
            reader.release(capture)
            supported_manifest = v21._supervisor._manifest
            v21._supervisor._manifest = replace(supported_manifest, manifest_version=23)
            try:
                assert v21._supervisor._install_j7_operator_policy_key_pin() is None
                assert v21._supervisor._install_j7_operator_grant_key_pin() is None
            finally:
                v21._supervisor._manifest = supported_manifest
