"""Mounted evidence tests for target-scoped native selected accounting facts."""

from __future__ import annotations

import cProfile
import inspect
import io
import json
import pstats
import secrets
import sqlite3
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest

import chiplog.composition.r14_loop_history as loop_history
import chiplog.composition.r14_native_selected_accounting_source as selected_source
import chiplog.composition.r14_original_prepare_source as original_source
import chiplog.composition.r16_effects as effects
from chiplog.adapters.driven.loop_sqlite import LoopIntegrityError
from chiplog.architecture.r7_runtime import (
    R14_EXECUTION_NATIVE_SEALED_ACCOUNTING_V39_PRODUCTION_MANIFEST,
    R14_R17_H1_NATIVE_ONE_CALL_ACCOUNTING_V23_PRODUCTION_MANIFEST,
)
from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.execution_contracts import (
    ConsequentialToolCall,
    ExecutionContinue,
    ExecutionRunRecord,
    SelfEffectArguments,
)
from chiplog.capabilities.agent_loop.native_sealed_accounting_v3_contracts import (
    NATIVE_SEALED_ACCOUNTING_V3_REQUEST_SCHEMA,
    NATIVE_SEALED_ACCOUNTING_V3_RESULT_SCHEMA,
    PreparedNativeSealedAccountingV3,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.effects.dispatch_outcome_contracts import DispatchOutcomeRecordV2
from chiplog.composition.common_cli_execution_runtime import (
    CommonCliExecutionRuntime,
    open_common_cli_execution_runtime,
)
from chiplog.composition.common_execution_driver_contracts import SelectedExecutionReceiptV1
from chiplog.composition.h1_selected_prepare import select_h1_v3_prepare_for_native_fanout
from chiplog.composition.r14_call_acceptance_port import (
    AcceptedCallReceipt,
    CallAcceptanceAdoption,
    CallAcceptanceTarget,
)
from chiplog.composition.r14_cancellation_contracts import CancelCallSubmission
from chiplog.composition.r14_execution_complete_seal_records import (
    RetainedExecutionCompleteSealV3,
)
from chiplog.composition.r14_execution_fanout_records import reference
from chiplog.composition.r14_loop_history import (
    read_execution_history,
    read_execution_recovery_history,
    read_selected_native_sealed_accounting_v3,
)
from chiplog.composition.r14_native_preparation_bindings import (
    bind_native_execution_preparation_v2,
)
from chiplog.composition.r14_native_recovery_cut import NativeRecoveryCutSource
from chiplog.composition.r14_native_sealed_accounting_v3_publication import (
    publish_native_sealed_accounting_v3,
)
from chiplog.composition.r14_original_prepare_source import R14OriginalNativePrepareSource
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform._owner_publication_contracts import (
    JournalSelectedPublication,
)
from chiplog.platform.authority_reads import capture_authority_storage_state
from chiplog.platform.broker import (
    BrokerSession,
    CallBudget,
    PublicPortCall,
    PublicPortRejected,
    PublicPortSuccess,
)
from chiplog.platform.workspace_snapshot import read_connection
from tests.support.h1_cli_execution import admit_complete_script, admit_runtime_slot
from tests.support.native_cancelled import native_response


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


async def _close_accepted_member(
    runtime: CommonCliExecutionRuntime,
    original_call_id: str,
    accepted: AcceptedCallReceipt,
    run_id: str,
    *,
    expected_state: str = "CONFIRMED",
) -> object:
    assert accepted.original_call_id == original_call_id
    intent_id = accepted.external_intent.subject_id
    authorized = await runtime.authorize_dispatch(
        "hermetic-ingress", intent_id, "authorize-" + original_call_id, run_id
    )
    assert isinstance(authorized, JournalSelectedPublication), authorized
    sent = await runtime.commit_first_send(
        "hermetic-ingress", intent_id, "send-" + original_call_id, run_id
    )
    assert isinstance(sent, JournalSelectedPublication), sent
    await runtime.emit_committed("hermetic-ingress", intent_id)
    publication = await runtime.resolve_dispatch_outcome(
        "hermetic-ingress", intent_id, "close-" + original_call_id
    )
    outcome = DispatchOutcomeRecordV2.model_validate_json(
        publication.complete_records[0].canonical_bytes
    )
    assert outcome.snapshot.state == expected_state
    assert outcome.snapshot.obligation.state == "CLOSED"
    return await runtime.publish_accepted_direct_terminal(
        original_call_id=original_call_id
    )


async def _accept_native_member(
    runtime: CommonCliExecutionRuntime, call_label: str, index: int
) -> tuple[str, AcceptedCallReceipt, str]:
    _snapshot, inventory, _legacy, seals, _cancellations, _acceptances = (
        read_execution_recovery_history(runtime)
    )
    accepted_row = next(
        item
        for item in inventory.ordered_calls
        if item.initialized_record.call.original.model_call_label == call_label
    )
    sealed = seals[0].proposal.sealed_run
    preview = await runtime.preview_call_acceptance(
        "hermetic-ingress",
        CallAcceptanceTarget(
            original_call_id=accepted_row.original_call_id,
            initialized=accepted_row.initialized,
            current_run=CallSubjectHead(
                subject_id=sealed.run_id,
                revision=Present(head=sealed.head, fingerprint=sealed.digest()),
            ),
        ),
    )
    accepted = await runtime.accept_call(
        "hermetic-ingress",
        CallAcceptanceAdoption(
            act_id=f"accept-native-member-{index}",
            preview_bytes=preview.canonical_bytes(),
        ),
    )
    return accepted_row.original_call_id, accepted, sealed.run_id


@asynccontextmanager
async def _selected_v23_native_fanout(
    tmp_path: Path,
    *,
    tool_calls: tuple[ConsequentialToolCall, ...] | None = None,
    accepted_call_id: str | None = None,
    accepted_call_ids: tuple[str, ...] | None = None,
    cancelled_call_id: str | None = "effect",
    dispatch_cap: int = 1,
    dispatch_scenarios: tuple[str, ...] | None = None,
    manifest=R14_R17_H1_NATIVE_ONE_CALL_ACCOUNTING_V23_PRODUCTION_MANIFEST,
    native_one_call_context: bool = False,
    terminalize_accepted: bool = False,
    model_responses: tuple[bytes, ...] | None = None,
    recovery_cut_source=None,
    recovery_profile_version: str | None = None,
    phase_trace: list[tuple[str, int]] | None = None,
    phase_counts: list[tuple[str, int, int]] | None = None,
) -> AsyncIterator[
    tuple[CommonCliExecutionRuntime, CallSubjectHead, AcceptedCallReceipt | None, str]
]:
    database = tmp_path / "native-v23-prepare.sqlite"
    custody = tmp_path / "dispatch-custody"
    resources = HermeticDispatchResources(
        scenarios=dispatch_scenarios or ("CONFIRM",) * dispatch_cap,
        cap=dispatch_cap,
        custody_path=custody,
    )
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
    accepted: AcceptedCallReceipt | None = None

    async def timed_phase(name: str, operation):
        if phase_trace is None:
            return await operation
        started_ns = time.monotonic_ns()
        try:
            return await operation
        finally:
            phase_trace.append((name, time.monotonic_ns() - started_ns))

    def timed_sync_phase(name: str, operation):
        if phase_trace is None:
            return operation()
        started_ns = time.monotonic_ns()
        try:
            return operation()
        finally:
            phase_trace.append((name, time.monotonic_ns() - started_ns))

    runtime_start_ns = time.monotonic_ns()
    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        responses=(response,) if model_responses is None else model_responses,
        manifest=manifest,
        recovery_cut_source=recovery_cut_source,
    ) as runtime:
        if phase_trace is not None:
            phase_trace.append(("runtime startup", time.monotonic_ns() - runtime_start_ns))
        if recovery_profile_version is not None:
            await timed_phase(
                "publish recovery profile",
                runtime.publish_native_recovery_profile(version=recovery_profile_version),
            )
        request = await timed_phase(
            "admit initial slot", admit_runtime_slot(runtime, "native-v23")
        )
        initial = await timed_phase("drive initial input", runtime.drive_input(request))
        assert isinstance(initial, SelectedExecutionReceiptV1)
        started = await timed_phase(
            "begin first Run",
            runtime.begin_execution(
                "hermetic-ingress", initial.stable_run_lineage_id, initial.selected_run_head.head
            ),
        )
        captured = await timed_phase(
            "capture first Turn",
            runtime.capture_execution(
                "hermetic-ingress",
                initial.stable_run_lineage_id,
                started.head,
                native_one_call_context=native_one_call_context,
            ),
        )
        await timed_phase(
            "seal first Turn",
            runtime.seal_execution(
                "hermetic-ingress", initial.stable_run_lineage_id, captured.head
            ),
        )
        _snapshot, inventory, _legacy, seals, _cancellations, _acceptances = (
            timed_sync_phase(
                "first recovery history read", lambda: read_execution_recovery_history(runtime)
            )
        )
        if phase_counts is not None:
            phase_counts.append(
                ("first recovery view", len(inventory.ordered_calls), len(seals))
            )
        accepted_ids = accepted_call_ids or (
            () if accepted_call_id is None else (accepted_call_id,)
        )
        for index, accepted_label in enumerate(accepted_ids):
            _accepted_call_id, accepted, _run_id = await _accept_native_member(
                runtime, accepted_label, index
            )
        sealed = seals[0].proposal.sealed_run
        if cancelled_call_id is not None:
            row = next(
                (
                    item
                    for item in inventory.ordered_calls
                    if item.initialized_record.call.original.model_call_label
                    == cancelled_call_id
                ),
                None,
            )
            assert row is not None
            await timed_phase(
                "cancel first-Turn member",
                runtime.cancel_execution_call(
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
                ),
            )
        if terminalize_accepted:
            assert accepted_call_id is not None
            intent_id = accepted.external_intent.subject_id
            await runtime.authorize_dispatch(
                "hermetic-ingress", intent_id, "authorize-native-member", sealed.run_id
            )
            await runtime.commit_first_send(
                "hermetic-ingress", intent_id, "send-native-member", sealed.run_id
            )
            await runtime.emit_committed("hermetic-ingress", intent_id)
            outcome_publication = await runtime.resolve_dispatch_outcome(
                "hermetic-ingress", intent_id, "close-native-member"
            )
            outcome = DispatchOutcomeRecordV2.model_validate_json(
                outcome_publication.complete_records[0].canonical_bytes
            )
            assert outcome.snapshot.state == "CONFIRMED"
            assert outcome.snapshot.obligation.state == "CLOSED"
            await runtime.publish_accepted_direct_terminal()
        seal = seals[0].proposal.fan_out.response_seal
        yield cast(CommonCliExecutionRuntime, runtime), CallSubjectHead(
            subject_id=seal.response_seal_id,
            revision=Present(head="record:" + seal.digest(), fingerprint=seal.digest()),
        ), accepted, sealed.run_id


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
    ) as (runtime, seal, _accepted, _run_id):
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
async def test_v3_source_projects_mixed_terminal_members_in_seal_order(
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
        manifest=R14_EXECUTION_NATIVE_SEALED_ACCOUNTING_V39_PRODUCTION_MANIFEST,
        terminalize_accepted=True,
    ) as (runtime, seal, _accepted, _run_id):
        projection = R14OriginalNativePrepareSource(runtime).collect(seal)
        assert projection is not None
        source = selected_source.R14NativeSelectedAccountingSource(
            runtime, projection, bind_native_execution_preparation_v2(projection)
        )
        source_facts = source.collect(projection.prepared_run)
        assert source_facts is not None
        labels_by_id = {
            item.original_call_id: item.call.original.model_call_label
            for item in source_facts.selected_native_seals[0].initialized_records
        }
        request = source.prepare_sealed_accounting_v3(seal)
        assert request is not None
        assert tuple(
            labels_by_id[item.original_call_id]
            for item in request.complete_ordered_frontiers
        ) == ("first-effect", "second-effect")
        assert source.prepare_sealed_accounting_v3(
            CallSubjectHead(
                subject_id="unselected-response",
                revision=Present(head="record:" + "0" * 64, fingerprint="0" * 64),
            )
        ) is None
        engine = runtime._supervisor.runtime()
        callee = engine.session("agent_loop")
        caller = BrokerSession(
            tenant_id=runtime._tenant_id,
            broker_epoch=callee.broker_epoch,
            generation_id=callee.generation_id,
            owner_id="broker",
            session_id=f"broker:{callee.generation_id}",
        )
        owner_call = PublicPortCall(
            operation_id="agent_loop.prepare_native_sealed_accounting.v3",
            request_id="native-sealed-accounting-test:" + secrets.token_hex(8),
            caller=caller,
            callee=callee,
            schema_id=NATIVE_SEALED_ACCOUNTING_V3_REQUEST_SCHEMA,
            canonical_payload=request.canonical_bytes(),
            budget=CallBudget(
                remaining_calls=1,
                remaining_depth=1,
                policy_version=1,
                absolute_deadline_ns=time.monotonic_ns() + 5_000_000_000,
            ),
        )
        result = await engine.call(owner_call)
        assert isinstance(result, PublicPortSuccess), (
            result.failure.reason if isinstance(result, PublicPortRejected) else result
        )
        assert result.request_id == owner_call.request_id
        assert result.responder == callee
        assert result.schema_id == NATIVE_SEALED_ACCOUNTING_V3_RESULT_SCHEMA
        prepared = PreparedNativeSealedAccountingV3.model_validate_json(
            result.canonical_payload
        )
        assert prepared.selected_response == request.selected_response
        assert prepared.sealed_manifest_fingerprint == request.sealed_manifest_fingerprint
        assert prepared.complete_ordered_calls == request.complete_ordered_frontiers


