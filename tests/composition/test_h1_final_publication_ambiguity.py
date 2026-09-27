"""RED witnesses for the last durable H1 publication boundaries.

These tests deliberately start with an installed slot and a real V3 -> V2
seal.  They do not manufacture a recovery root, B response, selected owner
decision, physical batch, intent, or transmission attempt.  Until the public
driver turns that installed four-stage recovery into a terminal H1 V2 batch,
the only permitted RED outcome is ``H1FinalPublicationUnavailable``.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, cast

import pytest

from chiplog.adapters.driven.deployment_trust._journal import IndependentTenantDecisionJournal
from chiplog.capabilities.agent_loop.delivery_preparation import (
    Commentary,
    DeliveryCompletion,
    ProposedDelivery,
)
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.composition.common_cli_execution_runtime import (
    CommonCliExecutionRuntime,
    open_installed_h1_runtime,
)
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.r14_execution_completion_records import COMPLETE_ACCEPTANCE_OPERATION
from chiplog.composition.r14_runtime import AnchoredOwnerDecisionJournal
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform._owner_publication_contracts import CompleteDeliveryBatchV2
from tests.support.h1_cli_execution import _admit, advance
from tests.support.h1_installed_launch import TENANT, installed_slot, prepare_installed_slot


class H1FinalPublicationUnavailable(RuntimeError):
    """The public H1 driver has not yet selected a terminal physical batch."""


class _CrashAfterDecided(RuntimeError):
    """Process loss after durable H1 selection and before physical materialization."""


class _CrashAfterSelectedBeforeReadback(RuntimeError):
    """Process loss after raw selected bytes reach the journal, before its scan."""


def _resources(
    tmp_path: Path, *, scenarios: tuple[str, ...] = ("CONFIRM",)
) -> HermeticDispatchResources:
    return HermeticDispatchResources(
        scenarios=cast(Any, scenarios), cap=1, custody_path=tmp_path / "dispatch-custody"
    )


async def _seal_real_v3_then_v2(runtime: CommonCliExecutionRuntime) -> tuple[Any, Any]:
    """Leave a real selected V2 seal for installed recovery; do not open B here."""
    request = await _admit(runtime)
    initial = await runtime.drive_input(request)
    assert initial.kind == "SELECTED_EXECUTION_RECEIPT_V1"
    private = cast(Any, runtime)
    private._execution_model._responses = (
        DeliveryCompletion(
            tenant=TENANT,
            run_id=initial.stable_run_lineage_id,
            turn_id=initial.stable_run_lineage_id + "/turn/1",
            deliveries=(
                ProposedDelivery(payload=(Commentary(text="final publication ambiguity"),)),
            ),
        ).canonical_bytes(),
    )
    started = await runtime.begin_execution(
        "hermetic-ingress", initial.stable_run_lineage_id, initial.selected_run_head.head
    )
    captured = await runtime.capture_execution(
        "hermetic-ingress", initial.stable_run_lineage_id, started.head
    )
    sealed = await runtime.seal_execution_complete(
        "hermetic-ingress", initial.stable_run_lineage_id, captured.head, profile="H1_V2"
    )
    assert sealed.state == "ACTIVE"
    assert sealed.event == "ModelCompletionPrepared"
    assert private._h1_postseal_recovery_journal.scan().states_by_root == ()
    return request, initial


def _require_final(receipt: object) -> Any:
    """Keep the active RED attached to H1, never to R8/bootstrap mechanics."""
    if getattr(receipt, "phase", None) == "RUNNING":
        raise H1FinalPublicationUnavailable(
            "H1 final physical publication is unavailable: installed four-stage B returned "
            "RUNNING where the terminal CompleteDeliveryBatchV2 receipt is required"
        )
    assert getattr(receipt, "phase", None) == "TERMINAL", receipt
    assert getattr(receipt, "selected_run_state", None) == "SUCCEEDED", receipt
    return receipt


def _selected_h1(runtime: CommonCliExecutionRuntime) -> tuple[Any, ...]:
    return tuple(
        decision
        for decision in runtime._owner_decisions().snapshot().decisions
        if type(decision.prepared.request) is CompleteDeliveryBatchV2
        and decision.prepared.request.operation == COMPLETE_ACCEPTANCE_OPERATION
    )


def _selected_bytes(runtime: CommonCliExecutionRuntime) -> tuple[tuple[str, str, bytes], ...]:
    raw_by_head = {
        head: raw for head, _predecessor, raw in runtime._owner_decisions()._raw.entries()
    }
    return tuple(
        (
            decision.decision_id,
            decision.decision_head,
            raw_by_head[decision.decision_head],
        )
        for decision in _selected_h1(runtime)
    )


def _terminal_runs(database: Path) -> tuple[bytes, ...]:
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT canonical_bytes FROM records "
            "WHERE owner='agent_loop' ORDER BY commit_sequence, rowid"
        ).fetchall()
    return tuple(raw for (raw,) in rows if _is_terminal_run(raw))


def _is_terminal_run(raw: bytes) -> bool:
    try:
        run = ExecutionRunRecord.model_validate_json(raw)
    except TypeError, ValueError:
        return False
    return run.state == "SUCCEEDED" and run.event == "ExecutionCompleted"


def _model_and_b_counts(runtime: CommonCliExecutionRuntime) -> tuple[int, int]:
    private = cast(Any, runtime)
    journal = private._h1_postseal_recovery_journal.scan()
    b_results = sum(len(dict(state.results)) for _, state in journal.states_by_root)
    return len(private._execution_model.requests), b_results


def _assert_one_final_cut(runtime: CommonCliExecutionRuntime, database: Path) -> tuple[bytes, ...]:
    selected = _selected_h1(runtime)
    assert len(selected) == 1
    batch = selected[0].prepared.request
    assert type(batch) is CompleteDeliveryBatchV2
    assert batch.operation == COMPLETE_ACCEPTANCE_OPERATION
    runs = _terminal_runs(database)
    assert len(runs) == 1
    return runs


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    raises=H1FinalPublicationUnavailable,
    reason="H1 final CompleteDeliveryBatchV2 selection/materialization is not public yet",
)
async def test_final_h1_decided_before_materialization_reopens_exact_selected_bytes_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A durable DECIDED H1 batch survives restart without re-running model or B."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    resources = _resources(tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=resources) as runtime:
            request, initial = await _seal_real_v3_then_v2(runtime)

        original_select = AnchoredOwnerDecisionJournal.select

        def crash_after_durable_h1_selection(self: Any, prepared: Any, commitment: str) -> Any:
            selected = original_select(self, prepared, commitment)
            request_batch = selected.prepared.request
            if (
                type(request_batch) is CompleteDeliveryBatchV2
                and request_batch.operation == COMPLETE_ACCEPTANCE_OPERATION
            ):
                raise _CrashAfterDecided()
            return selected

        with monkeypatch.context() as patch:
            patch.setattr(AnchoredOwnerDecisionJournal, "select", crash_after_durable_h1_selection)
            async with open_installed_h1_runtime(launch, resources=resources) as crashed:
                try:
                    await crashed.advance_execution(advance(initial, request))
                except _CrashAfterDecided:
                    selected_before = _selected_bytes(crashed)
                    assert len(selected_before) == 1
                    assert _terminal_runs(slot.database_path) == ()
                    assert _model_and_b_counts(crashed) == (0, 4)
                else:
                    _require_final(await crashed.advance_execution(advance(initial, request)))

        async with open_installed_h1_runtime(launch, resources=resources) as reopened:
            terminal = _require_final(await reopened.advance_execution(advance(initial, request)))
            assert (
                terminal.selected_journal_decision.head == _selected_h1(reopened)[0].decision_head
            )
            assert _selected_bytes(reopened) == selected_before
            assert _model_and_b_counts(reopened) == (0, 4)
            assert len(_assert_one_final_cut(reopened, slot.database_path)) == 1


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    raises=H1FinalPublicationUnavailable,
    reason="H1 final CompleteDeliveryBatchV2 selection/readback is not public yet",
)
async def test_final_h1_selected_before_readback_reopens_one_exact_decision_and_terminal_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Raw selected bytes are reconciled from the journal, never selected a second time."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    resources = _resources(tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=resources) as runtime:
            request, initial = await _seal_real_v3_then_v2(runtime)

        original_append = IndependentTenantDecisionJournal.append

        def append_then_lose_selected_readback(
            self: Any, raw: bytes, predecessor: str | None
        ) -> str:
            head = original_append(self, raw, predecessor)
            entry = json.loads(raw)
            request_value = entry.get("request", {})
            if (
                entry.get("kind") == "SELECTED"
                and request_value.get("kind") == "COMPLETE_DELIVERY_ATOMIC_V2"
                and request_value.get("operation") == COMPLETE_ACCEPTANCE_OPERATION
            ):
                raise _CrashAfterSelectedBeforeReadback()
            return head

        with monkeypatch.context() as patch:
            patch.setattr(
                IndependentTenantDecisionJournal, "append", append_then_lose_selected_readback
            )
            async with open_installed_h1_runtime(launch, resources=resources) as crashed:
                try:
                    await crashed.advance_execution(advance(initial, request))
                except _CrashAfterSelectedBeforeReadback:
                    # The only observation allowed after this cut is a reopened,
                    # authenticated journal.  Do not treat the old wrapper as a
                    # successful selection readback.
                    selected_before = _selected_bytes(crashed)
                    assert len(selected_before) == 1
                    assert _terminal_runs(slot.database_path) == ()
                    assert _model_and_b_counts(crashed) == (0, 4)
                else:
                    _require_final(await crashed.advance_execution(advance(initial, request)))

        async with open_installed_h1_runtime(launch, resources=resources) as reopened:
            terminal = _require_final(await reopened.advance_execution(advance(initial, request)))
            assert (
                terminal.selected_journal_decision.head == _selected_h1(reopened)[0].decision_head
            )
            assert _selected_bytes(reopened) == selected_before
            assert _model_and_b_counts(reopened) == (0, 4)
            assert len(_assert_one_final_cut(reopened, slot.database_path)) == 1


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    raises=H1FinalPublicationUnavailable,
    reason="H1 selected external delivery/send recovery is not mounted yet",
)
async def test_final_h1_selected_delivery_preserves_prepared_bytes_without_send_success_claim(
    tmp_path: Path,
) -> None:
    """Final H1 selection is not itself a provider SEND or a success observation.

    No selected-V3 delivery continuation is mounted yet.  In particular,
    ``emit_committed`` is an R16-v2 route and cannot consume this H1 batch.
    This boundary witness keeps the selected prepared bytes available for that
    future one-shot/ambiguous-SEND test without inventing a public delivery API.
    """
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    resources = _resources(tmp_path, scenarios=("LOST_RESPONSE_AFTER_EFFECT",))
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=resources) as runtime:
            request, initial = await _seal_real_v3_then_v2(runtime)
            terminal = _require_final(await runtime.advance_execution(advance(initial, request)))
            assert terminal.phase == "TERMINAL"
            selected_before = _selected_bytes(runtime)
            assert len(selected_before) == 1
            provider = resources.require_original_provider()
            batch = _selected_h1(runtime)[0].prepared.request
            prepared_before = tuple(
                command.canonical_bytes for command in batch.prepared_effects_commands
            )
            assert prepared_before
            assert provider.transfers == ()
            assert all(
                b"CONFIRMED_SUCCESS" not in raw for raw in _terminal_runs(slot.database_path)
            )

        async with open_installed_h1_runtime(launch, resources=resources) as reopened:
            replay = _require_final(await reopened.advance_execution(advance(initial, request)))
            assert replay.disposition == "EXACT_REPLAY"
            assert _selected_bytes(reopened) == selected_before
            assert _assert_one_final_cut(reopened, slot.database_path)
            batch = _selected_h1(reopened)[0].prepared.request
            assert tuple(
                command.canonical_bytes for command in batch.prepared_effects_commands
            ) == (prepared_before)
            assert resources.require_original_provider().transfers == ()
            assert all(
                b"CONFIRMED_SUCCESS" not in raw for raw in _terminal_runs(slot.database_path)
            )
