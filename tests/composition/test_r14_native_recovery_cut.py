"""Mounted selected native full-cut boundary; inert DTOs never grant authority."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from chiplog.architecture.r7_runtime import (
    R14_EXECUTION_RECOVERY_V26_PRODUCTION_MANIFEST,
)
from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallPreparationRejected
from chiplog.capabilities.agent_loop.execution_recovery_contracts import PrepareExecutionAccounting
from chiplog.capabilities.agent_loop.execution_recovery_preparation import (
    prepare_execution_accounting,
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


@pytest.mark.parametrize(
    "schema_id",
    (
        "chiplog.execution.accepted-open-accounting.v1",
        "chiplog.execution.accepted-open-recovery-opening.v1",
        "chiplog.loop.original-resolver-batch.v1",
        "chiplog.loop.semantic-reduction.v1",
        "chiplog.loop.semantic-reducer-policy.v1",
    ),
)
def test_direct_selector_holds_rival_r14_schema_under_foreign_operation(schema_id: str):
    raw = json.dumps({"kind": "DECIDED"}).encode()
    command = SimpleNamespace(
        operation_kind="custom.foreign-operation.v1",
        records=(SimpleNamespace(schema_id=schema_id),),
    )
    log = SimpleNamespace(entries=lambda: (("decision", "unused", raw),))
    runtime = SimpleNamespace(_loop_decisions=lambda: log, _publication=lambda entry: command)

    claimed, selected = NativeRecoveryCutSource._selected_accepted_direct_terminal_inputs(
        runtime, None
    )

    assert claimed
    assert selected is None


def test_direct_selector_holds_retained_rival_marker_under_foreign_operation():
    raw = json.dumps({"kind": "DECIDED", "original_call_resolution_evidence": "retained"}).encode()
    command = SimpleNamespace(operation_kind="custom.foreign-operation.v1", records=())
    log = SimpleNamespace(entries=lambda: (("decision", "unused", raw),))
    runtime = SimpleNamespace(_loop_decisions=lambda: log, _publication=lambda entry: command)

    claimed, selected = NativeRecoveryCutSource._selected_accepted_direct_terminal_inputs(
        runtime, None
    )

    assert claimed
    assert selected is None


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
        await publish_native_recovery_profile(runtime)
        await selected_native_cancelled(runtime, bound=bound)
        await runtime.publish_native_one_call_cancelled_accounting()
        if not bound:
            assert source.collect(runtime) is None
        # Call the internal method here so failure diagnostics cannot be hidden
        # behind the public HOLD projection while the positive path is mounted.
        with runtime._authority_gate().hold():
            cut = source._collect_held(runtime)
        if bound:
            assert cut is not None
            validate_native_recovery_cut(cut)
            profileless_v2_cut = cut.model_copy(
                update={
                    "complete_sources": tuple(
                        row
                        for row in cut.complete_sources
                        if row.schema_id
                        not in (
                            "chiplog.execution.native-one-call-recovery-profile.v1",
                            "chiplog.execution.native-one-call-recovery-profile.v2",
                        )
                    )
                }
            )
            rejected = prepare_execution_accounting(
                PrepareExecutionAccounting(command_id="profileless-v2", cut=profileless_v2_cut)
            )
            assert isinstance(rejected, CallPreparationRejected)
            assert rejected.code == "HOLD"
            assert rejected.reason == "V2 registry requires exactly one selected V2/V3 profile"
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
