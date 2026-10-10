"""Mounted witness for the bounded, non-complete native recovery source bridge."""

from pathlib import Path

import pytest

from chiplog.adapters.driven.effects_hermetic import HermeticResponseLost
from chiplog.architecture.r7_runtime import (
    R14_EXECUTION_LOOP_SEMANTIC_REDUCTION_V34_PRODUCTION_MANIFEST,
    R14_R17_H1_NATIVE_ONE_CALL_ACCOUNTING_V24_PRODUCTION_MANIFEST,
)
from chiplog.composition.common_cli_execution_runtime import open_common_cli_execution_runtime
from chiplog.composition.r14_call_acceptance_port import (
    CallAcceptanceAdoption,
    CallAcceptanceTarget,
)
from chiplog.composition.r14_call_preview import execution_run_head
from chiplog.composition.r14_loop_history import read_execution_call_history
from chiplog.composition.r14_native_recovery_source_subset import (
    R14NativeRecoverySourceSubsetReader,
)
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from tests.support.h1_cli_execution import admit_runtime_slot
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


@pytest.mark.asyncio
async def test_source_subset_reopens_exact_accepted_closed_outcome_without_resend(
    tmp_path: Path,
) -> None:
    database = tmp_path / "native-recovery-accepted-sources.sqlite"
    resources = HermeticDispatchResources(
        scenarios=("LOST_RESPONSE_AFTER_EFFECT",), cap=1, custody_path=tmp_path / "custody"
    )
    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        responses=(native_response(),),
        manifest=R14_EXECUTION_LOOP_SEMANTIC_REDUCTION_V34_PRODUCTION_MANIFEST,
    ) as runtime:
        wire = await admit_runtime_slot(runtime, "native-accepted-recovery")
        receipt = await runtime.drive_input(wire)
        started = await runtime.begin_execution(
            "hermetic-ingress", receipt.stable_run_lineage_id, receipt.selected_run_head.head
        )
        captured = await runtime.capture_execution(
            "hermetic-ingress", started.run_id, started.head, native_one_call_context=True
        )
        sealed = await runtime.seal_execution("hermetic-ingress", captured.run_id, captured.head)
        _, inventory, _ = read_execution_call_history(runtime)
        calls = tuple(
            item
            for item in inventory.ordered_calls
            if item.initialized_record.call.classification == "CONSEQUENTIAL"
        )
        assert len(calls) == 1
        preview = await runtime.preview_call_acceptance(
            "hermetic-ingress",
            CallAcceptanceTarget(
                original_call_id=calls[0].original_call_id,
                initialized=calls[0].initialized,
                current_run=execution_run_head(sealed),
            ),
        )
        accepted = await runtime.accept_call(
            "hermetic-ingress",
            CallAcceptanceAdoption(
                act_id="accept-native-recovery", preview_bytes=preview.canonical_bytes()
            ),
        )
        intent_id = accepted.external_intent.subject_id
        await runtime.authorize_dispatch(
            "hermetic-ingress", intent_id, "authorize-native-recovery", sealed.run_id
        )
        await runtime.commit_first_send(
            "hermetic-ingress", intent_id, "send-native-recovery", sealed.run_id
        )
        with pytest.raises(HermeticResponseLost):
            await runtime.emit_committed("hermetic-ingress", intent_id)
        await runtime.publish_accepted_open_recovery()
        await runtime.observe_dispatch_outcome(intent_id)
        await runtime.resolve_dispatch_outcome(
            "hermetic-ingress", intent_id, "close-native-recovery"
        )

        subset = R14NativeRecoverySourceSubsetReader(runtime).collect()
        assert subset is not None
        assert subset.selected_accounting is None
        assert subset.accepted_closed_outcome is not None
        assert subset.authenticated_acceptances == (
            subset.accepted_closed_outcome.accepted.acceptance,
        )
        assert subset.accepted_closed_outcome.accepted.original_call_id == calls[0].original_call_id
        assert len(resources.require_original_provider().transfers) == 1

    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        responses=(native_response(),),
        manifest=R14_EXECUTION_LOOP_SEMANTIC_REDUCTION_V34_PRODUCTION_MANIFEST,
    ) as reopened:
        assert R14NativeRecoverySourceSubsetReader(reopened).collect() == subset
        assert len(resources.require_original_provider().transfers) == 1