@pytest.mark.asyncio
async def test_v3_source_holds_when_one_member_is_not_terminal(
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
        manifest=R14_EXECUTION_NATIVE_SEALED_ACCOUNTING_V39_PRODUCTION_MANIFEST,
    ) as (runtime, seal, _accepted, _run_id):
        projection = R14OriginalNativePrepareSource(runtime).collect(seal)
        assert projection is not None
        source = selected_source.R14NativeSelectedAccountingSource(
            runtime, projection, bind_native_execution_preparation_v2(projection)
        )
        assert source.prepare_sealed_accounting_v3(seal) is None


@pytest.mark.asyncio
async def test_v3_mixed_seal_publishes_reads_after_reopen_and_replays_once(
    tmp_path: Path, monkeypatch
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
    database = tmp_path / "native-v23-prepare.sqlite"
    custody = tmp_path / "dispatch-custody"
    async with _selected_v23_native_fanout(
        tmp_path,
        tool_calls=calls,
        accepted_call_id="first-effect",
        cancelled_call_id="second-effect",
        manifest=R14_EXECUTION_NATIVE_SEALED_ACCOUNTING_V39_PRODUCTION_MANIFEST,
        terminalize_accepted=True,
    ) as (runtime, seal, _accepted, _run_id):
        record = await publish_native_sealed_accounting_v3(runtime, seal)
        assert record.selected_response_fingerprint == seal.revision.fingerprint
        assert record.ordered_sealed_call_ids == tuple(
            item.original_call_id for item in record.result.complete_ordered_calls
        )
        assert tuple(
            item.disposition.kind for item in record.result.complete_ordered_calls
        ) == ("SUCCEEDED", "CANCELLED_BEFORE_ACCEPT")
        assert read_selected_native_sealed_accounting_v3(
            runtime, seal.revision.fingerprint
        ) == record

    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=custody
    )
    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        manifest=R14_EXECUTION_NATIVE_SEALED_ACCOUNTING_V39_PRODUCTION_MANIFEST,
    ) as reopened:
        original_reader = loop_history._read_call_history
        reader_depth = 0

        def reject_nested_history_read(*args, **kwargs):
            nonlocal reader_depth
            if reader_depth:
                raise AssertionError("V3 validation recursively reopened execution history")
            reader_depth += 1
            try:
                return original_reader(*args, **kwargs)
            finally:
                reader_depth -= 1

        monkeypatch.setattr(loop_history, "_read_call_history", reject_nested_history_read)
        assert read_selected_native_sealed_accounting_v3(
            reopened, seal.revision.fingerprint
        ) == record
        current_history = loop_history.read_execution_recovery_history(reopened)[0]
        with read_connection(reopened._database) as connection:
            frontier = connection.execute(
                "SELECT head FROM tenant_heads WHERE tenant_id=?", (reopened._tenant_id,)
            ).fetchone()[0]
            with pytest.raises(
                ValueError, match="execution selected history and effects physical cut differ"
            ):
                effects._reconstruct_registered_runs(
                    reopened,
                    connection,
                    reopened._tenant_id,
                    reopened._owner_decisions().snapshot(),
                    frontier,
                    authenticated_execution_history=current_history.model_copy(
                        update={
                            "records": tuple(
                                item
                                for item in current_history.records
                                if not isinstance(item, ExecutionRunRecord)
                            )
                        }
                    ),
                )
        before = len(reopened._loop_decisions().entries())
        assert await publish_native_sealed_accounting_v3(reopened, seal) == record
        assert len(reopened._loop_decisions().entries()) == before
        before_head = read_execution_history(reopened).tenant_head
        before_decisions = reopened._loop_decisions().entries()
        with sqlite3.connect(database) as connection:
            selected_row = connection.execute(
                "SELECT owner, schema_id, canonical_bytes, commit_sequence FROM records "
                "WHERE tenant_id=? AND record_id=?",
                (reopened._tenant_id, record.record_id),
            ).fetchone()
            assert selected_row is not None
            connection.execute(
                "INSERT INTO records(tenant_id, record_id, owner, schema_id, canonical_bytes, "
                "commit_sequence) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    reopened._tenant_id,
                    "orphan:" + record.record_id,
                    *selected_row[:3],
                    0,
                ),
            )
        reopened._commitment_journal.commit(
            reopened._tenant_id, capture_authority_storage_state(database)[0]
        )
        # Isolate the full reader check from startup's earlier selected-only preflight.
        with pytest.raises(LoopIntegrityError) as rejected:
            read_selected_native_sealed_accounting_v3(
                reopened, seal.revision.fingerprint
            )
        assert "orphaned, missing or unknown loop publication members" in str(
            rejected.value.__cause__
        )
        assert reopened._loop_decisions().entries() == before_decisions
        with read_connection(reopened._database) as connection:
            assert connection.execute(
                "SELECT head FROM tenant_heads WHERE tenant_id=?", (reopened._tenant_id,)
            ).fetchone() == (before_head,)
        with read_connection(reopened._database) as connection:
            count = connection.execute(
                "SELECT count(*) FROM publications WHERE tenant_id=? "
                "AND operation_kind='agent_loop.native-sealed-accounting.v3'",
                (reopened._tenant_id,),
            ).fetchone()
        assert count == (1,)


