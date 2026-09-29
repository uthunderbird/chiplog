"""The scoped H1 producer sources are mounted only for the v20 runtime."""

from __future__ import annotations

from pathlib import Path

import pytest

from chiplog.composition.common_cli_execution_runtime import (
    open_installed_h1_runtime,
    open_installed_h1_scoped_runtime,
)
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.h1_producer_sources import H1ProducerSourceReader
from chiplog.composition.h1_scoped_delivery_authority import H1ScopedDeliveryAuthorityReader
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from tests.support.h1_installed_launch import installed_slot, prepare_installed_slot


def _resources(tmp_path: Path) -> HermeticDispatchResources:
    return HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )


@pytest.mark.asyncio
async def test_scoped_installed_h1_mounts_and_closes_exact_producer_source_readers(
    tmp_path: Path,
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)

    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_scoped_runtime(
            launch, resources=_resources(tmp_path)
        ) as runtime:
            authority_reader = runtime._h1_scoped_delivery_authority_reader
            producer_reader = runtime._h1_producer_source_reader

            assert type(authority_reader) is H1ScopedDeliveryAuthorityReader
            assert authority_reader._runtime is runtime
            assert type(producer_reader) is H1ProducerSourceReader
            assert producer_reader._runtime is runtime
            assert producer_reader._authority_reader is authority_reader

        assert producer_reader._closed is True
        assert not hasattr(runtime, "_h1_producer_source_reader")
        assert not hasattr(runtime, "_h1_scoped_delivery_authority_reader")


@pytest.mark.asyncio
async def test_v19_installed_h1_never_mounts_scoped_producer_source_readers(tmp_path: Path) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)

    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            assert not hasattr(runtime, "_h1_producer_source_reader")
            assert not hasattr(runtime, "_h1_scoped_delivery_authority_reader")
