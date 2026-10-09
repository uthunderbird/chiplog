"""Mounted R14 resolver selects the exact closed R16 result and reopens it."""

from pathlib import Path

import pytest

from chiplog.adapters.driven.effects_hermetic import HermeticResponseLost
from chiplog.architecture.r7_runtime import (
    R14_EXECUTION_ORIGINAL_CALL_RESOLUTION_V33_PRODUCTION_MANIFEST,
)
from chiplog.capabilities.agent_loop.original_recovery_contracts import (
    PreparedOriginalCallResolution,
)
from chiplog.capabilities.effects.dispatch_outcome_contracts import DispatchOutcomeRecordV2
from chiplog.composition.common_cli_execution_runtime import (
    open_common_cli_execution_runtime,
)
from chiplog.composition.r14_call_acceptance_port import (
    CallAcceptanceAdoption,
    CallAcceptanceTarget,
)
from chiplog.composition.r14_call_preview import execution_run_head
from chiplog.composition.r14_loop_history import read_execution_call_history
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform._owner_publication_contracts import JournalSelectedPublication
from tests.support.h1_cli_execution import admit_runtime_slot
from tests.support.native_cancelled import native_response


@pytest.mark.asyncio
async def test_original_resolution_publishes_closed_outcome_and_reopens_without_resend(
    tmp_path: Path,
) -> None:
    database = tmp_path / "r14-original-resolution.sqlite"
    custody = tmp_path / "custody"
    resources = HermeticDispatchResources(
        scenarios=("LOST_RESPONSE_AFTER_EFFECT",), cap=1, custody_path=custody
    )
    manifest = R14_EXECUTION_ORIGINAL_CALL_RESOLUTION_V33_PRODUCTION_MANIFEST

    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        responses=(native_response(),),
        manifest=manifest,
    ) as runtime:
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
        assert len(resources.require_original_provider().transfers) == 1

    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        responses=(native_response(),),
        manifest=manifest,
    ) as reopened:
        assert reopened.read_selected_original_resolver_authority_policy() == policy
        assert reopened.read_selected_original_resolver_invocation_source()[0] == invocation
        assert reopened.read_selected_original_call_resolution() == resolution
        assert len(resources.require_original_provider().transfers) == 1
