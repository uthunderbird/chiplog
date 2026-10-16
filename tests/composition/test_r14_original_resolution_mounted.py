"""Mounted R14 resolver selects the exact closed R16 result and reopens it."""

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from chiplog.adapters.driven.effects_hermetic import HermeticResponseLost
from chiplog.adapters.driven.loop_sqlite import LoopIntegrityError
from chiplog.architecture.r7_runtime import (
    R14_EXECUTION_CURRENT_ACTOR_RESUME_V40_PRODUCTION_MANIFEST,
)
from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.original_recovery_contracts import (
    ConsumableReduction,
    HeldReduction,
    PreparedLoopSemanticReduction,
    PreparedOriginalCallResolution,
)
from chiplog.capabilities.effects.dispatch_outcome_contracts import DispatchOutcomeRecordV2
from chiplog.composition import r14_loop_history
from chiplog.composition.common_cli_execution_runtime import (
    open_common_cli_execution_runtime,
)
from chiplog.composition.r14_accepted_open_accounting_publication import (
    hold_if_accepted_open,
)
from chiplog.composition.r14_call_acceptance_port import (
    CallAcceptanceAdoption,
    CallAcceptanceTarget,
)
from chiplog.composition.r14_call_preview import execution_run_head
from chiplog.composition.r14_loop_history import read_execution_call_history
from chiplog.composition.r14_loop_semantic_reduction_publication import SCHEMA as REDUCTION_SCHEMA
from chiplog.composition.r14_native_recovery_cut import NativeRecoveryCutSource
from chiplog.composition.r14_next_turn_records import NEXT_TURN_PUBLICATION
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform._owner_publication_contracts import JournalSelectedPublication
from tests.support.h1_cli_execution import admit_runtime_slot
from tests.support.native_cancelled import native_response


