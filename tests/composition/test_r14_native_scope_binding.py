"""Mounted original native Prepare trust binding and substitution refusals."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.composition.common_cli_execution_runtime import open_common_cli_execution_runtime
from chiplog.composition.r14_execution_transition_records import RetainedExecutionTransitionV4
from chiplog.composition.r14_native_scope_binding import verify_native_prepare_scope
from chiplog.composition.r14_original_prepare_source import R14OriginalNativePrepareSource
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.composition.r17_native_one_call_scope import NativeOneCallScopeService
from tests.support.composition.r14_native_one_call_accounting_producer import _response
from tests.support.h1_cli_execution import admit_runtime_slot


async def _capture(runtime):
    wire = await admit_runtime_slot(runtime, "native-bound-prepare")
    receipt = await runtime.drive_input(wire)
    started = await runtime.begin_execution(
        "hermetic-ingress", receipt.stable_run_lineage_id, receipt.selected_run_head.head
    )
    return await runtime.capture_execution(
        "hermetic-ingress",
        receipt.stable_run_lineage_id,
        started.head,
        native_one_call_context=True,
    )


async def test_original_native_scope_is_retained_and_reopened(tmp_path: Path) -> None:
    database = tmp_path / "native-binding.sqlite"
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "custody"
    )
    async with open_common_cli_execution_runtime(
        database, resources=resources, responses=(_response(),)
    ) as runtime:
        captured = await _capture(runtime)
        evidence = next(
            RetainedExecutionTransitionV4.model_validate_json(
                json.loads(raw)["execution_transition"]
            )
            for _, _, raw in runtime._loop_decisions().entries()
            if "R14_SELECTED_EXECUTION_TRANSITION_V4" in raw.decode()
        )
        binding = evidence.native_scope_binding
        missing = evidence.model_dump(mode="json")
        del missing["native_scope_binding"]
        with pytest.raises(ValueError):
            RetainedExecutionTransitionV4.model_validate(missing)
        h1_anchor = binding.anchor.model_dump(mode="json")
        h1_anchor["record_type_id"] = "chiplog.deployment_trust.hermetic_output_scope"
        with pytest.raises(ValueError):
            type(binding.anchor).model_validate(h1_anchor)
        verify_native_prepare_scope(runtime, evidence.request, binding, historical=True)
        with pytest.raises((ValueError, LoopRejected)):
            verify_native_prepare_scope(
                runtime,
                evidence.request.model_copy(update={"command_id": "rival-prepare"}),
                binding,
                historical=True,
            )
        with pytest.raises((ValueError, LoopRejected)):
            verify_native_prepare_scope(
                runtime,
                evidence.request,
                binding.model_copy(
                    update={
                        "anchor": binding.anchor.model_copy(
                            update={"record": binding.anchor.decision}
                        )
                    }
                ),
                historical=True,
            )
        with pytest.raises((ValueError, LoopRejected)):
            verify_native_prepare_scope(
                runtime,
                evidence.request,
                binding.model_copy(update={"record_bytes": binding.record_bytes + b" "}),
                historical=True,
            )
        sealed = await runtime.seal_execution("hermetic-ingress", captured.run_id, captured.head)
        seal = sealed.turns[0].response_seal
        projection = R14OriginalNativePrepareSource(runtime).collect(seal)
        assert projection is not None
        assert projection.original_native_scope_binding == binding
        with patch.object(
            NativeOneCallScopeService,
            "read_current",
            side_effect=AssertionError("historical must use original scope"),
        ):
            assert R14OriginalNativePrepareSource(runtime).collect(seal) == projection
    async with open_common_cli_execution_runtime(database, resources=resources) as runtime:
        assert R14OriginalNativePrepareSource(runtime).collect(seal) == projection


async def test_changed_current_scope_refuses_before_selected_prepare(tmp_path: Path) -> None:
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "custody"
    )
    async with open_common_cli_execution_runtime(
        tmp_path / "stale.sqlite", resources=resources, responses=(_response(),)
    ) as runtime:
        with (
            patch.object(
                NativeOneCallScopeService,
                "read_current",
                side_effect=LoopRejected("current selected scope changed"),
            ),
            pytest.raises(LoopRejected, match="current selected scope changed"),
        ):
            await _capture(runtime)
        assert not any(
            "R14_SELECTED_EXECUTION_TRANSITION_V4" in raw.decode()
            for _, _, raw in runtime._loop_decisions().entries()
        )
