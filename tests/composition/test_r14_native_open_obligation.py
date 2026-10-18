"""Mounted accepted-call accounting keeps unresolved R16 work on HOLD."""

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from chiplog.adapters.driven.effects_hermetic import HermeticResponseLost
from chiplog.architecture.r7_runtime import (
    R14_EXECUTION_ACCEPTED_OPEN_V29_PRODUCTION_MANIFEST,
    R14_EXECUTION_NATIVE_SEALED_ACCOUNTING_V39_PRODUCTION_MANIFEST,
)
from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallPreparationRejected
from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.direct_terminal_contracts import (
    AcceptedDirectTerminalCutV1,
    PrepareAcceptedDirectTerminalV1,
)
from chiplog.capabilities.agent_loop.direct_terminal_preparation import (
    prepare_accepted_direct_terminal_v1,
)
from chiplog.capabilities.agent_loop.execution_recovery_contracts import (
    PrepareAcceptedOpenAccounting,
    PrepareAcceptedOpenRecoveryV1,
    PreparedAcceptedOpenAccounting,
    PreparedAcceptedOpenRecoveryV1,
)
from chiplog.capabilities.agent_loop.execution_recovery_preparation import (
    prepare_accepted_open_accounting,
    prepare_accepted_open_recovery_v1,
)
from chiplog.capabilities.effects.dispatch_outcome_contracts import DispatchOutcomeRecordV2
from chiplog.composition.common_cli_execution_runtime import (
    CommonCliExecutionRuntime,
    open_common_cli_execution_runtime,
)
from chiplog.composition.common_execution_driver_contracts import ExecutionDriverRejectedV1
from chiplog.composition.r14_accepted_open_accounting_publication import (
    _accepted_open_publication_state_is_valid,
)
from chiplog.composition.r14_call_acceptance_port import (
    CallAcceptanceAdoption,
    CallAcceptanceTarget,
)
from chiplog.composition.r14_call_preview import execution_run_head
from chiplog.composition.r14_loop_history import (
    read_execution_call_history,
    read_execution_recovery_history,
)
from chiplog.composition.r14_native_accepted_open_cut import (
    build_accepted_open_recovery_cut,
)
from chiplog.composition.r14_native_preparation_bindings import (
    bind_native_execution_preparation_v2,
)
from chiplog.composition.r14_native_recovery_cut import NativeRecoveryCutSource
from chiplog.composition.r14_native_selected_accounting_source import (
    R14NativeSelectedAccountingSource,
)
from chiplog.composition.r14_original_prepare_source import R14OriginalNativePrepareSource
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform._owner_publication_contracts import JournalSelectedPublication
from chiplog.platform.authority_reads import capture_authority_snapshot_commitment
from chiplog.platform.workspace_snapshot import read_connection
from tests.support.h1_cli_execution import admit_runtime_slot
from tests.support.native_cancelled import native_response


def test_historical_recovery_publication_requires_complete_or_exact_pending_state() -> None:
    predecessor, resulting, later = "before", "after", "later"
    assert _accepted_open_publication_state_is_valid("COMPLETE", later, later, predecessor)
    assert _accepted_open_publication_state_is_valid(
        "ABSENT", predecessor, predecessor, predecessor
    )
    assert not _accepted_open_publication_state_is_valid("ABSENT", later, later, predecessor)
    assert not _accepted_open_publication_state_is_valid(
        "PARTIAL", resulting, resulting, predecessor
    )
    assert not _accepted_open_publication_state_is_valid("COMPLETE", later, resulting, predecessor)