@pytest.mark.asyncio
async def test_collects_and_reopens_mounted_v3_target_facts(tmp_path: Path) -> None:
    async with _selected_v23_native_fanout(tmp_path) as (runtime, seal, _accepted, _run_id):
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
    async with _selected_v23_native_fanout(tmp_path) as (runtime, seal, _accepted, _run_id):
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
    async with _selected_v23_native_fanout(tmp_path) as (runtime, seal, _accepted, _run_id):
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


@pytest.mark.asyncio
async def test_v3_interleaves_two_sequentially_accepted_members_across_replay_and_reopen(
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
    database = tmp_path / "native-v23-prepare.sqlite"
    custody = tmp_path / "dispatch-custody"

    def tenant_head(runtime: CommonCliExecutionRuntime) -> int:
        with read_connection(runtime._database) as connection:
            row = connection.execute(
                "SELECT head FROM tenant_heads WHERE tenant_id=?", (runtime._tenant_id,)
            ).fetchone()
        assert row is not None
        return int(row[0])

    async with _selected_v23_native_fanout(
        tmp_path,
        tool_calls=calls,
        accepted_call_ids=("first-effect",),
        cancelled_call_id=None,
        dispatch_cap=2,
        dispatch_scenarios=("CONFIRM", "PERMANENT_NO_EFFECT"),
        manifest=R14_EXECUTION_NATIVE_SEALED_ACCOUNTING_V39_PRODUCTION_MANIFEST,
    ) as (runtime, seal, first_accepted, run_id):
        assert first_accepted is not None
        first_call = first_accepted.original_call_id
        first_terminal = await _close_accepted_member(runtime, first_call, first_accepted, run_id)
        assert (
            runtime.read_selected_accepted_direct_terminal(original_call_id=first_call)
            == first_terminal
        )
        head_after_first = tenant_head(runtime)
        assert (
            await runtime.publish_accepted_direct_terminal(original_call_id=first_call)
            == first_terminal
        )
        assert tenant_head(runtime) == head_after_first

        with pytest.raises(LoopRejected):
            await publish_native_sealed_accounting_v3(runtime, seal)
        with read_connection(runtime._database) as connection:
            assert connection.execute(
                "SELECT count(*) FROM publications WHERE tenant_id=? "
                "AND operation_kind='agent_loop.native-sealed-accounting.v3'",
                (runtime._tenant_id,),
            ).fetchone() == (0,)
        assert tenant_head(runtime) == head_after_first

        second_call, second_accepted, second_run_id = await _accept_native_member(
            runtime, "second-effect", 1
        )
        assert second_call != first_call
        second_terminal = await _close_accepted_member(
            runtime,
            second_call,
            second_accepted,
            second_run_id,
            expected_state="FAILED_NO_EFFECT",
        )
        published = await publish_native_sealed_accounting_v3(runtime, seal)
        assert tuple(item.original_call_id for item in published.result.complete_ordered_calls) == (
            first_call,
            second_call,
        )
        assert tuple(item.disposition.kind for item in published.result.complete_ordered_calls) == (
            "SUCCEEDED",
            "FAILED_DEFINITE",
        )

        head_after_publication = tenant_head(runtime)
        assert (
            await runtime.publish_accepted_direct_terminal(original_call_id=first_call)
            == first_terminal
        )
        assert (
            await runtime.publish_accepted_direct_terminal(original_call_id=second_call)
            == second_terminal
        )
        assert await publish_native_sealed_accounting_v3(runtime, seal) == published
        assert tenant_head(runtime) == head_after_publication

    resources = HermeticDispatchResources(
        scenarios=("CONFIRM", "PERMANENT_NO_EFFECT"), cap=2, custody_path=custody
    )
    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        manifest=R14_EXECUTION_NATIVE_SEALED_ACCOUNTING_V39_PRODUCTION_MANIFEST,
    ) as reopened:
        reopened_head = tenant_head(reopened)
        assert (
            reopened.read_selected_accepted_direct_terminal(original_call_id=first_call)
            == first_terminal
        )
        assert (
            reopened.read_selected_accepted_direct_terminal(original_call_id=second_call)
            == second_terminal
        )
        assert (
            read_selected_native_sealed_accounting_v3(reopened, seal.revision.fingerprint)
            == published
        )
        assert (
            await reopened.publish_accepted_direct_terminal(original_call_id=first_call)
            == first_terminal
        )
        assert (
            await reopened.publish_accepted_direct_terminal(original_call_id=second_call)
            == second_terminal
        )
        assert await publish_native_sealed_accounting_v3(reopened, seal) == published
        assert tenant_head(reopened) == reopened_head
