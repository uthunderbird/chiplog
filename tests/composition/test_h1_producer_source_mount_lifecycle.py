"""The scoped H1 producer sources are mounted only for the v20 runtime."""

from __future__ import annotations

from pathlib import Path

import pytest

from chiplog.composition.common_cli_execution_runtime import (
    open_installed_h1_runtime,
)
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from tests.support.h1_installed_launch import installed_slot, prepare_installed_slot


def _resources(tmp_path: Path) -> HermeticDispatchResources:
    return HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )


@pytest.mark.asyncio
async def test_v19_installed_h1_never_mounts_scoped_producer_source_readers(tmp_path: Path) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)

    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            assert not hasattr(runtime, "_h1_producer_source_reader")
            assert not hasattr(runtime, "_h1_producer_inventory_reader")
            assert not hasattr(runtime, "_h1_scoped_delivery_authority_reader")