@pytest.mark.asyncio
async def test_original_resolution_publishes_closed_outcome_and_reopens_without_resend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "r14-original-resolution.sqlite"
    custody = tmp_path / "custody"
    resources = HermeticDispatchResources(
        scenarios=("LOST_RESPONSE_AFTER_EFFECT",),
        late_receipt_scenarios=(("COMPATIBLE", "RIVAL"),),
        cap=1,
        custody_path=custody,
    )
    manifest = R14_EXECUTION_CURRENT_ACTOR_RESUME_V40_PRODUCTION_MANIFEST

    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        responses=(native_response(),),
        manifest=manifest,
        recovery_cut_source=NativeRecoveryCutSource(),
    ) as runtime:
        await runtime.publish_native_recovery_profile(version="v5")
        wire = await admit_runtime_slot(runtime, "r14-original-resolution")
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
                act_id="accept-original-resolution", preview_bytes=preview.canonical_bytes()
            ),
        )
        intent_id = accepted.external_intent.subject_id
        assert isinstance(
            await runtime.authorize_dispatch(
                "hermetic-ingress", intent_id, "authorize-original-resolution", sealed.run_id
            ),
            JournalSelectedPublication,
        )
        assert isinstance(
            await runtime.commit_first_send(
                "hermetic-ingress", intent_id, "send-original-resolution", sealed.run_id
            ),
            JournalSelectedPublication,
        )

        with pytest.raises(HermeticResponseLost):
            await runtime.emit_committed("hermetic-ingress", intent_id)
        open_recovery = await runtime.publish_accepted_open_recovery()
        assert open_recovery.accounting.kind == "RECOVERY_REQUIRED"
        assert open_recovery.opening.original.original_call_id == calls[0].original_call_id
        suspension = await runtime.suspend_execution(sealed.run_id, sealed.head, ())
        suspended_head = suspension.pair.suspended_run.revision.head

        await runtime.observe_dispatch_outcome(intent_id)
        closed_publication = await runtime.resolve_dispatch_outcome(
            "hermetic-ingress", intent_id, "close-original-resolution"
        )
        closed = DispatchOutcomeRecordV2.model_validate_json(
            closed_publication.complete_records[0].canonical_bytes
        )
        assert closed.snapshot.state == "CONFIRMED"
        assert closed.snapshot.obligation.state == "CLOSED"
        assert len(resources.require_original_provider().transfers) == 1

        policy = await runtime.publish_original_resolver_authority_policy()
        assert policy.principal_id == "hermetic-principal"
        invocation = await runtime.publish_original_resolver_invocation()
        assert invocation.original == open_recovery.opening.original
        resolution = await runtime.publish_original_call_resolution()
        assert isinstance(resolution, PreparedOriginalCallResolution)
        assert resolution.closure.original == open_recovery.opening.original
        assert resolution.recovered_outcome.original == open_recovery.opening.original
        commitment_before_resume_hold = runtime._commitment_journal.load(runtime._tenant_id)
        with pytest.raises(LoopRejected, match="HOLD"):
            await runtime.resume_execution(
                sealed.run_id, suspended_head, b"resume-obligation-without-reduction"
            )
        assert runtime.read_selected_execution_resume() is None
        assert runtime._commitment_journal.load(runtime._tenant_id) == commitment_before_resume_hold
        reduction = await runtime.publish_loop_semantic_reduction()
        assert isinstance(reduction, PreparedLoopSemanticReduction)
        assert reduction.record.disposition.kind == "CONSUMABLE"
        selected_reduction = runtime.read_selected_loop_semantic_reduction()
        assert selected_reduction.record == reduction.record
        resolved_frontier = runtime.read_selected_accepted_open_frontier()
        resolved_disposition = resolved_frontier.calls[0].disposition
        assert resolved_disposition.kind == "RECOVERY_REQUIRED"
        assert resolved_disposition.result.kind == "ABSENT"
        assert resolved_disposition.recovered_outcome.outcome.kind == "PRESENT"
        assert resolved_disposition.recovered_outcome.outcome.head == (
            "record:" + resolution.recovered_outcome.digest()
        )
        assert len(resources.require_original_provider().transfers) == 1

    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        responses=(native_response(),),
        manifest=manifest,
        recovery_cut_source=NativeRecoveryCutSource(),
    ) as reopened:
        assert reopened.read_selected_original_resolver_authority_policy() == policy
        assert reopened.read_selected_original_resolver_invocation_source()[0] == invocation
        history_reads: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        original_history_reader = r14_loop_history._read_call_history

        def counted_history_reader(*args: Any, **kwargs: Any) -> object:
            history_reads.append((args, kwargs))
            return original_history_reader(*args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(r14_loop_history, "_read_call_history", counted_history_reader)
            assert reopened.read_selected_original_call_resolution() == resolution
        assert len(history_reads) == 1
        assert history_reads[0] == (
            (reopened,),
            {"selected_only": True, "exact_historical_sources": False},
        )
        reduction_bytes = reduction.record.canonical_bytes()
        reduction_id = REDUCTION_SCHEMA + ":" + hashlib.sha256(reduction_bytes).hexdigest()
        reduction_key = (reopened._tenant_id, reduction_id, REDUCTION_SCHEMA)
        with closing(sqlite3.connect(database)) as connection:
            assert connection.execute(
                "SELECT canonical_bytes FROM records "
                "WHERE tenant_id=? AND record_id=? AND schema_id=?",
                reduction_key,
            ).fetchall() == [(reduction_bytes,)]
        try:
            with (
                reopened._authority_gate().hold(),
                closing(sqlite3.connect(database)) as connection,
                connection,
            ):
                assert (
                    connection.execute(
                        "UPDATE records SET canonical_bytes=? "
                        "WHERE tenant_id=? AND record_id=? AND schema_id=?",
                        (b"changed", *reduction_key),
                    ).rowcount
                    == 1
                )
            history_reads.clear()
            with monkeypatch.context() as patch:
                patch.setattr(r14_loop_history, "_read_call_history", counted_history_reader)
                with pytest.raises(LoopIntegrityError, match="operation=snapshot"):
                    reopened.read_selected_original_call_resolution()
            assert len(history_reads) == 1
        finally:
            with (
                reopened._authority_gate().hold(),
                closing(sqlite3.connect(database)) as connection,
                connection,
            ):
                assert (
                    connection.execute(
                        "UPDATE records SET canonical_bytes=? "
                        "WHERE tenant_id=? AND record_id=? AND schema_id=?",
                        (reduction_bytes, *reduction_key),
                    ).rowcount
                    == 1
                )
        assert reopened.read_selected_original_call_resolution() == resolution
        assert reopened.read_selected_loop_semantic_reduction().record == reduction.record
        provider = resources.require_original_provider()
        compatible_receipt = provider.observe_late_receipt(
            provider.transfers[0].ticket.transmission_id
        )
        compatible_publication = await reopened.observe_dispatch_outcome(
            intent_id, compatible_receipt
        )
        compatible = DispatchOutcomeRecordV2.model_validate_json(
            compatible_publication.complete_records[0].canonical_bytes
        )
        assert compatible.snapshot.obligation.state == "CLOSED"
        assert compatible.snapshot.conflicting_evidence is False
        assert len(compatible.snapshot.evidence_heads) == len(closed.snapshot.evidence_heads) + 1
        assert len(provider.transfers) == 1

        # The old consumable head remains selected but cannot clear the recovery hold.
        assert reopened.read_selected_loop_semantic_reduction().record == reduction.record
        assert await hold_if_accepted_open(reopened, "hermetic-ingress")
        refined_reduction = await reopened.publish_loop_semantic_reduction()
        assert isinstance(refined_reduction.record.disposition, ConsumableReduction)
        assert refined_reduction.record.reduction_id == reduction.record.reduction_id
        assert refined_reduction.record.disposition.semantic_class == (
            reduction.record.disposition.semantic_class
        )
        assert (
            len(refined_reduction.record.complete_ordered_evidence)
            == len(reduction.record.complete_ordered_evidence) + 1
        )
        assert not await hold_if_accepted_open(reopened, "hermetic-ingress")

    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        responses=(native_response(),),
        manifest=manifest,
        recovery_cut_source=NativeRecoveryCutSource(),
    ) as reopened:
        assert reopened.read_selected_original_resolver_authority_policy() == policy
        assert reopened.read_selected_original_resolver_invocation_source()[0] == invocation
        assert reopened.read_selected_original_call_resolution() == resolution
        assert reopened.read_selected_loop_semantic_reduction().record == refined_reduction.record
        resolved_frontier = reopened.read_selected_accepted_open_frontier()
        resolved_disposition = resolved_frontier.calls[0].disposition
        assert resolved_disposition.kind == "RECOVERY_REQUIRED"
        assert resolved_disposition.result.kind == "ABSENT"
        assert resolved_disposition.recovered_outcome.outcome.kind == "PRESENT"
        assert resolved_disposition.recovered_outcome.outcome.head == (
            "record:" + resolution.recovered_outcome.digest()
        )
        with pytest.raises(LoopRejected, match="HOLD"):
            await reopened.start_next_execution_turn(sealed.run_id, sealed.head)
        assert not any(
            json.loads(raw).get("operation_kind") == NEXT_TURN_PUBLICATION
            for _, _, raw in reopened._loop_decisions().entries()
        )

        resumed = await reopened.resume_execution(
            sealed.run_id, suspended_head, b"resume-closed-obligation"
        )
        assert resumed.run_id == sealed.run_id
        assert resumed.state == "ACTIVE"
        assert resumed.predecessor == suspended_head
        assert resumed.worker_session == suspension.run.worker_session
        assert resumed.worker_session != reopened.current_worker()
        assert reopened.read_selected_execution_resume() == resumed
        resumed_cut = NativeRecoveryCutSource().collect(reopened)
        assert resumed_cut is not None
        assert resumed_cut.complete_ordered_run_lineage[-1] == resumed
        assert len(resumed_cut.original_closures) == len(resumed_cut.current_reductions) == 1
        await reopened.publish_execution_accounting_readiness()
        next_run = await reopened.start_next_execution_turn(resumed.run_id, resumed.head)
        assert next_run.event == "TurnStarted"
        assert next_run.worker_session == reopened.current_worker()
        assert next_run.turns[:-1] == resumed.turns
        assert tuple(turn.ordinal for turn in next_run.turns) == (1, 2)
        assert next_run.turns[-1].state == "PREPARING"
        assert await reopened.start_next_execution_turn(resumed.run_id, resumed.head) == next_run
        assert (
            sum(
                json.loads(raw).get("operation_kind") == NEXT_TURN_PUBLICATION
                for _, _, raw in reopened._loop_decisions().entries()
            )
            == 1
        )

        provider = resources.require_original_provider()
        rival_receipt = provider.observe_late_receipt(provider.transfers[0].ticket.transmission_id)
        rival_publication = await reopened.observe_dispatch_outcome(intent_id, rival_receipt)
        rival = DispatchOutcomeRecordV2.model_validate_json(
            rival_publication.complete_records[0].canonical_bytes
        )
        assert rival.snapshot.obligation.state == "CLOSED"
        assert rival.snapshot.conflicting_evidence is True
        assert len(rival.snapshot.evidence_heads) == len(compatible.snapshot.evidence_heads) + 1
        assert await hold_if_accepted_open(reopened, "hermetic-ingress")
        held_reduction = await reopened.publish_loop_semantic_reduction()
        assert isinstance(held_reduction.record.disposition, HeldReduction)
        assert held_reduction.record.reduction_id == refined_reduction.record.reduction_id
        assert await hold_if_accepted_open(reopened, "hermetic-ingress")
        with pytest.raises(LoopRejected, match="HOLD"):
            await reopened.start_next_execution_turn(sealed.run_id, sealed.head)
        assert (
            sum(
                json.loads(raw).get("operation_kind") == NEXT_TURN_PUBLICATION
                for _, _, raw in reopened._loop_decisions().entries()
            )
            == 1
        )
        assert len(provider.transfers) == 1
