"""The retained B exchange registry exists only on an installed H1 runtime."""

from __future__ import annotations

from pathlib import Path

import pytest

from chiplog.composition.common_cli_execution_runtime import (
    open_common_cli_execution_runtime,
)
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources


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
