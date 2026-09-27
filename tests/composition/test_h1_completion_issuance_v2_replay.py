"""RED replay witnesses for the separate installed H1 issuance-V2 finalizer.

Nothing in this module manufactures a batch, issuance, marker, ROOT or owner
decision.  Every observation begins at the installed H1 driver and reaches the
already implemented, durable four-stage recovery cut.  The sole current RED
seam is the documented but uninstalled ``finalize_execution`` operation.
"""

from __future__ import annotations

import hashlib
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
from chiplog.composition.common_cli_execution_runtime import (
    CommonCliExecutionRuntime,
    open_installed_h1_runtime,
)
from chiplog.composition.h1_completion_issuance import decode_h1_completion_issuance
from chiplog.composition.h1_completion_preparation_session import H1CompletionPreparationSession
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.r14_execution_completion_records import COMPLETE_ACCEPTANCE_OPERATION
from chiplog.composition.r14_loop_history import _completion_v2_schema_dispatch
from chiplog.composition.r14_runtime import AnchoredOwnerDecisionJournal
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform._owner_publication_contracts import CompleteDeliveryBatchV2
from chiplog.platform.owner_publications import SelectedOwnerDecision
from tests.support.h1_cli_execution import _admit, advance
from tests.support.h1_installed_launch import TENANT, installed_slot, prepare_installed_slot

V2_SCHEMA = "chiplog.composition.h1-completion-issuance.v2"


class H1CompletionIssuanceV2Unavailable(RuntimeError):
    """The frozen installed finalizer has not been installed yet."""


_RED = pytest.mark.xfail(
    strict=True,
    raises=H1CompletionIssuanceV2Unavailable,
    reason="separate installed H1 finalize_execution / issuance-V2 writer is absent",
)


class _CrashAfterDecided(RuntimeError):
    pass


class _CrashAfterSelectedReadback(RuntimeError):
    pass


def _resources(tmp_path: Path) -> HermeticDispatchResources:
    return HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )


async def _durable_recovery(
    runtime: CommonCliExecutionRuntime,
) -> tuple[Any, Any]:
    """Reach the real recovery cut before asking for the separate finalizer."""
    request = await _admit(runtime)
    initial = await runtime.drive_input(request)
    private = cast(Any, runtime)
    private._execution_model._responses = (
        DeliveryCompletion(
            tenant=TENANT,
            run_id=initial.stable_run_lineage_id,
            turn_id=initial.stable_run_lineage_id + "/turn/1",
            deliveries=(ProposedDelivery(payload=(Commentary(text="issuance-v2 replay"),)),),
        ).canonical_bytes(),
    )
    await runtime.advance_execution(advance(initial, request))
    states = private._h1_postseal_recovery_journal.scan().states_by_root
    assert len(states) == 1
    state = states[0][1]
    assert tuple(stage for stage, _raw, _command in state.inputs) == (
        "COMPLETION",
        "CONVERSATION",
        "EFFECTS",
        "TERMINAL_WORK",
    )
    assert tuple(stage for stage, _raw in state.results) == (
        "COMPLETION",
        "CONVERSATION",
        "EFFECTS",
        "TERMINAL_WORK",
    )
    return request, initial


def _selected_v2(runtime: CommonCliExecutionRuntime) -> tuple[SelectedOwnerDecision, ...]:
    return tuple(
        decision
        for decision in runtime._owner_decisions().snapshot().decisions
        if type(decision.prepared.request) is CompleteDeliveryBatchV2
        and decision.prepared.request.operation == COMPLETE_ACCEPTANCE_OPERATION
    )


async def _require_installed_v2_finalization(
    runtime: CommonCliExecutionRuntime, request: Any
) -> SelectedOwnerDecision:
    """Call precisely the frozen private operation; no fallback is permitted."""
    finalizer = getattr(runtime, "finalize_execution", None)
    if not callable(finalizer):
        raise H1CompletionIssuanceV2Unavailable("installed finalize_execution is absent")
    await finalizer(request.identity, request.original_driver_command_fingerprint())
    selected = _selected_v2(runtime)
    if len(selected) != 1:
        raise H1CompletionIssuanceV2Unavailable(
            "installed finalizer did not create one authenticated CompleteDeliveryBatchV2"
        )
    _assert_exact_v2_selection(runtime, selected[0])
    return selected[0]


def _selected_raw(runtime: CommonCliExecutionRuntime, decision: SelectedOwnerDecision) -> bytes:
    rows = tuple(
        raw
        for entry_id, _predecessor, raw in runtime._owner_decisions()._raw.entries()
        if entry_id == decision.decision_id
    )
    assert len(rows) == 1
    return rows[0]