@pytest.mark.asyncio
async def test_v3_reads_three_accepted_members_across_delayed_dispatch_and_replay(
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
        ConsequentialToolCall(
            call_id="third-effect",
            tool="request_self_effect",
            arguments=SelfEffectArguments(payload=b"third", bundle_members=("third",)),
        ),
    )
    first_call = second_call = None
    database = tmp_path / "native-v23-prepare.sqlite"
    custody = tmp_path / "dispatch-custody"
    async with _selected_v23_native_fanout(
        tmp_path,
        tool_calls=calls,
        accepted_call_ids=("first-effect",),
        cancelled_call_id=None,
        dispatch_cap=3,
        dispatch_scenarios=("CONFIRM", "PERMANENT_NO_EFFECT", "CONFIRM"),
        manifest=R14_EXECUTION_NATIVE_SEALED_ACCOUNTING_V39_PRODUCTION_MANIFEST,
    ) as (runtime, seal, first_accepted, run_id):
        assert first_accepted is not None
        first_call = first_accepted.original_call_id
        first_terminal = await _close_accepted_member(
            runtime, first_call, first_accepted, run_id
        )
        second_call, second_accepted, second_run_id = await _accept_native_member(
            runtime, "second-effect", 1
        )
        second_terminal = await _close_accepted_member(
            runtime,
            second_call,
            second_accepted,
            second_run_id,
            expected_state="FAILED_NO_EFFECT",
        )
        third_call, third_accepted, third_run_id = await _accept_native_member(
            runtime, "third-effect", 2
        )
        third_terminal = await _close_accepted_member(
            runtime, third_call, third_accepted, third_run_id
        )
        published = await publish_native_sealed_accounting_v3(runtime, seal)
        assert tuple(
            item.original_call_id for item in published.result.complete_ordered_calls
        ) == (first_call, second_call, third_call)
        assert tuple(
            item.disposition.kind for item in published.result.complete_ordered_calls
        ) == ("SUCCEEDED", "FAILED_DEFINITE", "SUCCEEDED")

        with read_connection(runtime._database) as connection:
            head = connection.execute(
                "SELECT head FROM tenant_heads WHERE tenant_id=?", (runtime._tenant_id,)
            ).fetchone()[0]
        replayed_first = await runtime.publish_accepted_direct_terminal(
            original_call_id=first_call
        )
        replayed_second = await runtime.publish_accepted_direct_terminal(
            original_call_id=second_call
        )
        replayed_third = await runtime.publish_accepted_direct_terminal(
            original_call_id=third_call
        )
        replayed_v3 = await publish_native_sealed_accounting_v3(runtime, seal)
        with read_connection(runtime._database) as connection:
            assert connection.execute(
                "SELECT head FROM tenant_heads WHERE tenant_id=?", (runtime._tenant_id,)
            ).fetchone()[0] == head
        assert replayed_first == first_terminal
        assert replayed_second == second_terminal
        assert replayed_third == third_terminal
        assert replayed_v3 == published

        before_entries = runtime._loop_decisions().entries()
        before_owner = runtime._owner_decisions().snapshot()
        with pytest.raises(LoopRejected):
            await runtime.publish_accepted_direct_terminal(
                original_call_id="unselected-call"
            )
        assert runtime._loop_decisions().entries() == before_entries
        assert runtime._owner_decisions().snapshot() == before_owner
        with read_connection(runtime._database) as connection:
            assert connection.execute(
                "SELECT head FROM tenant_heads WHERE tenant_id=?", (runtime._tenant_id,)
            ).fetchone()[0] == head

    resources = HermeticDispatchResources(
        scenarios=("CONFIRM", "PERMANENT_NO_EFFECT", "CONFIRM"),
        cap=3,
        custody_path=custody,
    )
    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        manifest=R14_EXECUTION_NATIVE_SEALED_ACCOUNTING_V39_PRODUCTION_MANIFEST,
    ) as reopened:
        assert reopened.read_selected_accepted_direct_terminal(
            original_call_id=first_call
        ) == first_terminal
        assert reopened.read_selected_accepted_direct_terminal(
            original_call_id=second_call
        ) == second_terminal
        assert reopened.read_selected_accepted_direct_terminal(
            original_call_id=third_call
        ) == third_terminal


