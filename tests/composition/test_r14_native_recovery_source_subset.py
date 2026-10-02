"""Mounted witness for the bounded, non-complete native recovery source bridge."""

from pathlib import Path

import pytest

from chiplog.architecture.r7_runtime import (
    R14_R17_H1_NATIVE_ONE_CALL_ACCOUNTING_V24_PRODUCTION_MANIFEST,
)
from chiplog.composition.common_cli_execution_runtime import open_common_cli_execution_runtime
from chiplog.composition.r14_native_recovery_source_subset import (
    R14NativeRecoverySourceSubsetReader,
)
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from tests.support.native_cancelled import native_response, selected_native_cancelled


@pytest.mark.asyncio
async def test_source_subset_requires_selected_accounting_and_reopens(tmp_path: Path) -> None:
    database = tmp_path / "native-recovery-sources.sqlite"
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "custody"
    )
    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        responses=(native_response(),),
        manifest=R14_R17_H1_NATIVE_ONE_CALL_ACCOUNTING_V24_PRODUCTION_MANIFEST,
    ) as runtime:
        await selected_native_cancelled(runtime, bound=False)
        assert R14NativeRecoverySourceSubsetReader(runtime).collect() is None
        accounting = await runtime.publish_native_one_call_cancelled_accounting()
        subset = R14NativeRecoverySourceSubsetReader(runtime).collect()
        assert subset is not None
        assert subset.selected_accounting == accounting
        assert tuple(source.schema_id for source in subset.execution_sources)[-4:] == (
            "chiplog.call.response-seal.v1",
            "chiplog.call.initialized.v1",
            "chiplog.call.cancelled-before-accept.v1",
            "chiplog.call.not-executed-result.v1",
        )
        physical_ids = {source.physical_record.subject_id for source in subset.execution_sources}
        assert len(physical_ids) == len(subset.execution_sources)
    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        manifest=R14_R17_H1_NATIVE_ONE_CALL_ACCOUNTING_V24_PRODUCTION_MANIFEST,
    ) as reopened:
        assert R14NativeRecoverySourceSubsetReader(reopened).collect() == subset
