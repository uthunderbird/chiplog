"""Mounted runtime selects and reads the registered V5 recovery profile."""

from pathlib import Path

import pytest

from chiplog.architecture.r7_runtime import (
    R14_EXECUTION_OBLIGATION_RESUME_V37_PRODUCTION_MANIFEST,
)
from chiplog.capabilities.agent_loop.native_recovery_profile_contracts import SCHEMA_V5
from chiplog.composition.common_cli_execution_runtime import (
    open_common_cli_execution_runtime,
)
from chiplog.composition.r14_loop_history import read_execution_history
from chiplog.composition.r14_recovery_profile_publication import (
    read_native_recovery_profile_held,
)
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources


@pytest.mark.asyncio
async def test_v5_profile_is_selected_and_authenticated_by_mounted_history(
    tmp_path: Path,
) -> None:
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "custody"
    )
    async with open_common_cli_execution_runtime(
        tmp_path / "r14-v5-profile.sqlite",
        resources=resources,
        manifest=R14_EXECUTION_OBLIGATION_RESUME_V37_PRODUCTION_MANIFEST,
    ) as runtime:
        await runtime.publish_native_recovery_profile(version="v5")
        with runtime._authority_gate().hold():
            selected = read_native_recovery_profile_held(runtime)
        assert selected is not None
        assert selected.schema_id == SCHEMA_V5
        assert len(read_execution_history(runtime).records) == 0
