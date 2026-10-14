"""Mounted evidence tests for target-scoped native selected accounting facts."""

from __future__ import annotations

import json
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
    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        responses=(response,) if model_responses is None else model_responses,
        manifest=manifest,
        recovery_cut_source=recovery_cut_source,
    ) as runtime:
        if recovery_profile_version is not None:
            await runtime.publish_native_recovery_profile(version=recovery_profile_version)
        request = await admit_runtime_slot(runtime, "native-v23")
        initial = await runtime.drive_input(request)
        assert isinstance(initial, SelectedExecutionReceiptV1)
        started = await runtime.begin_execution(
            "hermetic-ingress", initial.stable_run_lineage_id, initial.selected_run_head.head
        )
        captured = await runtime.capture_execution(
            "hermetic-ingress",
            initial.stable_run_lineage_id,
            started.head,
            native_one_call_context=native_one_call_context,
        )
        await runtime.seal_execution(
            "hermetic-ingress", initial.stable_run_lineage_id, captured.head
        )
        _snapshot, inventory, _legacy, seals, _cancellations, _acceptances = (
            read_execution_recovery_history(runtime)
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
) -> None:
    database = tmp_path / "native-two-turn-seals.sqlite"
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
    async with _selected_v23_native_fanout(
        tmp_path,
        cancelled_call_id="effect",
        dispatch_cap=1,
        manifest=R14_EXECUTION_NATIVE_SEALED_ACCOUNTING_V39_PRODUCTION_MANIFEST,
        native_one_call_context=True,
        model_responses=(native_response(), followup),
        recovery_cut_source=NativeRecoveryCutSource(),
        recovery_profile_version="v3",
    ) as (runtime, first_seal, _accepted, run_id):
        await runtime.publish_native_one_call_cancelled_accounting()
        await publish_native_sealed_accounting_v3(runtime, first_seal)

        await runtime.publish_execution_accounting_readiness()
        first_run = read_execution_history(runtime).records[-1]
        next_run = await runtime.start_next_execution_turn(run_id, first_run.head)
        captured = await runtime.capture_execution(
            "hermetic-ingress", next_run.run_id, next_run.head
        )
        second_run = await runtime.seal_execution(
            "hermetic-ingress", captured.run_id, captured.head
        )
        second_seal = second_run.turns[-1].response_seal
        assert second_seal is not None
        assert first_seal != second_seal
        second_call = next(
            item
            for item in read_execution_recovery_history(runtime)[1].ordered_calls
            if item.initialized_record.call.original.model_call_label == "followup-effect"
        )
        second_run_head = CallSubjectHead(
            subject_id=second_run.run_id,
            revision=Present(head=second_run.head, fingerprint=second_run.digest()),
        )
        preview = await runtime.preview_call_acceptance(
            "hermetic-ingress",
            CallAcceptanceTarget(
                original_call_id=second_call.original_call_id,
                initialized=second_call.initialized,
                current_run=second_run_head,
            ),
        )
        accepted = await runtime.accept_call(
            "hermetic-ingress",
            CallAcceptanceAdoption(
                act_id="accept-followup-seal-member",
                preview_bytes=preview.canonical_bytes(),
            ),
        )
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
        assert second_v3.request.selected_response == second_seal
        assert first_v3.request.selected_response == first_seal
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
