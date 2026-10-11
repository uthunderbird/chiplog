"""Mounted suspension admission fails closed before a complete selected cut exists."""

import pytest

from chiplog.architecture.r7_runtime import R14_EXECUTION_SUSPENSION_V35_PRODUCTION_MANIFEST
from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.composition.common_cli_execution_runtime import open_common_cli_execution_runtime
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources


async def test_mounted_suspension_holds_without_authenticated_active_run(tmp_path):
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "custody"
    )
    async with open_common_cli_execution_runtime(
        tmp_path / "suspension.sqlite",
        resources=resources,
        manifest=R14_EXECUTION_SUSPENSION_V35_PRODUCTION_MANIFEST,
    ) as runtime:
        before = runtime._commitment_journal.load(runtime._tenant_id)
        with pytest.raises(LoopRejected, match="complete authenticated suspension cut"):
            await runtime.suspend_execution("missing-run", "missing-head", ())
        assert runtime._commitment_journal.load(runtime._tenant_id) == before
        assert runtime.read_selected_execution_suspension() is None