@pytest.mark.asyncio
async def test_v3_accounts_two_sequential_turn_seals_after_reopen_and_cross_order_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "native-v23-prepare.sqlite"
    custody = tmp_path / "dispatch-custody"
    followup = ExecutionContinue(
        kind="Continue",
        tool_calls=(
            ConsequentialToolCall(
                call_id="followup-effect",
                tool="request_self_effect",
                arguments=SelfEffectArguments(
                    payload=b"followup", bundle_members=("followup",)
                ),
            ),
        ),
    ).canonical_bytes()
    setup_phases: list[tuple[str, int]] = []
    setup_counts: list[tuple[str, int, int]] = []

    async def timed_setup(name: str, operation):
        started_ns = time.monotonic_ns()
        try:
            return await operation
        finally:
            setup_phases.append((name, time.monotonic_ns() - started_ns))

    def timed_setup_sync(name: str, operation):
        started_ns = time.monotonic_ns()
        try:
            return operation()
        finally:
            setup_phases.append((name, time.monotonic_ns() - started_ns))

    async with _selected_v23_native_fanout(
        tmp_path,
        cancelled_call_id="effect",
        dispatch_cap=1,
        manifest=R14_EXECUTION_NATIVE_SEALED_ACCOUNTING_V39_PRODUCTION_MANIFEST,
        native_one_call_context=True,
        model_responses=(native_response(), followup),
        recovery_cut_source=NativeRecoveryCutSource(),
        recovery_profile_version="v3",
        phase_trace=setup_phases,
        phase_counts=setup_counts,
    ) as (runtime, first_seal, _accepted, run_id):
        await timed_setup(
            "publish cancellation accounting",
            runtime.publish_native_one_call_cancelled_accounting(),
        )
        await timed_setup(
            "publish first-seal V3", publish_native_sealed_accounting_v3(runtime, first_seal)
        )

        await timed_setup(
            "publish accounting readiness", runtime.publish_execution_accounting_readiness()
        )
        first_run = timed_setup_sync(
            "read first execution history", lambda: read_execution_history(runtime).records[-1]
        )
        next_run = await timed_setup(
            "start second Turn", runtime.start_next_execution_turn(run_id, first_run.head)
        )
        captured = await timed_setup(
            "capture second Turn",
            runtime.capture_execution("hermetic-ingress", next_run.run_id, next_run.head),
        )
        second_run = await timed_setup(
            "seal second Turn",
            runtime.seal_execution("hermetic-ingress", captured.run_id, captured.head),
        )
        second_seal = second_run.turns[-1].response_seal
        assert second_seal is not None
        assert first_seal != second_seal
        second_history = timed_setup_sync(
            "second recovery history read",
            lambda: read_execution_recovery_history(runtime),
        )
        setup_counts.append(
            (
                "second recovery view",
                len(second_history[1].ordered_calls),
                len(second_history[3]),
            )
        )
        second_call = next(
            item
            for item in second_history[1].ordered_calls
            if item.initialized_record.call.original.model_call_label == "followup-effect"
        )
        print(
            "R14_SETUP_TRACE",
            {"phase_wall_ns": tuple(setup_phases), "recovery_views": tuple(setup_counts)},
            flush=True,
        )
        second_run_head = CallSubjectHead(
            subject_id=second_run.run_id,
            revision=Present(head=second_run.head, fingerprint=second_run.digest()),
        )
        from chiplog.composition import (
            r14_call_authority,
            r14_call_preparation,
            r14_call_preview,
            r14_call_publication,
        )

        # Spans are nested; async thread CPU can include other event-loop work.
        trace_origin_ns = time.monotonic_ns()
        spans: list[tuple[str, int, int, int, int]] = []
        writer_refusal: list[str] = []
        writer_guard_ages: list[tuple[int, int, str | None]] = []
        writer_deadline_details: list[dict[str, object]] = []
        writer_history_profile: list[str] = []

        def timed(name, function):
            if inspect.iscoroutinefunction(function):

                async def wrapped(*args, **kwargs):
                    start = time.monotonic_ns()
                    cpu_start = time.thread_time_ns()
                    try:
                        return await function(*args, **kwargs)
                    finally:
                        end = time.monotonic_ns()
                        spans.append(
                            (
                                name,
                                start - trace_origin_ns,
                                end - trace_origin_ns,
                                end - start,
                                time.thread_time_ns() - cpu_start,
                            )
                        )

                return wrapped

            def wrapped(*args, **kwargs):
                start = time.monotonic_ns()
                cpu_start = time.thread_time_ns()
                try:
                    return function(*args, **kwargs)
                finally:
                    end = time.monotonic_ns()
                    spans.append(
                        (
                            name,
                            start - trace_origin_ns,
                            end - trace_origin_ns,
                            end - start,
                            time.thread_time_ns() - cpu_start,
                        )
                    )

            return wrapped

        target = CallAcceptanceTarget(
            original_call_id=second_call.original_call_id,
            initialized=second_call.initialized,
            current_run=second_run_head,
        )
        with monkeypatch.context() as preview_probe:
            preview_probe.setattr(
                r14_call_preview,
                "read_execution_call_history",
                timed("preview.history", r14_call_preview.read_execution_call_history),
            )
            preview_probe.setattr(
                r14_call_preview,
                "_read_call_previews_and_history",
                timed(
                    "preview.preview_history",
                    r14_call_preview._read_call_previews_and_history,
                ),
            )
            preview_probe.setattr(
                r14_call_preview,
                "authenticate",
                timed("preview.authenticate", r14_call_preview.authenticate),
            )
            preview_probe.setattr(
                r14_call_preview,
                "capture_dispatch",
                timed("preview.capture", r14_call_preview.capture_dispatch),
            )
            preview_probe.setattr(
                r14_call_preview,
                "owner_call",
                timed("preview.owner_call", r14_call_preview.owner_call),
            )
            preview_start_ns = time.monotonic_ns()
            preview_cpu_start = time.thread_time_ns()
            preview = await runtime.preview_call_acceptance("hermetic-ingress", target)
            preview_end_ns = time.monotonic_ns()
        preview_cpu_ns = time.thread_time_ns() - preview_cpu_start
        mandate = json.loads(preview.mandate_bytes)["horizon"]
        mandate_not_before_ns = mandate["not_before_ns"]
        mandate_expires_at_ns = mandate["expires_at_ns"]
        preview_mandate_age_ns = time.monotonic_ns() - mandate_not_before_ns

        adoption = CallAcceptanceAdoption(
            act_id="accept-followup-seal-member",
            preview_bytes=preview.canonical_bytes(),
        )
        accept_start_ns = time.monotonic_ns()
        accept_cpu_start = time.thread_time_ns()

        def report_accept_trace(failure: str) -> None:
            now_ns = time.monotonic_ns()
            print(
                "R14_ACCEPT_TRACE",
                {
                    "failure": failure,
                    "preview_start_offset_ns": preview_start_ns - trace_origin_ns,
                    "preview_end_offset_ns": preview_end_ns - trace_origin_ns,
                    "preview_wall_ns": preview_end_ns - preview_start_ns,
                    "preview_thread_cpu_ns": preview_cpu_ns,
                    "preview_mandate_age_ns": preview_mandate_age_ns,
                    "accept_start_offset_ns": accept_start_ns - trace_origin_ns,
                    "accept_end_offset_ns": now_ns - trace_origin_ns,
                    "accept_wall_ns": now_ns - accept_start_ns,
                    "accept_thread_cpu_ns": time.thread_time_ns() - accept_cpu_start,
                    "mandate_age_at_return_ns": now_ns - mandate_not_before_ns,
                    "mandate_remaining_at_return_ns": mandate_expires_at_ns - now_ns,
                    "writer_refusal": writer_refusal[-1] if writer_refusal else None,
                    "writer_guard_mandate_ages_ns": tuple(writer_guard_ages),
                    "writer_deadline_details": tuple(writer_deadline_details),
                    "writer_history_profile": tuple(writer_history_profile),
                    "spans_start_end_wall_thread_cpu_ns": tuple(spans),
                    "setup_phase_wall_ns": tuple(setup_phases),
                },
                flush=True,
            )

        with monkeypatch.context() as probe:
            probe.setattr(
                r14_call_authority.CallPublicationAuthority,
                "prepare_fresh",
                timed(
                    "accept.authority_prepare_fresh",
                    r14_call_authority.CallPublicationAuthority.prepare_fresh,
                ),
            )
            probe.setattr(
                r14_call_authority,
                "_prepare_call_exchange_with_history",
                timed(
                    "accept.prepare_call_exchange",
                    r14_call_authority._prepare_call_exchange_with_history,
                ),
            )
            probe.setattr(
                r14_call_preparation,
                "authenticate",
                timed("accept.prepare_authenticate", r14_call_preparation.authenticate),
            )
            probe.setattr(
                r14_call_preparation,
                "owner_call",
                timed("accept.effects_owner_call", r14_call_preparation.owner_call),
            )
            probe.setattr(
                r14_call_preparation,
                "_read_call_previews_and_history",
                timed(
                    "accept.preview_history",
                    r14_call_preparation._read_call_previews_and_history,
                ),
            )
            probe.setattr(
                r14_call_preparation,
                "capture_dispatch",
                timed("accept.prepare_capture", r14_call_preparation.capture_dispatch),
            )
            probe.setattr(
                r14_call_publication,
                "authenticate",
                timed("accept.replay_authenticate", r14_call_publication.authenticate),
            )
            original_check_live = r14_call_authority.CallPublicationAuthority._check_live

            def checked_live(authority, value):
                not_before_ns = value.preview.request.mandate.horizon.not_before_ns
                start_age_ns = time.monotonic_ns() - not_before_ns
                refusal = None
                try:
                    return original_check_live(authority, value)
                except LoopRejected as error:
                    refusal = str(error)
                    writer_refusal.append(str(error))
                    frame = error.__traceback__
                    check_locals = None
                    while frame is not None:
                        if frame.tb_frame.f_code.co_name == "_check_live":
                            check_locals = frame.tb_frame.f_locals
                            break
                        frame = frame.tb_next
                    now_ns = time.monotonic_ns()
                    fresh = check_locals.get("fresh") if check_locals is not None else None
                    cut = check_locals.get("cut") if check_locals is not None else None
                    sources = getattr(cut, "sources", ())
                    candidates = (
                        ("loop_rpc", value.loop_sent.budget.absolute_deadline_ns),
                        ("effects_rpc", value.effects_sent.budget.absolute_deadline_ns),
                        (
                            "effects_lease",
                            value.retained.effects_request.current.lease_expires_at_ns,
                        ),
                        ("mandate", value.preview.request.mandate.horizon.expires_at_ns),
                        *(
                            (f"source_{index}", source.valid_until_ns)
                            for index, source in enumerate(sources)
                        ),
                    )
                    earliest = min((deadline for _, deadline in candidates), default=None)
                    writer_deadline_details.append(
                        {
                            "reason": refusal,
                            "observed_at_ns": getattr(fresh, "observed_time_ns", None),
                            "check_deadline_ns": (
                                check_locals.get("deadline") if check_locals is not None else None
                            ),
                            "now_ns": now_ns,
                            "candidate_deadlines_ns": candidates,
                            "earliest_names": tuple(
                                name for name, deadline in candidates if deadline == earliest
                            ),
                            "earliest_slack_at_exception_ns": (
                                None if earliest is None else earliest - now_ns
                            ),
                            "earliest_slack_at_observation_ns": (
                                None
                                if earliest is None or fresh is None
                                else earliest - fresh.observed_time_ns
                            ),
                        }
                    )
                    raise
                finally:
                    writer_guard_ages.append(
                        (start_age_ns, time.monotonic_ns() - not_before_ns, refusal)
                    )

            probe.setattr(
                r14_call_authority.CallPublicationAuthority,
                "_check_live",
                timed("writer.check_live", checked_live),
            )
            history_profile = cProfile.Profile(time.thread_time_ns, 1e-9)
            original_writer_history = r14_call_authority.read_execution_call_history

            def profiled_writer_history(*args, **kwargs):
                wall_start_ns = time.monotonic_ns()
                thread_start_ns = time.thread_time_ns()
                history_profile.enable()
                try:
                    return original_writer_history(*args, **kwargs)
                finally:
                    history_profile.disable()
                    writer_history_profile.append(
                        f"wall_ns={time.monotonic_ns() - wall_start_ns} "
                        f"thread_cpu_ns={time.thread_time_ns() - thread_start_ns}"
                    )
                    for sort_key in ("cumtime", "tottime"):
                        stream = io.StringIO()
                        pstats.Stats(history_profile, stream=stream).strip_dirs().sort_stats(
                            sort_key
                        ).print_stats(15)
                        writer_history_profile.append(f"{sort_key}:\n{stream.getvalue()}")

            probe.setattr(
                r14_call_authority,
                "read_execution_call_history",
                profiled_writer_history,
            )
            probe.setattr(
                r14_call_authority,
                "capture_dispatch",
                timed("writer.capture", r14_call_authority.capture_dispatch),
            )
            original_submit = runtime._appender.submit
            probe.setattr(
                runtime._appender,
                "submit",
                timed("writer.submit", original_submit),
            )
            try:
                accepted = await runtime.accept_call("hermetic-ingress", adoption)
            except Exception as error:
                error_label = (
                    "writer-stale" if "writer STALE" in str(error) else type(error).__name__
                )
                report_accept_trace(error_label)
                raise
            else:
                report_accept_trace("selected")
        second_terminal = await _close_accepted_member(
            runtime,
            second_call.original_call_id,
            accepted,
            second_run.run_id,
        )

        projection = R14OriginalNativePrepareSource(runtime).collect(second_seal)
        assert projection is not None
        source = selected_source.R14NativeSelectedAccountingSource(
            runtime, projection, bind_native_execution_preparation_v2(projection)
        )
        facts = source.collect(projection.prepared_run)
        assert facts is not None
        first_facts = tuple(
            item for item in facts.selected_native_seals if item.response_seal == first_seal
        )
        assert len(first_facts) == 1
        assert (
            source._accepted_member_facts(
                facts, first_facts[0], second_call.original_call_id
            )
            is None
        )

        second_v3 = await publish_native_sealed_accounting_v3(runtime, second_seal)
        first_v3 = await publish_native_sealed_accounting_v3(runtime, first_seal)
        before_replay = read_execution_history(runtime).tenant_head
        assert second_v3.selected_response_fingerprint == second_seal.revision.fingerprint
        assert first_v3.selected_response_fingerprint == first_seal.revision.fingerprint
        assert read_selected_native_sealed_accounting_v3(
            runtime, second_seal.revision.fingerprint
        ) == second_v3
        assert read_selected_native_sealed_accounting_v3(
            runtime, first_seal.revision.fingerprint
        ) == first_v3
        assert await publish_native_sealed_accounting_v3(runtime, first_seal) == first_v3
        assert await publish_native_sealed_accounting_v3(runtime, second_seal) == second_v3
        assert read_execution_history(runtime).tenant_head == before_replay

    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=custody
    )
    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        manifest=R14_EXECUTION_NATIVE_SEALED_ACCOUNTING_V39_PRODUCTION_MANIFEST,
        recovery_cut_source=NativeRecoveryCutSource(),
    ) as reopened:
        reopened_head = read_execution_history(reopened).tenant_head
        assert read_selected_native_sealed_accounting_v3(
            reopened, first_seal.revision.fingerprint
        ) == first_v3
        assert read_selected_native_sealed_accounting_v3(
            reopened, second_seal.revision.fingerprint
        ) == second_v3
        assert await publish_native_sealed_accounting_v3(reopened, second_seal) == second_v3
        assert await publish_native_sealed_accounting_v3(reopened, first_seal) == first_v3
        assert read_execution_history(reopened).tenant_head == reopened_head
        assert reopened.read_selected_accepted_direct_terminal(
            original_call_id=second_call.original_call_id
        ) == second_terminal


