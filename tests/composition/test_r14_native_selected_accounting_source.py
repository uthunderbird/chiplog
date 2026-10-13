"""Mounted evidence tests for target-scoped native selected accounting facts."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest

import chiplog.composition.r14_native_selected_accounting_source as selected_source
import chiplog.composition.r14_original_prepare_source as original_source
from chiplog.architecture.r7_runtime import (
    R14_R17_H1_NATIVE_ONE_CALL_ACCOUNTING_V23_PRODUCTION_MANIFEST,
)
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
from chiplog.composition.h1_selected_prepare import select_h1_v3_prepare_for_native_fanout
from chiplog.composition.r14_call_acceptance_port import (
    CallAcceptanceAdoption,
    CallAcceptanceTarget,
)
from chiplog.composition.r14_cancellation_contracts import CancelCallSubmission
from chiplog.composition.r14_execution_complete_seal_records import (
    RetainedExecutionCompleteSealV3,
)
from chiplog.composition.r14_execution_fanout_records import reference
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


@asynccontextmanager
async def _selected_v23_native_fanout(
    tmp_path: Path,
    *,
    tool_calls: tuple[ConsequentialToolCall, ...] | None = None,
    accepted_call_id: str | None = None,
    cancelled_call_id: str = "effect",
) -> AsyncIterator[tuple[CommonCliExecutionRuntime, CallSubjectHead]]:
    database = tmp_path / "native-v23-prepare.sqlite"
    custody = tmp_path / "dispatch-custody"
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1, custody_path=custody)
    response = ExecutionContinue(
        kind="Continue",
        tool_calls=tool_calls
        or (
            ConsequentialToolCall(
                call_id="effect",
                tool="request_self_effect",
                arguments=SelfEffectArguments(payload=b"effect", bundle_members=("one",)),
            ),
        ),
    ).canonical_bytes()
    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        responses=(response,),
        manifest=R14_R17_H1_NATIVE_ONE_CALL_ACCOUNTING_V23_PRODUCTION_MANIFEST,
    ) as runtime:
        request = await admit_runtime_slot(runtime, "native-v23")
        initial = await runtime.drive_input(request)
        assert isinstance(initial, SelectedExecutionReceiptV1)
        started = await runtime.begin_execution(
            "hermetic-ingress", initial.stable_run_lineage_id, initial.selected_run_head.head
        )
        captured = await runtime.capture_execution(
            "hermetic-ingress", initial.stable_run_lineage_id, started.head
        )
        await runtime.seal_execution(
            "hermetic-ingress", initial.stable_run_lineage_id, captured.head
        )
        _snapshot, inventory, _legacy, seals, _cancellations, _acceptances = (
            read_execution_recovery_history(runtime)
        )
        if accepted_call_id is not None:
            accepted_row = next(
                item
                for item in inventory.ordered_calls
                if item.initialized_record.call.original.model_call_label == accepted_call_id
            )
            accepted_preview = await runtime.preview_call_acceptance(
                "hermetic-ingress",
                CallAcceptanceTarget(
                    original_call_id=accepted_row.original_call_id,
                    initialized=accepted_row.initialized,
                    current_run=CallSubjectHead(
                        subject_id=seals[0].proposal.sealed_run.run_id,
                        revision=Present(
                            head=seals[0].proposal.sealed_run.head,
                            fingerprint=seals[0].proposal.sealed_run.digest(),
                        ),
                    ),
                ),
            )
            await runtime.accept_call(
                "hermetic-ingress",
                CallAcceptanceAdoption(
                    act_id="accept-native-member", preview_bytes=accepted_preview.canonical_bytes()
                ),
            )
        row = next(
            (
                item
                for item in inventory.ordered_calls
                if item.initialized_record.call.original.model_call_label == cancelled_call_id
            ),
            None,
        )
        assert row is not None
        sealed = seals[0].proposal.sealed_run
        await runtime.cancel_execution_call(
            "hermetic-ingress",
            CancelCallSubmission(
                act_id="cancel-native-effect",
                original_call_id=row.original_call_id,
                initialized=reference(
                    row.initialized_record.original_call_id, row.initialized_record
                ),
                current_run=CallSubjectHead(
                    subject_id=sealed.run_id,
                    revision=Present(head=sealed.head, fingerprint=sealed.digest()),
                ),
            ),
        )
        seal = seals[0].proposal.fan_out.response_seal
        yield cast(CommonCliExecutionRuntime, runtime), CallSubjectHead(
            subject_id=seal.response_seal_id,
            revision=Present(head="record:" + seal.digest(), fingerprint=seal.digest()),
        )


@pytest.mark.asyncio
async def test_cancelled_member_projection_uses_call_identity_in_multi_call_seal(
    tmp_path: Path,
) -> None:
    calls = (
        ConsequentialToolCall(
            call_id="first-effect",
            tool="request_self_effect",
            arguments=SelfEffectArguments(payload=b"first", bundle_members=("first",)),
        ),
        ConsequentialToolCall(
            call_id="second-effect",
            tool="request_self_effect",
            arguments=SelfEffectArguments(payload=b"second", bundle_members=("second",)),
        ),
    )
    async with _selected_v23_native_fanout(
        tmp_path,
        tool_calls=calls,
        accepted_call_id="first-effect",
        cancelled_call_id="second-effect",
    ) as (runtime, seal):
        projection = R14OriginalNativePrepareSource(runtime).collect(seal)
        assert projection is not None
        source = selected_source.R14NativeSelectedAccountingSource(
            runtime, projection, bind_native_execution_preparation_v2(projection)
        )
        source_facts = source.collect(projection.prepared_run)
        assert source_facts is not None
        second_original_id = next(
            row.original_call_id
            for row in source_facts.target_inventory_rows
            if row.initialized_record.call.original.model_call_label == "second-effect"
        )
        facts = source.collect_one_cancelled(
            projection.prepared_run, original_call_id=second_original_id
        )
        assert facts is not None
        assert tuple(
            item.call.original.model_call_label
            for item in facts.selected_seal.initialized_records
        ) == ("first-effect", "second-effect")
        assert facts.original_call_id == second_original_id
        assert source.collect_one_cancelled(
            projection.prepared_run,
            original_call_id=next(
                row.original_call_id
                for row in source_facts.target_inventory_rows
                if row.initialized_record.call.original.model_call_label == "first-effect"
            ),
        ) is None
        first_original_id = next(
            row.original_call_id
            for row in source_facts.target_inventory_rows
            if row.initialized_record.call.original.model_call_label == "first-effect"
        )
        accepted = source.collect_one_accepted(
            projection.prepared_run, original_call_id=first_original_id
        )
        assert accepted is not None
        assert accepted.original_call_id == first_original_id

    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )
    async with open_common_cli_execution_runtime(
        tmp_path / "native-v23-prepare.sqlite",
        resources=resources,
        manifest=R14_R17_H1_NATIVE_ONE_CALL_ACCOUNTING_V23_PRODUCTION_MANIFEST,
    ) as reopened:
        reopened_source = selected_source.R14NativeSelectedAccountingSource(
            reopened, projection, bind_native_execution_preparation_v2(projection)
        )
        reopened_cancelled = reopened_source.collect_one_cancelled(
            projection.prepared_run, original_call_id=second_original_id
        )
        reopened_accepted = reopened_source.collect_one_accepted(
            projection.prepared_run, original_call_id=first_original_id
        )
        assert reopened_cancelled is not None
        assert reopened_accepted is not None
        assert reopened_accepted.acceptance == accepted.acceptance


@pytest.mark.asyncio
async def test_collects_and_reopens_mounted_v3_target_facts(tmp_path: Path) -> None:
    async with _selected_v23_native_fanout(tmp_path) as (runtime, seal):
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
        assert len(facts.selected_native_seals[0].initialized_records) == 1
        assert len(facts.target_inventory_rows) == 1
        assert source.collect(projection.prepared_run) == facts
        cancelled = source.collect_one_cancelled(projection.prepared_run)
        assert cancelled is not None
        assert cancelled.source.original_prepare.prepared_run == projection.prepared_run
        await _seal_other_native(runtime)
        assert len(read_execution_recovery_history(runtime)[3]) == 2
        assert source.collect(projection.prepared_run) == facts

    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )
    async with open_common_cli_execution_runtime(
        tmp_path / "native-v23-prepare.sqlite",
        resources=resources,
        manifest=R14_R17_H1_NATIVE_ONE_CALL_ACCOUNTING_V23_PRODUCTION_MANIFEST,
    ) as reopened:
        reopened_source = selected_source.R14NativeSelectedAccountingSource(
            reopened, projection, bindings
        )
        assert reopened_source.collect(projection.prepared_run) == facts
        reopened_cancelled = reopened_source.collect_one_cancelled(projection.prepared_run)
        assert reopened_cancelled is not None
        assert reopened_cancelled.source.original_prepare.prepared_run == projection.prepared_run


@pytest.mark.asyncio
async def test_holds_on_one_sided_target_physical_substitution(tmp_path: Path) -> None:
    async with _selected_v23_native_fanout(tmp_path) as (runtime, seal):
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
        engine = runtime._supervisor.runtime()
        selected = seals[0]
        damaged = selected.proposal.sealed_run.model_copy(update={"head": "loop:damaged"})
        substituted = selected.model_copy(
            update={"proposal": selected.proposal.model_copy(update={"sealed_run": damaged})}
        )
        with (
            patch(
                "chiplog.composition.r14_native_selected_accounting_source.read_execution_recovery_history",
                return_value=(
                    snapshot,
                    inventory,
                    legacy,
                    (substituted,),
                    cancellations,
                    acceptances,
                ),
            ),
            patch.object(engine, "call", new_callable=AsyncMock) as owner_call,
        ):
            assert source.collect() is None
            owner_call.assert_not_awaited()
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
        assert runtime._loop_decisions().entries() == decisions
        assert read_execution_recovery_history(runtime)[0].records == records


@pytest.mark.asyncio
async def test_v23_native_prepare_reopen_holds_for_locator_physical_or_prompt_substitution(
    tmp_path: Path,
) -> None:
    async with _selected_v23_native_fanout(tmp_path) as (runtime, seal):
        source = R14OriginalNativePrepareSource(runtime)
        decisions = runtime._loop_decisions().entries()
        records = read_execution_recovery_history(runtime)[0].records
        engine = runtime._supervisor.runtime()
        selected = select_h1_v3_prepare_for_native_fanout(runtime, selected_seal=seal)
        assert selected is not None
        changed_locator = seal.model_copy(
            update={"revision": Present(head=seal.revision.head, fingerprint="0" * 64)}
        )
        with patch.object(engine, "call", new_callable=AsyncMock) as owner_call:
            assert source.collect(changed_locator) is None
            owner_call.assert_not_awaited()
        with (
            patch(
                "chiplog.composition.h1_selected_prepare.read_execution_recovery_history",
                side_effect=ValueError("fanout history rejected"),
            ),
            patch.object(engine, "call", new_callable=AsyncMock) as owner_call,
        ):
            assert source.collect(seal) is None
            owner_call.assert_not_awaited()
        changed_physical = replace(
            selected,
            prepare=replace(
                selected.prepare,
                physical_run=replace(selected.prepare.physical_run, canonical_bytes=b"{}"),
            ),
        )
        prompt = selected.captured_run.turns[0].attempts[0].manifest.members[2]
        changed_prompt = replace(
            selected,
            captured_run=selected.captured_run.model_copy(
                update={
                    "turns": (
                        selected.captured_run.turns[0].model_copy(
                            update={
                                "attempts": (
                                    selected.captured_run.turns[0].attempts[0].model_copy(
                                        update={
                                            "manifest": selected.captured_run.turns[0]
                                            .attempts[0]
                                            .manifest.model_copy(
                                                update={
                                                    "members": (
                                                        *selected.captured_run.turns[0]
                                                        .attempts[0]
                                                        .manifest.members[:2],
                                                        prompt.model_copy(
                                                            update={"content": "rival"}
                                                        ),
                                                        selected.captured_run.turns[0]
                                                        .attempts[0]
                                                        .manifest.members[3],
                                                    )
                                                }
                                            )
                                        }
                                    ),
                                )
                            }
                        ),
                    )
                }
            ),
        )
        for changed in (changed_physical, changed_prompt):
            with (
                patch.object(
                    original_source,
                    "select_h1_v3_prepare_for_native_fanout",
                    return_value=changed,
                ),
                patch.object(engine, "call", new_callable=AsyncMock) as owner_call,
            ):
                assert source.collect(seal) is None
                owner_call.assert_not_awaited()
        assert runtime._loop_decisions().entries() == decisions
        assert read_execution_recovery_history(runtime)[0].records == records


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
