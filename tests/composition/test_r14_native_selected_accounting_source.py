"""Mounted evidence tests for target-scoped native selected accounting facts."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest

import chiplog.composition.r14_native_selected_accounting_source as selected_source
from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.execution_contracts import (
    ConsequentialToolCall,
    ExecutionContinue,
    SelfEffectArguments,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.composition.common_cli_execution_runtime import (
    CommonCliExecutionRuntime,
    open_common_cli_execution_runtime,
)
from chiplog.composition.common_execution_driver_contracts import SelectedExecutionReceiptV1
from chiplog.composition.r14_execution_complete_seal_records import (
    RetainedExecutionCompleteSealV3,
)
from chiplog.composition.r14_loop_history import read_execution_recovery_history
from chiplog.composition.r14_native_preparation_bindings import (
    bind_native_execution_preparation_v2,
)
from chiplog.composition.r14_original_prepare_source import R14OriginalNativePrepareSource
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from tests.support.h1_cli_execution import admit_complete_script, admit_runtime_slot


@asynccontextmanager
async def _selected_v3_prepare(
    tmp_path: Path,
) -> AsyncIterator[tuple[CommonCliExecutionRuntime, CallSubjectHead]]:
    database = tmp_path / "original-prepare.sqlite"
    custody = tmp_path / "dispatch-custody"
    request, complete = await admit_complete_script(database, custody)
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1, custody_path=custody)
    async with open_common_cli_execution_runtime(
        database, resources=resources, responses=(complete,)
    ) as runtime:
        initial = await runtime.drive_input(request)
        assert isinstance(initial, SelectedExecutionReceiptV1)
        started = await runtime.begin_execution(
            "hermetic-ingress", initial.stable_run_lineage_id, initial.selected_run_head.head
        )
        captured = await runtime.capture_execution(
            "hermetic-ingress", initial.stable_run_lineage_id, started.head
        )
        await runtime.seal_execution_complete(
            "hermetic-ingress", initial.stable_run_lineage_id, captured.head, profile="H1_V3"
        )
        decision = next(
            json.loads(raw)
            for _, _, raw in runtime._loop_decisions().entries()
            if "execution_complete_seal" in json.loads(raw)
            and "V3" in json.loads(raw)["execution_complete_seal"]
        )
        retained = RetainedExecutionCompleteSealV3.model_validate_json(
            decision["execution_complete_seal"]
        )
        seal = retained.exchange.proposal.fan_out.response_seal
        yield (
            cast(CommonCliExecutionRuntime, runtime),
            CallSubjectHead(
                subject_id=seal.response_seal_id,
                revision=Present(head="record:" + seal.digest(), fingerprint=seal.digest()),
            ),
        )


async def _seal_other_native(runtime: CommonCliExecutionRuntime) -> None:
    request = await admit_runtime_slot(runtime, "other")
    initial = await runtime.drive_input(request)
    assert isinstance(initial, SelectedExecutionReceiptV1)
    response = ExecutionContinue(
        kind="Continue",
        tool_calls=(
            ConsequentialToolCall(
                call_id="other-effect",
                tool="request_self_effect",
                arguments=SelfEffectArguments(payload=b"other", bundle_members=("other",)),
            ),
        ),
    ).canonical_bytes()
    runtime._execution_model._responses = (b"already-consumed", response)
    started = await runtime.begin_execution(
        "hermetic-ingress", initial.stable_run_lineage_id, initial.selected_run_head.head
    )
    captured = await runtime.capture_execution(
        "hermetic-ingress", initial.stable_run_lineage_id, started.head
    )
    await runtime.seal_execution("hermetic-ingress", initial.stable_run_lineage_id, captured.head)


@pytest.mark.asyncio
async def test_collects_and_reopens_mounted_v3_target_facts(tmp_path: Path) -> None:
    async with _selected_v3_prepare(tmp_path) as (runtime, seal):
        projection = R14OriginalNativePrepareSource(runtime).collect(seal)
        assert projection is not None
        bindings = bind_native_execution_preparation_v2(projection)
        source = selected_source.R14NativeSelectedAccountingSource(runtime, projection, bindings)

        facts = source.collect()
        assert facts is not None
        assert facts.original_prepare == projection
        assert facts.preparation_bindings == bindings
        assert projection.prepared_run in facts.target_run_lineage
        assert len(facts.selected_native_seals) == 1
        assert facts.selected_native_seals[0].initialized_records == ()
        assert facts.target_inventory_rows == ()
        assert source.collect(projection.prepared_run) == facts
        assert source.collect_one_cancelled() is None
        await _seal_other_native(runtime)
        assert len(read_execution_recovery_history(runtime)[3]) == 2
        assert source.collect(projection.prepared_run) == facts

    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )
    async with open_common_cli_execution_runtime(
        tmp_path / "original-prepare.sqlite", resources=resources
    ) as reopened:
        assert (
            selected_source.R14NativeSelectedAccountingSource(
                reopened, projection, bindings
            ).collect(projection.prepared_run)
            == facts
        )


@pytest.mark.asyncio
async def test_holds_on_one_sided_target_physical_substitution(tmp_path: Path) -> None:
    async with _selected_v3_prepare(tmp_path) as (runtime, seal):
        projection = R14OriginalNativePrepareSource(runtime).collect(seal)
        assert projection is not None
        source = selected_source.R14NativeSelectedAccountingSource(
            runtime, projection, bind_native_execution_preparation_v2(projection)
        )
        snapshot, inventory, legacy, seals, cancellations, acceptances = (
            read_execution_recovery_history(runtime)
        )
        selected = seals[0]
        damaged = selected.proposal.sealed_run.model_copy(update={"head": "loop:damaged"})
        substituted = selected.model_copy(
            update={"proposal": selected.proposal.model_copy(update={"sealed_run": damaged})}
        )
        with patch(
            "chiplog.composition.r14_native_selected_accounting_source.read_execution_recovery_history",
            return_value=(
                snapshot,
                inventory,
                legacy,
                (substituted,),
                cancellations,
                acceptances,
            ),
        ):
            assert source.collect() is None
        foreign_captured = selected.request.captured_run.model_copy(update={"run_id": "foreign"})
        foreign_sealed = selected.proposal.sealed_run.model_copy(update={"run_id": "foreign"})
        foreign_runs = selected.model_copy(
            update={
                "request": selected.request.model_copy(update={"captured_run": foreign_captured}),
                "proposal": selected.proposal.model_copy(update={"sealed_run": foreign_sealed}),
            }
        )
        with patch(
            "chiplog.composition.r14_native_selected_accounting_source.read_execution_recovery_history",
            return_value=(
                snapshot,
                inventory,
                legacy,
                (foreign_runs,),
                cancellations,
                acceptances,
            ),
        ):
            # response.original_run_id still names target while both Run sides
            # are one-sided substitutions; it must be classified then held.
            assert source.collect() is None
        damaged_inventory = inventory.model_copy(
            update={"tenant_commit_sequence": inventory.tenant_commit_sequence + 1}
        )
        with patch(
            "chiplog.composition.r14_native_selected_accounting_source.read_execution_recovery_history",
            return_value=(
                snapshot,
                damaged_inventory,
                legacy,
                seals,
                cancellations,
                acceptances,
            ),
        ):
            assert source.collect() is None


@pytest.mark.asyncio
async def test_one_call_adapter_holds_for_duplicate_selected_target_predicate(
    tmp_path: Path,
) -> None:
    """A mounted V3 seal is duplicated only as a selected-history predicate fixture.

    It does not claim independently selected second-seal evidence.  The current
    runtime has no mounted transition that can append a second seal to this Run.
    """
    async with _selected_v3_prepare(tmp_path) as (runtime, seal):
        projection = R14OriginalNativePrepareSource(runtime).collect(seal)
        assert projection is not None
        source = selected_source.R14NativeSelectedAccountingSource(
            runtime, projection, bind_native_execution_preparation_v2(projection)
        )
        snapshot, inventory, legacy, seals, cancellations, acceptances = (
            read_execution_recovery_history(runtime)
        )
        decisions = runtime._loop_decisions().entries()
        records = snapshot.records
        emissions = tuple(runtime._execution_model.requests)
        with patch(
            "chiplog.composition.r14_native_selected_accounting_source.read_execution_recovery_history",
            return_value=(
                snapshot,
                inventory,
                legacy,
                (seals[0], seals[0]),
                cancellations,
                acceptances,
            ),
        ):
            facts = source.collect()
            assert facts is not None
            assert len(facts.selected_native_seals) == 2
            assert source.collect_one_cancelled() is None
        assert runtime._loop_decisions().entries() == decisions
        assert read_execution_recovery_history(runtime)[0].records == records
        assert tuple(runtime._execution_model.requests) == emissions