@pytest.mark.asyncio
@pytest.mark.parametrize("observer_raises", [False, True])
async def test_readiness_submit_observer_restores_real_workspace_dispatch(
    tmp_path: Path, observer_raises: bool,
) -> None:
    from chiplog.capabilities.projections.r9_boundary import WorkspaceRejected
    from tests.support.workspace_batch import workspace

    async with workspace(tmp_path) as (workspace_runtime, _):
        appender = workspace_runtime._appender
        assert "submit" not in vars(appender)
        original_submit = appender.submit

        async def observe_submit(batch):
            return await original_submit(batch)

        workspace_runtime._check_dispatch()
        try:
            with patch.object(appender, "submit", observe_submit):
                if observer_raises:
                    raise RuntimeError("observer scope interrupted")
        except RuntimeError as error:
            assert observer_raises and str(error) == "observer scope interrupted"
        # Same real guard as the second native Turn's workspace read.
        workspace_runtime._check_dispatch()
        assert "submit" not in vars(appender)
        # The same bound function is still a forbidden instance override while
        # installed; cleanup must preserve ownership, not relax the guard.
        with (
            patch.object(appender, "submit", original_submit),
            pytest.raises(WorkspaceRejected, match="unregistered workspace instance dispatch"),
        ):
            workspace_runtime._check_dispatch()
        assert "submit" not in vars(appender)


