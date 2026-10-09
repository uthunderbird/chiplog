"""Mounted accepted-call accounting keeps unresolved R16 work on HOLD."""

import sqlite3
from pathlib import Path

import pytest

from chiplog.adapters.driven.effects_hermetic import HermeticResponseLost
from chiplog.architecture.r7_runtime import R14_EXECUTION_ACCEPTED_OPEN_V28_PRODUCTION_MANIFEST
from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.execution_recovery_contracts import (
    PrepareAcceptedOpenAccounting,
    PreparedAcceptedOpenAccounting,
)
from chiplog.capabilities.agent_loop.execution_recovery_preparation import (
    prepare_accepted_open_accounting,
)
from chiplog.capabilities.effects.dispatch_outcome_contracts import DispatchOutcomeRecordV2
from chiplog.composition.common_cli_execution_runtime import (
    CommonCliExecutionRuntime,
    open_common_cli_execution_runtime,
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
        manifest=R14_EXECUTION_ACCEPTED_OPEN_V28_PRODUCTION_MANIFEST,
    ) as runtime:
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
        response_seal = read_execution_recovery_history(runtime)[
            3
        ][0].proposal.fan_out.response_seal
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
        accounting_request = PrepareAcceptedOpenAccounting(
            command_id="accepted-open-accounting:" + open_cut.cut_fingerprint,
            cut=open_cut,
        )
        prepared = prepare_accepted_open_accounting(accounting_request)
        assert isinstance(prepared, PreparedAcceptedOpenAccounting), prepared
        assert prepared.accounting.kind == "RECOVERY_REQUIRED"
        assert prepared.accounting.selected_outcome_bytes == exact_open.outcome.canonical_bytes()

        selected_accounting = await runtime.publish_accepted_open_accounting()
        assert selected_accounting == prepared.accounting
        assert selected_accounting.kind == "RECOVERY_REQUIRED"
        assert selected_accounting.selected_outcome_bytes == exact_open.outcome.canonical_bytes()
        from chiplog.composition import r14_next_turn_publication

        async def reject_successor_prepare(*_args, **_kwargs):
            raise AssertionError("next-turn owner preparation must not run for OPEN obligation")

        monkeypatch.setattr(
            r14_next_turn_publication, "_exchange_next_turn", reject_successor_prepare
        )
        with pytest.raises(LoopRejected, match="OUTCOME_UNKNOWN/OPEN"):
            await runtime.start_next_execution_turn(sealed.run_id, sealed.head)
        assert len(resources.require_original_provider().transfers) == 1
    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        manifest=R14_EXECUTION_ACCEPTED_OPEN_V28_PRODUCTION_MANIFEST,
    ) as reopened:
        assert reopened.read_selected_accepted_open_accounting() == selected_accounting
        assert len(resources.require_original_provider().transfers) == 1
    with sqlite3.connect(database) as connection:
        record_id, canonical = connection.execute(
            "SELECT r.record_id, r.canonical_bytes FROM publications AS p "
            "JOIN records AS r ON r.tenant_id=p.tenant_id AND r.record_id=p.record_ids "
            "WHERE p.operation_kind=?",
            ("agent_loop.accepted-open-accounting.v1",),
        ).fetchone()
        damaged = canonical[:-1] + (b" " if canonical[-1:] != b" " else b"\n")
        assert connection.execute(
            "UPDATE records SET canonical_bytes=? WHERE record_id=?", (damaged, record_id)
        ).rowcount == 1
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
            manifest=R14_EXECUTION_ACCEPTED_OPEN_V28_PRODUCTION_MANIFEST,
        ):
            pytest.fail("corrupt accepted/open physical record reopened")
    assert recover_calls == []
    with read_connection(database) as connection:
        assert capture_authority_snapshot_commitment(connection, "hermetic-tenant") == before