@pytest.mark.asyncio
async def test_open_obligation_successor_holds_after_reopen(tmp_path: Path) -> None:
    database = tmp_path / "native-open-successor-hold.sqlite"
    resources = HermeticDispatchResources(
        scenarios=("LOST_RESPONSE_AFTER_EFFECT",),
        cap=1,
        custody_path=tmp_path / "custody",
    )
    manifest = R14_EXECUTION_NATIVE_SEALED_ACCOUNTING_V39_PRODUCTION_MANIFEST
    source = NativeRecoveryCutSource()
    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        responses=(native_response(),),
        manifest=manifest,
        recovery_cut_source=source,
    ) as runtime:
        await runtime.publish_native_recovery_profile(version="v5")
        wire = await admit_runtime_slot(runtime, "native-open-successor-hold")
        receipt = await runtime.drive_input(wire)
        started = await runtime.begin_execution(
            "hermetic-ingress", receipt.stable_run_lineage_id, receipt.selected_run_head.head
        )
        captured = await runtime.capture_execution(
            "hermetic-ingress", started.run_id, started.head, native_one_call_context=True
        )
        sealed = await runtime.seal_execution("hermetic-ingress", captured.run_id, captured.head)
        _, inventory, _ = read_execution_call_history(runtime)
        calls = [
            item
            for item in inventory.ordered_calls
            if item.initialized_record.call.classification == "CONSEQUENTIAL"
        ]
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
                act_id="accept-open-successor-hold", preview_bytes=preview.canonical_bytes()
            ),
        )
        intent_id = accepted.external_intent.subject_id
        await runtime.authorize_dispatch(
            "hermetic-ingress", intent_id, "authorize-open-successor-hold", sealed.run_id
        )
        await runtime.commit_first_send(
            "hermetic-ingress", intent_id, "send-open-successor-hold", sealed.run_id
        )
        with pytest.raises(HermeticResponseLost):
            await runtime.emit_committed("hermetic-ingress", intent_id)
        assert len(resources.require_original_provider().transfers) == 1
        selected_recovery = await runtime.publish_accepted_open_recovery()
        assert selected_recovery.accounting.kind == "RECOVERY_REQUIRED"
        selected_outcome = DispatchOutcomeRecordV2.model_validate_json(
            selected_recovery.accounting.selected_outcome_bytes
        )
        assert selected_outcome.snapshot.state == "OUTCOME_UNKNOWN"
        assert selected_outcome.snapshot.obligation.state == "OPEN"

        suspension = await runtime.suspend_execution(sealed.run_id, sealed.head, ())
        assert suspension.baseline.original_obligations == (selected_recovery.opening.original,)

    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        manifest=manifest,
        recovery_cut_source=source,
    ) as reopened:
        new_request = await admit_runtime_slot(
            reopened,
            "open-obligation-successor-after-reopen",
            b"start a different follow-up task",
        )
        history_before_successor = read_execution_call_history(reopened)

        held = await reopened.drive_input(new_request)
        assert isinstance(held, ExecutionDriverRejectedV1)
        assert held.code == "HOLD", held.reason
        assert held.reason == (
            "recovery owner preparation HOLD: successor requires recovery of original obligations"
        )
        assert read_execution_call_history(reopened) == history_before_successor
        assert not any(
            json.loads(raw).get("operation_kind") == "agent_loop.execution-successor.v1"
            for _, _, raw in reopened._loop_decisions().entries()
        )
        assert len(resources.require_original_provider().transfers) == 1