@pytest.mark.asyncio
async def test_accepted_open_recovery_reopens_across_seals_and_rejects_each_foreign_locator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from chiplog.adapters.driven.effects_hermetic import HermeticResponseLost
    from chiplog.composition.r14_accepted_open_accounting_records import (
        ACCEPTED_OPEN_RECOVERY_OPERATION,
        retained_accepted_open_recovery,
    )
    from chiplog.platform._sqlite import LostCommitAcknowledgement

    first_response = ExecutionContinue(
        kind="Continue",
        tool_calls=(
            ConsequentialToolCall(
                call_id="first-open-effect",
                tool="request_self_effect",
                arguments=SelfEffectArguments(payload=b"first-open", bundle_members=("open",)),
            ),
        ),
    ).canonical_bytes()
    second_response = ExecutionContinue(
        kind="Continue",
        tool_calls=(
            ConsequentialToolCall(
                call_id="second-open-effect",
                tool="request_self_effect",
                arguments=SelfEffectArguments(payload=b"second-open", bundle_members=("open",)),
            ),
        ),
    ).canonical_bytes()
    scenarios = ("LOST_RESPONSE_AFTER_EFFECT", "LOST_RESPONSE_AFTER_EFFECT")
    database = tmp_path / "native-v23-prepare.sqlite"
    custody = tmp_path / "dispatch-custody"

    async with _selected_v23_native_fanout(
        tmp_path,
        tool_calls=ExecutionContinue.model_validate_json(first_response).tool_calls,
        accepted_call_ids=("first-open-effect",),
        cancelled_call_id=None,
        dispatch_cap=2,
        dispatch_scenarios=scenarios,
        manifest=R14_EXECUTION_NATIVE_SEALED_ACCOUNTING_V39_PRODUCTION_MANIFEST,
        model_responses=(first_response, second_response),
        recovery_cut_source=NativeRecoveryCutSource(),
        recovery_profile_version="v5",
        native_one_call_context=True,
    ) as (runtime, first_seal, first_accepted, run_id):
        assert first_accepted is not None
        first_open_call_id = first_accepted.original_call_id
        first_open_accepted = first_accepted
        first_open_intent = first_open_accepted.external_intent.subject_id
        await runtime.authorize_dispatch(
            "hermetic-ingress", first_open_intent, "authorize-first-open", run_id
        )
        await runtime.commit_first_send(
            "hermetic-ingress", first_open_intent, "send-first-open", run_id
        )
        with pytest.raises(HermeticResponseLost):
            await runtime.emit_committed("hermetic-ingress", first_open_intent)
        first_open_result = await runtime.publish_accepted_open_recovery(
            original_call_id=first_open_call_id
        )
        first_v3 = await publish_native_sealed_accounting_v3(runtime, first_seal)
        assert tuple(item.disposition.kind for item in first_v3.result.complete_ordered_calls) == (
            "RECOVERY_REQUIRED",
        )
        first_open_entries = tuple(
            retained_accepted_open_recovery(json.loads(raw))
            for _, _, raw in runtime._loop_decisions().entries()
            if json.loads(raw).get("kind") == "DECIDED"
            and json.loads(raw).get("operation_kind") == ACCEPTED_OPEN_RECOVERY_OPERATION
        )
        first_open_evidence = next(
            item
            for item in first_open_entries
            if item.request.cut.original_call_id == first_open_call_id
        )
        first_open_cut = first_open_evidence.request.cut

        # A prior OPEN must be resolved and resumed before the next Turn is admitted.
        first_run = read_execution_history(runtime).records[-1]
        assert first_run.run_id == run_id
        recovery_source = runtime._full_recovery_cut_source
        collect_held_calls = []

        class CountingRecoverySource:
            def collect_held(self, actual_runtime):
                collect_held_calls.append(actual_runtime)
                return recovery_source.collect_held(actual_runtime)

            def authenticate_selected_held(self, actual_runtime, evidence, entry):
                return recovery_source.authenticate_selected_held(
                    actual_runtime, evidence, entry
                )

        runtime._full_recovery_cut_source = CountingRecoverySource()
        suspension = await runtime.suspend_execution(run_id, first_run.head, ())
        assert collect_held_calls == [runtime]
        runtime._full_recovery_cut_source = recovery_source
        suspended_head = suspension.pair.suspended_run.revision.head
        await runtime.observe_dispatch_outcome(first_open_intent)
        closed = await runtime.resolve_dispatch_outcome(
            "hermetic-ingress", first_open_intent, "close-first-open"
        )
        assert DispatchOutcomeRecordV2.model_validate_json(
            closed.complete_records[0].canonical_bytes
        ).snapshot.state == "CONFIRMED"
        await runtime.publish_original_resolver_authority_policy()
        await runtime.publish_original_resolver_invocation()
        first_resolution = await runtime.publish_original_call_resolution()
        await runtime.publish_loop_semantic_reduction()
        resumed = await runtime.resume_execution(
            run_id, suspended_head, b"resume-first-open-before-next-turn"
        )
        # Observe the real native collector and both actual writer callbacks.
        # No source result, eligibility or authenticated input is substituted.
        readiness_collects = []
        readiness_guards = []
        native_collect = NativeRecoveryCutSource.collect_held
        original_submit = runtime._appender.submit

        def count_readiness_collect(actual_source, actual_runtime):
            readiness_collects.append((actual_source, actual_runtime))
            return native_collect(actual_source, actual_runtime)

        async def count_readiness_guards(batch):
            if batch.operation_kind != "agent_loop.execution-accounting-readiness.v1":
                return await original_submit(batch)

            def admission():
                readiness_guards.append("admission")
                return batch.admission_guard()

            def decision(resulting):
                readiness_guards.append("decision")
                return batch.decision_guard(resulting)

            return await original_submit(replace(
                batch, admission_guard=admission, decision_guard=decision,
            ))

        assert "submit" not in vars(runtime._appender)
        with monkeypatch.context() as readiness_patch, patch.object(
            runtime._appender, "submit", count_readiness_guards
        ):
            readiness_patch.setattr(
                NativeRecoveryCutSource, "collect_held", count_readiness_collect
            )
            try:
                await runtime.publish_execution_accounting_readiness()
            finally:
                assert readiness_collects == [(recovery_source, runtime)]
                assert readiness_guards == ["admission", "decision"]
        assert "submit" not in vars(runtime._appender)
        next_run = await runtime.start_next_execution_turn(resumed.run_id, resumed.head)
        from chiplog.composition import r14_execution_runtime

        rendered_contexts = []
        original_render = r14_execution_runtime.render_execution_prompt

        def observe_render(content):
            rendered_contexts.append(json.loads(content))
            return original_render(content)

        with monkeypatch.context() as capture_patch:
            capture_patch.setattr(r14_execution_runtime, "render_execution_prompt", observe_render)
            captured = await runtime.capture_execution(
                "hermetic-ingress", next_run.run_id, next_run.head
            )
        assert len(rendered_contexts) == 1
        assert rendered_contexts[0]["prior_turns"][0]["results"] == [{
            "original_call_id": first_open_call_id,
            "terminal": first_open_result.terminal.model_dump(mode="json"),
            "result": first_resolution.recovered_outcome.model_dump(mode="json"),
        }]
        second_run = await runtime.seal_execution(
            "hermetic-ingress", captured.run_id, captured.head
        )
        second_seal = second_run.turns[-1].response_seal
        assert second_seal is not None and second_seal != first_seal
        second_history = read_execution_recovery_history(runtime)
        second_call = next(
            item
            for item in second_history[1].ordered_calls
            if item.initialized_record.call.original.model_call_label == "second-open-effect"
        )
        second_head = CallSubjectHead(
            subject_id=second_run.run_id,
            revision=Present(head=second_run.head, fingerprint=second_run.digest()),
        )
        preview = await runtime.preview_call_acceptance(
            "hermetic-ingress",
            CallAcceptanceTarget(
                original_call_id=second_call.original_call_id,
                initialized=second_call.initialized,
                current_run=second_head,
            ),
        )
        second_accepted = await runtime.accept_call(
            "hermetic-ingress",
            CallAcceptanceAdoption(
                act_id="accept-second-seal-open", preview_bytes=preview.canonical_bytes()
            ),
        )
        second_intent = second_accepted.external_intent.subject_id
        await runtime.authorize_dispatch(
            "hermetic-ingress", second_intent, "authorize-second-open", run_id
        )
        await runtime.commit_first_send(
            "hermetic-ingress", second_intent, "send-second-open", run_id
        )
        with pytest.raises(HermeticResponseLost):
            await runtime.emit_committed("hermetic-ingress", second_intent)

        predecessor_anchor = runtime._commitment_journal.load(runtime._tenant_id)
        original_submit = runtime._appender.submit
        interrupted_commands = []

        async def lose_recovery_ack(command):
            if command.operation_kind == ACCEPTED_OPEN_RECOVERY_OPERATION:
                interrupted_commands.append(command)
                command = replace(command, fault="after_commit")
            return await original_submit(command)

        with monkeypatch.context() as patcher:
            patcher.setattr(runtime._appender, "submit", lose_recovery_ack)
            with pytest.raises(LostCommitAcknowledgement, match="injected"):
                await runtime.publish_accepted_open_recovery(
                    original_call_id=second_call.original_call_id
                )
        assert len(interrupted_commands) == 1
        state, actual, anchor = runtime._selected_physical_state(interrupted_commands[0])
        assert state == "COMPLETE" and actual != anchor
        pending = runtime._pending()
        assert len(pending) == 1
        pending_evidence = retained_accepted_open_recovery(pending[0])
        second_open_cut = pending_evidence.request.cut
        assert second_open_cut.original_call_id == second_call.original_call_id
        assert anchor == predecessor_anchor
        assert anchor == runtime._commitment_journal.load(runtime._tenant_id)

        # The real outer fold must authenticate both seals and every original call.
        outer = read_execution_recovery_history(
            runtime, selected_only=True, exact_historical_sources=True
        )
        assert {
            item.proposal.sealed_run.turns[-1].response_seal for item in outer[3]
        } >= {first_seal, second_seal}
        assert {
            item.initialized_record.call.original.model_call_label
            for item in outer[1].ordered_calls
        } == {"first-open-effect", "second-open-effect"}
        assert first_open_result.accounting.kind == "RECOVERY_REQUIRED"

    resources = HermeticDispatchResources(
        scenarios=scenarios, cap=2, custody_path=custody
    )
    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        manifest=R14_EXECUTION_NATIVE_SEALED_ACCOUNTING_V39_PRODUCTION_MANIFEST,
        recovery_cut_source=NativeRecoveryCutSource(),
    ) as reopened:
        assert not reopened._pending()
        assert reopened.read_selected_accepted_open_recovery(
            original_call_id=first_open_call_id
        ) == first_open_result
        assert reopened.read_selected_accepted_open_recovery(
            original_call_id=second_call.original_call_id
        ) == pending_evidence.result

        projection = R14OriginalNativePrepareSource(reopened).collect(second_seal)
        assert projection is not None
        source = selected_source.R14NativeSelectedAccountingSource(
            reopened, projection, bind_native_execution_preparation_v2(projection)
        )
        assert source.collect_one_accepted_open_outcome(
            second_open_cut.current_run,
            selected_only=True,
            original_call_id=second_open_cut.original_call_id,
            expected_outcome_record_id=second_open_cut.selected_outcome_record_id,
            expected_selected_decision=second_open_cut.selected_decision,
            expected_tenant_commit_sequence=second_open_cut.tenant_commit_sequence,
        ) is not None
        substitutions = (
            (
                "outcome_record_id",
                first_open_cut.selected_outcome_record_id,
                second_open_cut.selected_outcome_record_id,
            ),
            (
                "selected_decision",
                first_open_cut.selected_decision,
                second_open_cut.selected_decision,
            ),
            (
                "tenant_commit_sequence",
                first_open_cut.tenant_commit_sequence,
                second_open_cut.tenant_commit_sequence,
            ),
        )
        for field, foreign_value, exact_value in substitutions:
            assert foreign_value != exact_value, field
            result = source.collect_one_accepted_open_outcome(
                second_open_cut.current_run,
                selected_only=True,
                original_call_id=second_open_cut.original_call_id,
                expected_outcome_record_id=(
                    foreign_value if field == "outcome_record_id"
                    else second_open_cut.selected_outcome_record_id
                ),
                expected_selected_decision=(
                    foreign_value if field == "selected_decision"
                    else second_open_cut.selected_decision
                ),
                expected_tenant_commit_sequence=(
                    foreign_value if field == "tenant_commit_sequence"
                    else second_open_cut.tenant_commit_sequence
                ),
            )
            assert result is None, field

        first_v3_reopened = await publish_native_sealed_accounting_v3(reopened, first_seal)
        second_v3_reopened = await publish_native_sealed_accounting_v3(reopened, second_seal)
        assert first_v3_reopened.result == first_v3.result
        assert tuple(
            item.disposition.kind for item in second_v3_reopened.result.complete_ordered_calls
        ) == ("RECOVERY_REQUIRED",)
        reopened_head = read_execution_history(reopened).tenant_head
        assert (
            await publish_native_sealed_accounting_v3(reopened, second_seal)
            == second_v3_reopened
        )
        assert (
            await publish_native_sealed_accounting_v3(reopened, first_seal) == first_v3_reopened
        )
        assert await reopened.publish_accepted_open_recovery(
            original_call_id=second_call.original_call_id
        ) == pending_evidence.result
        assert await reopened.publish_accepted_open_recovery(
            original_call_id=first_open_call_id
        ) == first_open_result
        assert read_execution_history(reopened).tenant_head == reopened_head
        assert len(resources.require_original_provider().transfers) == 2