def _assert_exact_v2_selection(
    runtime: CommonCliExecutionRuntime, decision: SelectedOwnerDecision
) -> None:
    """Authenticate the durable record, then inspect its V2 envelope bytes."""
    # ``snapshot`` has decoded and authenticated the whole owner journal; this
    # raw equality prevents a recovered object from becoming the witness.
    raw = _selected_raw(runtime, decision)
    assert hashlib.sha256(raw).hexdigest() == decision.decision_fingerprint
    batch = decision.prepared.request
    assert type(batch) is CompleteDeliveryBatchV2
    assert batch.operation == COMPLETE_ACCEPTANCE_OPERATION
    assert batch.authentication.applicability_schema == V2_SCHEMA
    assert hashlib.sha256(batch.authentication.applicability_bytes).hexdigest() == (
        batch.authentication.applicability_fingerprint
    )
    inner = json.loads(batch.authentication.applicability_bytes)
    assert inner["schema_id"] == V2_SCHEMA
    encoded_inner = json.dumps(
        inner, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    assert encoded_inner == (batch.authentication.applicability_bytes)


def _record_bytes(
    runtime: CommonCliExecutionRuntime, decision: SelectedOwnerDecision
) -> tuple[bytes, ...]:
    command = runtime._owner_command(decision)
    with sqlite3.connect(runtime._database) as connection:
        actual = tuple(
            row[0]
            for record in command.records
            for row in connection.execute(
                "SELECT canonical_bytes FROM records WHERE record_id = ?", (record.record_id,)
            ).fetchall()
        )
    assert actual == tuple(record.canonical_bytes for record in command.records)
    return actual


@pytest.mark.asyncio
@pytest.mark.parametrize("window", ("decided", "selected_before_readback"))
@_RED
async def test_v2_selected_or_pending_restart_reuses_authenticated_bytes_without_new_b_or_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, window: str
) -> None:
    """A restarted finalizer reconciles the original selection before opening B."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    resources = _resources(tmp_path)
    opened_b_sessions = 0
    original_bind = H1CompletionPreparationSession._bind_recovery

    def count_b_session(self: Any, **kwargs: Any) -> None:
        nonlocal opened_b_sessions
        opened_b_sessions += 1
        original_bind(self, **kwargs)

    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=resources) as runtime:
            request, _initial = await _durable_recovery(runtime)

        if window == "decided":
            original_select = AnchoredOwnerDecisionJournal.select

            def crash_after_select(self: Any, prepared: Any, commitment: str) -> Any:
                selected = original_select(self, prepared, commitment)
                if type(selected.prepared.request) is CompleteDeliveryBatchV2:
                    raise _CrashAfterDecided()
                return selected

            monkeypatch.setattr(AnchoredOwnerDecisionJournal, "select", crash_after_select)
            crash = _CrashAfterDecided
        else:
            original_append = IndependentTenantDecisionJournal.append

            def crash_after_append(self: Any, raw: bytes, predecessor: str | None) -> str:
                entry_id = original_append(self, raw, predecessor)
                entry = json.loads(raw)
                if (
                    entry.get("kind") == "SELECTED"
                    and entry.get("request", {}).get("kind") == "COMPLETE_DELIVERY_ATOMIC_V2"
                ):
                    raise _CrashAfterSelectedReadback()
                return entry_id

            monkeypatch.setattr(IndependentTenantDecisionJournal, "append", crash_after_append)
            crash = _CrashAfterSelectedReadback

        monkeypatch.setattr(H1CompletionPreparationSession, "_bind_recovery", count_b_session)
        async with open_installed_h1_runtime(launch, resources=resources) as crashed:
            with pytest.raises(crash):
                await _require_installed_v2_finalization(crashed, request)
            decision = _selected_v2(crashed)[0]
            _assert_exact_v2_selection(crashed, decision)
            selected_before = _selected_raw(crashed, decision)
            b_before = opened_b_sessions

        async with open_installed_h1_runtime(launch, resources=resources) as reopened:
            # The marker, fresh B frames and both fresh CURRENT calls belong to
            # a new issuance only.  An authenticated selected decision has none.
            monkeypatch.setattr(H1CompletionPreparationSession, "_bind_recovery", count_b_session)
            recovered = await _require_installed_v2_finalization(reopened, request)
            assert _selected_raw(reopened, recovered) == selected_before
            assert opened_b_sessions == b_before
            assert _record_bytes(reopened, recovered)


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ("missing", "unknown", "outer_inner_mismatch"))
@_RED
async def test_v2_dispatch_rejects_missing_unknown_or_mismatched_applicability_versions(
    tmp_path: Path, mutation: str
) -> None:
    """A real selected V2 batch may not silently enter the historical V1 route."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            request, _initial = await _durable_recovery(runtime)
            decision = await _require_installed_v2_finalization(runtime, request)
            batch = cast(CompleteDeliveryBatchV2, decision.prepared.request)
            auth = batch.authentication
            if mutation == "missing":
                altered = auth.model_copy(update={"applicability_schema": ""})
            elif mutation == "unknown":
                altered = auth.model_copy(update={"applicability_schema": "unknown.v2"})
            else:
                altered = auth.model_copy(
                    update={"applicability_schema": "chiplog.composition.h1-completion-issuance.v1"}
                )
            corrupted = batch.model_copy(update={"authentication": altered})
            if mutation == "outer_inner_mismatch":
                # A V1 outer tag must not coerce the real V2 body merely because
                # V1 and V2 retain the same stable completion command identity.
                with pytest.raises(ValueError):
                    decode_h1_completion_issuance(corrupted)
            else:
                with pytest.raises(ValueError):
                    _completion_v2_schema_dispatch(corrupted)