@pytest.mark.asyncio
async def test_selected_native_open_obligation_remains_outcome_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "native-open-obligation.sqlite"
    resources = HermeticDispatchResources(
        scenarios=("LOST_RESPONSE_AFTER_EFFECT",),
        cap=1,
        custody_path=tmp_path / "custody",
    )
    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        responses=(native_response(),),
        manifest=R14_EXECUTION_NATIVE_SEALED_ACCOUNTING_V39_PRODUCTION_MANIFEST,
        recovery_cut_source=NativeRecoveryCutSource(),
    ) as runtime:
        await runtime.publish_native_recovery_profile(version="v5")
        wire = await admit_runtime_slot(runtime, "native-open-obligation")
        receipt = await runtime.drive_input(wire)
        started = await runtime.begin_execution(
            "hermetic-ingress", receipt.stable_run_lineage_id, receipt.selected_run_head.head
        )
        captured = await runtime.capture_execution(
            "hermetic-ingress", started.run_id, started.head, native_one_call_context=True
        )
        sealed = await runtime.seal_execution("hermetic-ingress", captured.run_id, captured.head)
        _, inventory, _ = read_execution_call_history(runtime)
        calls = [
            item
            for item in inventory.ordered_calls
            if item.initialized_record.call.classification == "CONSEQUENTIAL"
        ]
        assert len(calls) == 1
        verified_publications = runtime._verified_publications

        def reject_planning_inventory(owner: str):
            assert owner != "planning", "call-origin capture must not scan planning history"
            return verified_publications(owner)

        monkeypatch.setattr(runtime, "_verified_publications", reject_planning_inventory)
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
                act_id="accept-open-call", preview_bytes=preview.canonical_bytes()
            ),
        )
        intent_id = accepted.external_intent.subject_id
        authorized = await runtime.authorize_dispatch(
            "hermetic-ingress", intent_id, "authorize-open", sealed.run_id
        )
        assert isinstance(authorized, JournalSelectedPublication), authorized
        sent = await runtime.commit_first_send(
            "hermetic-ingress", intent_id, "send-open", sealed.run_id
        )
        assert isinstance(sent, JournalSelectedPublication), sent
        with pytest.raises(HermeticResponseLost):
            await runtime.emit_committed("hermetic-ingress", intent_id)
        assert len(resources.require_original_provider().transfers) == 1
        open_outcomes = [
            DispatchOutcomeRecordV2.model_validate_json(record.canonical_bytes)
            for decision in runtime._owner_decisions().snapshot().decisions
            for record in decision.prepared.request.complete_records
            if record.schema_id == "chiplog.effects.dispatch-outcome-record.v2"
        ]
        assert len(open_outcomes) == 1
        assert open_outcomes[0].snapshot.obligation.state == "OPEN"
        assert open_outcomes[0].snapshot.state == "OUTCOME_UNKNOWN"
        response_seal = read_execution_recovery_history(runtime)[3][
            0
        ].proposal.fan_out.response_seal
        from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
        from chiplog.capabilities.agent_loop.recovery_contracts import Present

        seal_ref = CallSubjectHead(
            subject_id=response_seal.response_seal_id,
            revision=Present(
                head="record:" + response_seal.digest(),
                fingerprint=response_seal.digest(),
            ),
        )
        projection = R14OriginalNativePrepareSource(runtime).collect(seal_ref)
        assert projection is not None
        source = R14NativeSelectedAccountingSource(
            runtime,
            projection,
            bind_native_execution_preparation_v2(projection),
        )
        accepted_facts = source.collect_one_accepted(execution_run_head(sealed))
        assert accepted_facts is not None
        exact_open = source.collect_one_accepted_open_outcome(execution_run_head(sealed))
        assert exact_open is not None
        assert exact_open.accepted.original_call_id == calls[0].original_call_id
        assert exact_open.outcome.snapshot.state == "OUTCOME_UNKNOWN"
        assert exact_open.outcome.snapshot.obligation.state == "OPEN"
        assert exact_open.commit_sequence > 0
        assert exact_open.publication_ordinal == exact_open.selected_member.publication_ordinal
        assert exact_open.batch_record_ids
        open_cut = build_accepted_open_recovery_cut(runtime, exact_open)
        assert open_cut is not None
        direct_cut = AcceptedDirectTerminalCutV1(
            tenant_id=open_cut.tenant_id,
            database_id=open_cut.database_id,
            tenant_commit_sequence=open_cut.tenant_commit_sequence,
            materialization_commitment=open_cut.materialization_commitment,
            current_run=open_cut.current_run,
            current_run_bytes=open_cut.current_run_bytes,
            response_id=open_cut.response_id,
            original_call_id=open_cut.original_call_id,
            initialized=open_cut.initialized,
            accepted=open_cut.accepted,
            accepted_record_bytes=open_cut.accepted_record_bytes,
            execution_intent=open_cut.execution_intent,
            execution_intent_record_bytes=open_cut.execution_intent_record_bytes,
            external_effect_intent=open_cut.external_effect_intent,
            selected_outcome_record_id=open_cut.selected_outcome_record_id,
            selected_outcome_bytes=open_cut.selected_outcome_bytes,
            selected_outcome_fingerprint=open_cut.selected_outcome_fingerprint,
            selected_effect_member_bytes=exact_open.selected_member.canonical_bytes(),
            selected_decision=open_cut.selected_decision,
            semantic_binding=exact_open.selected_member.semantics,
            outcome_commit_sequence=open_cut.outcome_commit_sequence,
            publication_ordinal=open_cut.publication_ordinal,
            batch_record_ids=open_cut.batch_record_ids,
            prior_terminal="ABSENT",
            cut_fingerprint="0" * 64,
        )
        cut_body = direct_cut.model_dump(mode="json")
        cut_body.pop("cut_fingerprint")
        direct_cut = direct_cut.model_copy(
            update={
                "cut_fingerprint": hashlib.sha256(
                    json.dumps(
                        cut_body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                    ).encode()
                ).hexdigest()
            }
        )
        direct_prepared = prepare_accepted_direct_terminal_v1(
            PrepareAcceptedDirectTerminalV1(
                command_id="accepted-direct-terminal:" + direct_cut.cut_fingerprint,
                cut=direct_cut,
            )
        )
        assert isinstance(direct_prepared, CallPreparationRejected)
        assert direct_prepared.code == "HOLD"
        accounting_request = PrepareAcceptedOpenAccounting(
            command_id="accepted-open-accounting:" + open_cut.cut_fingerprint,
            cut=open_cut,
        )
        prepared = prepare_accepted_open_accounting(accounting_request)
        assert isinstance(prepared, PreparedAcceptedOpenAccounting), prepared
        assert prepared.accounting.kind == "RECOVERY_REQUIRED"
        assert prepared.accounting.selected_outcome_bytes == exact_open.outcome.canonical_bytes()

        recovery_request = PrepareAcceptedOpenRecoveryV1(
            command_id="accepted-open-recovery:" + open_cut.cut_fingerprint,
            cut=open_cut,
        )
        recovery_prepared = prepare_accepted_open_recovery_v1(recovery_request)
        assert isinstance(recovery_prepared, PreparedAcceptedOpenRecoveryV1), recovery_prepared
        assert recovery_prepared.accounting == prepared.accounting
        assert recovery_prepared.opening.original.original_call_id == calls[0].original_call_id
        assert recovery_prepared.opening.original.evidence_head.kind == "ABSENT"
        assert recovery_prepared.terminal.original_call_id == calls[0].original_call_id

        selected_recovery = await runtime.publish_accepted_open_recovery()
        assert selected_recovery == recovery_prepared
        assert selected_recovery.accounting.kind == "RECOVERY_REQUIRED"
        assert (
            selected_recovery.accounting.selected_outcome_bytes
            == exact_open.outcome.canonical_bytes()
        )
        joined = runtime.read_selected_accepted_open_frontier()
        assert len(joined.calls) == 1
        assert joined.calls[0].original_call_id == calls[0].original_call_id
        assert joined.calls[0].acceptance.kind == "CONSEQUENTIAL_ACCEPTED"
        assert joined.calls[0].disposition.kind == "RECOVERY_REQUIRED"
        assert joined.calls[0].disposition.obligation == selected_recovery.opening.original
        assert joined.calls[0].disposition.recovered_outcome.outcome.kind == "ABSENT"
        from chiplog.composition import r14_next_turn_publication

        async def reject_successor_prepare(*_args, **_kwargs) -> None:
            raise AssertionError("next-turn owner preparation must not run for OPEN obligation")

        monkeypatch.setattr(
            r14_next_turn_publication, "_exchange_next_turn", reject_successor_prepare
        )
        with pytest.raises(LoopRejected, match="OUTCOME_UNKNOWN/OPEN"):
            await runtime.start_next_execution_turn(sealed.run_id, sealed.head)
        assert len(resources.require_original_provider().transfers) == 1
        with runtime._authority_gate().hold():
            authenticated_cut = NativeRecoveryCutSource()._collect_held(runtime)
        assert authenticated_cut is not None
        assert len(authenticated_cut.frontier.ordered_calls) == 1
        assert authenticated_cut.frontier.ordered_calls[0].disposition.obligation == (
            joined.calls[0].disposition.obligation
        )
        suspension = await runtime.suspend_execution(sealed.run_id, sealed.head, ())
        assert suspension.pair.suspended_run.subject_id == sealed.run_id
        assert suspension.baseline.original_obligations == (joined.calls[0].disposition.obligation,)
        assert suspension.pair.suspended_run.revision.head
        from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
        from chiplog.composition import r14_native_selected_accounting_source as selected_source
        from chiplog.composition.r14_native_sealed_accounting_v3_publication import (
            publish_native_sealed_accounting_v3,
        )

        original_mismatch_check = selected_source._accepted_open_cut_mismatches
        exercised_mismatch_negatives = False

        def verify_suspension_cut_exception(*args):
            nonlocal exercised_mismatch_negatives
            mismatches = original_mismatch_check(*args)
            if not exercised_mismatch_negatives:
                runtime_arg, retained_cut, accepted_outcome, history, original_obligation = args
                assert mismatches == ()

                def rehash_cut(changes):
                    changed = retained_cut.model_copy(update=changes)
                    body = changed.model_dump(mode="json")
                    body.pop("cut_fingerprint")
                    fingerprint = hashlib.sha256(
                        json.dumps(
                            body,
                            sort_keys=True,
                            separators=(",", ":"),
                            ensure_ascii=False,
                        ).encode()
                    ).hexdigest()
                    return changed.model_copy(update={"cut_fingerprint": fingerprint})

                changed_acceptance = rehash_cut(
                    {"accepted_record_bytes": retained_cut.accepted_record_bytes + b"tampered"}
                )
                changed_mismatches = original_mismatch_check(
                    runtime_arg,
                    changed_acceptance,
                    accepted_outcome,
                    history,
                    original_obligation,
                )
                assert "accepted_bytes" in changed_mismatches

                snapshot = history[0]
                current_run = next(
                    run
                    for run in snapshot.records
                    if isinstance(run, ExecutionRunRecord)
                    and run.run_id == retained_cut.current_run.subject_id
                    and run.state == "SUSPENDED"
                )
                wrong_predecessor = next(
                    run
                    for run in snapshot.records
                    if isinstance(run, ExecutionRunRecord)
                    and run.run_id == retained_cut.current_run.subject_id
                    and run.head not in (retained_cut.current_run.revision.head, current_run.head)
                )
                changed_run = rehash_cut(
                    {
                        "current_run": CallSubjectHead(
                            subject_id=wrong_predecessor.run_id,
                            revision=Present(
                                head=wrong_predecessor.head,
                                fingerprint=wrong_predecessor.digest(),
                            ),
                        ),
                        "current_run_bytes": wrong_predecessor.canonical_bytes(),
                    }
                )
                run_mismatches = original_mismatch_check(
                    runtime_arg,
                    changed_run,
                    accepted_outcome,
                    history,
                    original_obligation,
                )
                assert run_mismatches == (
                    "current_run_head",
                    "current_run_fingerprint",
                    "current_run_bytes",
                )
                exercised_mismatch_negatives = True
            return mismatches

        monkeypatch.setattr(
            selected_source,
            "_accepted_open_cut_mismatches",
            verify_suspension_cut_exception,
        )

        v3_record = await publish_native_sealed_accounting_v3(runtime, seal_ref)
        assert exercised_mismatch_negatives
        assert len(v3_record.result.complete_ordered_calls) == 1
        assert v3_record.result.complete_ordered_calls[0].disposition.kind == "RECOVERY_REQUIRED"
        assert (
            v3_record.result.complete_ordered_calls[0].disposition.recovered_outcome.outcome.kind
            == "ABSENT"
        )
        assert len(resources.require_original_provider().transfers) == 1
    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        manifest=R14_EXECUTION_NATIVE_SEALED_ACCOUNTING_V39_PRODUCTION_MANIFEST,
        recovery_cut_source=NativeRecoveryCutSource(),
    ) as reopened:
        assert reopened.read_selected_execution_suspension() == suspension
        from chiplog.composition.r14_loop_history import (
            read_selected_native_sealed_accounting_v3,
        )

        with read_connection(reopened._database) as connection:
            before_v3_replay = connection.execute(
                "SELECT head FROM tenant_heads WHERE tenant_id=?", (reopened._tenant_id,)
            ).fetchone()[0]
        assert (
            read_selected_native_sealed_accounting_v3(reopened, seal_ref.revision.fingerprint)
            == v3_record
        )
        with read_connection(reopened._database) as connection:
            after_v3_replay = connection.execute(
                "SELECT head FROM tenant_heads WHERE tenant_id=?", (reopened._tenant_id,)
            ).fetchone()[0]
        assert after_v3_replay == before_v3_replay
        selected_suspension = reopened.read_selected_execution_suspension()
        assert selected_suspension is not None
        assert selected_suspension.baseline.original_obligations == (
            joined.calls[0].disposition.obligation,
        )
        assert len(resources.require_original_provider().transfers) == 1
        evidence_append = await reopened.observe_dispatch_outcome(intent_id)
        assert evidence_append.complete_records
        updated_outcomes = [
            DispatchOutcomeRecordV2.model_validate_json(record.canonical_bytes)
            for decision in reopened._owner_decisions().snapshot().decisions
            for record in decision.prepared.request.complete_records
            if record.schema_id == "chiplog.effects.dispatch-outcome-record.v2"
        ]
        assert len(updated_outcomes) == 2
        assert updated_outcomes[-1].snapshot.state == "CONFIRMED"
        assert updated_outcomes[-1].snapshot.obligation.state == "OPEN"
        assert updated_outcomes[-1].snapshot.evidence[-1].observation == "PROVIDER_RECEIPT"
        projection = R14OriginalNativePrepareSource(reopened).collect(seal_ref)
        assert projection is not None
        source = R14NativeSelectedAccountingSource(
            reopened,
            projection,
            bind_native_execution_preparation_v2(projection),
        )
        reopened_facts = source.collect_one_accepted_open_outcome(
            execution_run_head(sealed),
            expected_outcome_record_id=exact_open.outcome_record_id,
            expected_selected_decision=CallSubjectHead(
                subject_id=exact_open.selected_member.selected_decision.subject_id,
                revision=Present(
                    head=exact_open.selected_member.selected_decision.head,
                    fingerprint=exact_open.selected_member.selected_decision.fingerprint,
                ),
            ),
            expected_tenant_commit_sequence=exact_open.commit_sequence,
        )
        assert reopened_facts is not None
        assert reopened_facts.outcome == exact_open.outcome
        assert reopened_facts.latest_outcome == exact_open.outcome
        current_facts = source.collect_one_accepted_open_outcome(execution_run_head(sealed))
        assert current_facts is not None
        assert current_facts.outcome == exact_open.outcome
        assert current_facts.latest_outcome.snapshot.state == "CONFIRMED"
        assert current_facts.latest_outcome.snapshot.obligation.state == "OPEN"
        assert current_facts.latest_outcome == updated_outcomes[-1]
        assert (
            selected_recovery.accounting.selected_outcome_bytes
            == open_outcomes[0].canonical_bytes()
        )
        assert selected_recovery.accounting.selected_decision.subject_id == (
            exact_open.selected_member.selected_decision.subject_id
        )
        assert selected_recovery.accounting.selected_decision.revision.head == (
            exact_open.selected_member.selected_decision.head
        )
        assert selected_recovery.accounting.selected_decision.revision.fingerprint == (
            exact_open.selected_member.selected_decision.fingerprint
        )
        assert selected_recovery.accounting.batch_record_ids == exact_open.batch_record_ids
        assert reopened.read_selected_execution_suspension() == suspension
        recovered_frontier = reopened.read_selected_accepted_open_frontier()
        assert len(recovered_frontier.calls) == 1
        assert recovered_frontier.calls[0].disposition.kind == "RECOVERY_REQUIRED"
        assert recovered_frontier.calls[0].disposition.obligation == (
            selected_recovery.opening.original
        )
        assert recovered_frontier.calls[0].disposition.recovered_outcome.outcome.kind == "ABSENT"
        with pytest.raises(LoopRejected, match="OUTCOME_UNKNOWN/OPEN"):
            await reopened.start_next_execution_turn(sealed.run_id, sealed.head)
        new_request = await admit_runtime_slot(
            reopened,
            "open-obligation-successor",
            b"start a different follow-up task",
        )
        history_before_successor = read_execution_call_history(reopened)
        held = await reopened.drive_input(new_request)
        assert isinstance(held, ExecutionDriverRejectedV1)
        assert held.code == "HOLD", held.reason
        assert "recovery of original obligations" in held.reason
        assert read_execution_call_history(reopened) == history_before_successor
        assert not any(
            json.loads(raw).get("operation_kind") == "agent_loop.execution-successor.v1"
            for _, _, raw in reopened._loop_decisions().entries()
        )
        assert len(resources.require_original_provider().transfers) == 1
    with sqlite3.connect(database) as connection:
        record_id, canonical = connection.execute(
            "SELECT r.record_id, r.canonical_bytes FROM publications AS p "
            "JOIN records AS r ON r.tenant_id=p.tenant_id "
            "AND instr(char(10)||p.record_ids||char(10),char(10)||r.record_id||char(10))>0 "
            "WHERE p.operation_kind=? AND r.schema_id=?",
            (
                "agent_loop.accepted-open-recovery.v1",
                "chiplog.execution.accepted-open-terminal-disposition.v1",
            ),
        ).fetchone()
        damaged = canonical[:-1] + (b" " if canonical[-1:] != b" " else b"\n")
        assert (
            connection.execute(
                "UPDATE records SET canonical_bytes=? WHERE record_id=?", (damaged, record_id)
            ).rowcount
            == 1
        )
    with read_connection(database) as connection:
        before = capture_authority_snapshot_commitment(connection, "hermetic-tenant")
    recover_calls = []
    original_recover = CommonCliExecutionRuntime._recover_exact

    async def count_recover(self, *args, **kwargs):
        recover_calls.append(args[0])
        return await original_recover(self, *args, **kwargs)

    monkeypatch.setattr(CommonCliExecutionRuntime, "_recover_exact", count_recover)
    with pytest.raises((ValueError, RuntimeError)):
        async with open_common_cli_execution_runtime(
            database,
            resources=resources,
            manifest=R14_EXECUTION_ACCEPTED_OPEN_V29_PRODUCTION_MANIFEST,
        ):
            pytest.fail("corrupt accepted/open physical record reopened")
    assert recover_calls == []
    with read_connection(database) as connection:
        assert capture_authority_snapshot_commitment(connection, "hermetic-tenant") == before
