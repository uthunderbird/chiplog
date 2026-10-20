"""Canonical installed-H1 lifecycle for the inert live publication authority."""

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
async def test_generic_runtime_has_no_live_authority_or_resolver(tmp_path: Path) -> None:
    async with open_common_cli_execution_runtime(
        tmp_path / "generic.sqlite3", resources=_resources(tmp_path)
    ) as runtime:
        assert not hasattr(runtime, "_h1_live_publication_authority")
        assert not hasattr(runtime, "_h1_live_publication_coordinator")
        assert not hasattr(runtime, "_h1_live_completion_mount")
        assert not hasattr(runtime, "_h1_live_completion_enrollment")
        assert runtime._appender._materializer._owner_publication_resolver is None
