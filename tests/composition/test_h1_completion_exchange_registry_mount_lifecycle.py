"""The retained B exchange registry exists only on an installed H1 runtime."""

from __future__ import annotations

from pathlib import Path

import pytest

from chiplog.composition.common_cli_execution_runtime import (
    open_common_cli_execution_runtime,
    open_installed_h1_runtime,
)
from chiplog.composition.h1_completion_exchange_registry import H1CompletionExchangeRegistry
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from tests.support.h1_installed_launch import installed_slot, prepare_installed_slot


@pytest.mark.asyncio
async def test_installed_h1_mounts_and_revokes_its_private_completion_exchange_registry(
    tmp_path: Path,
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )

    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=resources) as runtime:
            registry = runtime._h1_completion_exchange_registry

            assert type(registry) is H1CompletionExchangeRegistry
            assert registry._runtime is runtime
            assert registry._native_sources is runtime._h1_native_member_sources
            assert registry._scope_port is runtime._h1_preissuance_registration_source_port

        assert not hasattr(runtime, "_h1_completion_exchange_registry")
        with pytest.raises(ValueError, match="closed"):
            registry._replay_completion_exchange(object(), object())


@pytest.mark.asyncio
async def test_generic_common_runtime_never_mounts_completion_exchange_registry(
    tmp_path: Path,
) -> None:
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )

    async with open_common_cli_execution_runtime(
        tmp_path / "generic.sqlite3", resources=resources
    ) as runtime:
        assert not hasattr(runtime, "_h1_completion_exchange_registry")
