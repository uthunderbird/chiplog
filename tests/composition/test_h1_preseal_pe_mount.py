"""The H1 preseal P/E decision owner is installed only for an enrolled runtime."""

from __future__ import annotations

from pathlib import Path

import pytest

from chiplog.composition.common_cli_execution_runtime import (
    open_common_cli_execution_runtime,
)
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources


def _resources(tmp_path: Path) -> HermeticDispatchResources:
    return HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )


@pytest.mark.asyncio
async def test_common_runtime_never_mounts_preseal_pe_decision_owner(tmp_path: Path) -> None:
    async with open_common_cli_execution_runtime(
        tmp_path / "generic.sqlite3", resources=_resources(tmp_path)
    ) as runtime:
        assert not hasattr(runtime, "_h1_preseal_pe_decision_owner")
