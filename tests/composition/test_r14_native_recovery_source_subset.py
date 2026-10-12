"""Mounted witness for the bounded, non-complete native recovery source bridge."""

from pathlib import Path

import pytest

from chiplog.adapters.driven.effects_hermetic import HermeticResponseLost
from chiplog.architecture.r7_runtime import (
    R14_EXECUTION_OBLIGATION_RESUME_V37_PRODUCTION_MANIFEST,
    R14_R17_H1_NATIVE_ONE_CALL_ACCOUNTING_V24_PRODUCTION_MANIFEST,
)
from chiplog.capabilities.effects.dispatch_outcome_contracts import DispatchOutcomeRecordV2
from chiplog.composition.common_cli_execution_runtime import open_common_cli_execution_runtime
from chiplog.composition.r14_call_acceptance_port import (
    CallAcceptanceAdoption,
    CallAcceptanceTarget,
)
from chiplog.composition.r14_call_preview import execution_run_head
from chiplog.composition.r14_loop_history import read_execution_call_history
from chiplog.composition.r14_native_recovery_cut import NativeRecoveryCutSource
from chiplog.composition.r14_native_recovery_source_subset import (
    R14NativeRecoverySourceSubsetReader,
)
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform._owner_publication_contracts import JournalSelectedPublication
from chiplog.platform.workspace_snapshot import read_connection
from tests.support.h1_cli_execution import admit_runtime_slot
from tests.support.native_cancelled import native_response, selected_native_cancelled


@pytest.mark.asyncio
async def test_source_subset_requires_selected_accounting_and_reopens(tmp_path: Path) -> None:
    database = tmp_path / "native-recovery-sources.sqlite"
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "custody"
    )
    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        responses=(native_response(),),
        manifest=R14_R17_H1_NATIVE_ONE_CALL_ACCOUNTING_V24_PRODUCTION_MANIFEST,
    ) as runtime:
        await selected_native_cancelled(runtime, bound=False)
        assert R14NativeRecoverySourceSubsetReader(runtime).collect() is None
        accounting = await runtime.publish_native_one_call_cancelled_accounting()
        subset = R14NativeRecoverySourceSubsetReader(runtime).collect()
        assert subset is not None
        assert subset.selected_accounting == accounting
        assert tuple(source.schema_id for source in subset.execution_sources)[-4:] == (
            "chiplog.call.response-seal.v1",
            "chiplog.call.initialized.v1",
            "chiplog.call.cancelled-before-accept.v1",
            "chiplog.call.not-executed-result.v1",
        )
        physical_ids = {source.physical_record.subject_id for source in subset.execution_sources}
        assert len(physical_ids) == len(subset.execution_sources)
    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        manifest=R14_R17_H1_NATIVE_ONE_CALL_ACCOUNTING_V24_PRODUCTION_MANIFEST,
    ) as reopened:
        assert R14NativeRecoverySourceSubsetReader(reopened).collect() == subset


@pytest.mark.asyncio
async def test_source_subset_reopens_exact_accepted_closed_outcome_without_resend(
    tmp_path: Path,
) -> None:
    database = tmp_path / "native-recovery-accepted-sources.sqlite"
    resources = HermeticDispatchResources(
        scenarios=("LOST_RESPONSE_AFTER_EFFECT",), cap=1, custody_path=tmp_path / "custody"
    )
    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        responses=(native_response(),),
        manifest=R14_EXECUTION_OBLIGATION_RESUME_V37_PRODUCTION_MANIFEST,
        recovery_cut_source=NativeRecoveryCutSource(),
    ) as runtime:
        await runtime.publish_native_recovery_profile(version="v5")
        wire = await admit_runtime_slot(runtime, "native-accepted-recovery")
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
                act_id="accept-native-recovery", preview_bytes=preview.canonical_bytes()
            ),
        )
        intent_id = accepted.external_intent.subject_id
        await runtime.authorize_dispatch(
            "hermetic-ingress", intent_id, "authorize-native-recovery", sealed.run_id
        )
        await runtime.commit_first_send(
            "hermetic-ingress", intent_id, "send-native-recovery", sealed.run_id
        )
        with pytest.raises(HermeticResponseLost):
            await runtime.emit_committed("hermetic-ingress", intent_id)
        await runtime.publish_accepted_open_recovery()
        suspension = await runtime.suspend_execution(sealed.run_id, sealed.head, ())
        suspended_run_head = suspension.pair.suspended_run
        await runtime.observe_dispatch_outcome(intent_id)
        closed_publication = await runtime.resolve_dispatch_outcome(
            "hermetic-ingress", intent_id, "close-native-recovery"
        )
        assert isinstance(closed_publication, JournalSelectedPublication), closed_publication
        closed = DispatchOutcomeRecordV2.model_validate_json(
            closed_publication.complete_records[0].canonical_bytes
        )
        assert closed.command.operation == "RESOLVE_OBLIGATION"
        assert closed.snapshot.state == "CONFIRMED"
        assert closed.snapshot.obligation.state == "CLOSED"
        with read_connection(database) as connection:
            stored = connection.execute(
                "SELECT p.commit_sequence, p.record_ids, r.canonical_bytes "
                "FROM publications AS p JOIN records AS r "
                "ON r.tenant_id=p.tenant_id AND r.record_id=? "
                "WHERE p.tenant_id=? AND p.idempotency_key=?",
                (closed.record.head, runtime._tenant_id, closed_publication.command_id),
            ).fetchone()
        assert stored is not None
        assert stored[0] == closed_publication.tenant_commit_sequence
        assert closed.record.head in stored[1].splitlines()
        assert stored[2] == closed.canonical_bytes()

        subset = R14NativeRecoverySourceSubsetReader(runtime).collect()
        assert subset is not None
        assert subset.selected_accounting is None
        assert subset.accepted_closed_outcome is not None
        selected_suspended_run = subset.authenticated_execution_snapshot.records[-1]
        assert selected_suspended_run.state == "SUSPENDED"
        assert execution_run_head(selected_suspended_run) == suspended_run_head
        assert (
            subset.accepted_closed_outcome.accepted.source.target_run_lineage[-1]
            == suspended_run_head
        )
        assert subset.authenticated_acceptances == (
            subset.accepted_closed_outcome.accepted.acceptance,
        )
        assert subset.accepted_closed_outcome.accepted.original_call_id == calls[0].original_call_id
        effect_sources = subset.accepted_effect_sources
        assert effect_sources == (
            *subset.accepted_closed_outcome.complete_ordered_dispatch_sources,
            *subset.accepted_closed_outcome.complete_ordered_evidence_sources,
        )
        assert effect_sources
        source_by_id = {}
        for source in effect_sources:
            identity = source.physical_record.subject_id
            previous = source_by_id.get(identity)
            assert previous is None or (
                previous.owner,
                previous.schema_id,
                previous.canonical_record_bytes,
                previous.physical_record,
            ) == (
                source.owner,
                source.schema_id,
                source.canonical_record_bytes,
                source.physical_record,
            )
            source_by_id[identity] = source
        assert len(resources.require_original_provider().transfers) == 1

    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        responses=(native_response(),),
        manifest=R14_EXECUTION_OBLIGATION_RESUME_V37_PRODUCTION_MANIFEST,
        recovery_cut_source=NativeRecoveryCutSource(),
    ) as reopened:
        assert R14NativeRecoverySourceSubsetReader(reopened).collect() == subset
        assert len(resources.require_original_provider().transfers) == 1
