"""Mounted selected native full-cut boundary; inert DTOs never grant authority."""

from pathlib import Path

import pytest

from chiplog.architecture.r7_runtime import (
    R14_EXECUTION_RECOVERY_V26_PRODUCTION_MANIFEST,
)
from chiplog.capabilities.agent_loop.native_recovery_cut_validation import (
    validate_native_recovery_cut,
)
from chiplog.composition.common_cli_execution_runtime import open_common_cli_execution_runtime
from chiplog.composition.r14_native_recovery_cut import NativeRecoveryCutSource
from chiplog.composition.r14_recovery_profile_publication import publish_native_recovery_profile
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from tests.support.native_cancelled import native_response, selected_native_cancelled
from tests.support.recovery_candidate import inert_recovery_candidate


@pytest.mark.asyncio
@pytest.mark.parametrize("bound", (True, False))
async def test_selected_native_cut_reopens_and_legacy_prepare_holds(tmp_path: Path, bound: bool):
    database = tmp_path / "native-cut.sqlite"
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "custody"
    )
    source = NativeRecoveryCutSource()
    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        responses=(native_response(),),
        manifest=R14_EXECUTION_RECOVERY_V26_PRODUCTION_MANIFEST,
        recovery_cut_source=source,
    ) as runtime:
        await selected_native_cancelled(runtime, bound=bound)
        await runtime.publish_native_one_call_cancelled_accounting()
        assert source.collect(runtime) is None
        await publish_native_recovery_profile(runtime)
        # Call the internal method here so failure diagnostics cannot be hidden
        # behind the public HOLD projection while the positive path is mounted.
        with runtime._authority_gate().hold():
            cut = source._collect_held(runtime)
        if bound:
            assert cut is not None
            validate_native_recovery_cut(cut)
            with pytest.raises(ValueError, match="member lacks"):
                validate_native_recovery_cut(
                    cut.model_copy(
                        update={
                            "complete_sources": tuple(
                                row
                                for row in cut.complete_sources
                                if row.schema_id != "chiplog.native-recovery.turn.v1"
                            )
                        }
                    )
                )
            with runtime._authority_gate().hold():
                assert source.matches_current_held(runtime, cut)
                assert not source.matches_current_held(
                    runtime,
                    cut.model_copy(
                        update={
                            "complete_sources": cut.complete_sources[:-1],
                        }
                    ),
                )
        else:
            assert cut is None
    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        manifest=R14_EXECUTION_RECOVERY_V26_PRODUCTION_MANIFEST,
        recovery_cut_source=source,
    ) as runtime:
        assert source.collect(runtime) == cut
        if bound:
            with runtime._authority_gate().hold():
                candidate, inert_entry = inert_recovery_candidate(runtime, cut)
                assert not source.authenticate_selected_held(runtime, candidate, inert_entry)
            evidence = await runtime.publish_execution_accounting_readiness()
            assert evidence.accounting_request.cut == cut
            with runtime._authority_gate().hold():
                import json

                from chiplog.composition.r14_recovery_publication import OPERATION

                entry = next(
                    json.loads(raw)
                    for _, _, raw in runtime._loop_decisions().entries()
                    if json.loads(raw).get("operation_kind") == OPERATION
                )
                assert source.authenticate_selected_held(runtime, evidence, entry)
            resulting_cut = source.collect(runtime)
            assert resulting_cut is not None
            assert resulting_cut.tenant_commit_sequence == cut.tenant_commit_sequence + 1
            assert len(resulting_cut.complete_sources) == len(cut.complete_sources) + 2
    if not bound:
        return
    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        manifest=R14_EXECUTION_RECOVERY_V26_PRODUCTION_MANIFEST,
        recovery_cut_source=source,
    ) as runtime:
        assert source.collect(runtime) == resulting_cut
        assert await runtime.publish_execution_accounting_readiness() == evidence
        # A different selected and physical custody token is evidence even
        # when it has no row in evidence_inbox; it cannot inherit ABSENT.
        runtime.provision_retained("foreign-recovery-evidence", b"foreign evidence")
        await runtime.allocate_receipt("foreign-recovery-evidence")
        assert source.collect(runtime) is None
