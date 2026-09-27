"""Mounted RED contract for the H1 V2 preseal P/E recovery anchor.

The direct V3 Prepare -> V2 seal path is deliberately used here.  No test
supplies a delivery, fence, member, policy, or worker DTO: the installed
owners must capture and bind their own facts before the native V2 decision.
"""

from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path

import pytest

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.delivery_preparation import (
    Commentary,
    DeliveryCompletion,
    ProposedDelivery,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.composition.common_cli_execution_runtime import (
    CommonCliExecutionRuntime,
    open_installed_h1_runtime,
)
from chiplog.composition.common_execution_driver_contracts import (
    DriveInputRequestV1,
    SelectedExecutionReceiptV1,
)
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.h1_selected_prepare import select_h1_v3_prepare_for_candidate
from chiplog.composition.h1_v2_recovery_native_source import H1V2RecoveryNativeSource
from chiplog.composition.r14_execution_complete_seal_records import (
    ExecutionCompleteSealPhysicalEnvelopeV2,
    RetainedExecutionCompleteSealV2,
    build_complete_seal_envelope,
)
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from tests.support.h1_cli_execution import _admit
from tests.support.h1_installed_launch import TENANT, installed_slot, prepare_installed_slot


def _resources(tmp_path: Path) -> HermeticDispatchResources:
    return HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )


def _decision_text(decision: dict[str, object], field: str) -> str:
    value = decision.get(field)
    if not isinstance(value, str):
        raise AssertionError(f"selected DECIDED lacks text {field}")
    return value


async def _direct_v3_prepare_then_v2_seal(
    runtime: CommonCliExecutionRuntime,
) -> tuple[DriveInputRequestV1, CallSubjectHead, str, bytes, dict[str, object]]:
    """Make the native cut without prebuilding any P/E recovery input."""
    request = await _admit(runtime)
    initial = await runtime.drive_input(request)
    assert isinstance(initial, SelectedExecutionReceiptV1)
    runtime._execution_model._responses = (
        DeliveryCompletion(
            tenant=TENANT,
            run_id=initial.stable_run_lineage_id,
            turn_id=initial.stable_run_lineage_id + "/turn/1",
            deliveries=(ProposedDelivery(payload=(Commentary(text="preseal P/E anchor"),)),),
        ).canonical_bytes(),
    )
    started = await runtime.begin_execution(
        "hermetic-ingress", initial.stable_run_lineage_id, initial.selected_run_head.head
    )
    captured = await runtime.capture_execution(
        "hermetic-ingress", initial.stable_run_lineage_id, started.head
    )
    selected_prepare = select_h1_v3_prepare_for_candidate(
        runtime, captured, expected_head=captured.head
    )
    assert selected_prepare.decision_id
    sealed = await runtime.seal_execution_complete(
        "hermetic-ingress", initial.stable_run_lineage_id, captured.head, profile="H1_V2"
    )
    decision_id, _predecessor, raw = next(
        entry
        for entry in runtime._loop_decisions().entries()
        if json.loads(entry[2]).get("operation_id") == sealed.head
    )
    decision = json.loads(raw)
    assert isinstance(decision, dict)
    retained = RetainedExecutionCompleteSealV2.model_validate_json(
        _decision_text(decision, "execution_complete_seal")
    )
    response_seal = retained.exchange.proposal.fan_out.response_seal
    locator = CallSubjectHead(
        subject_id=response_seal.response_seal_id,
        revision=Present(
            head="record:" + response_seal.digest(), fingerprint=response_seal.digest()
        ),
    )
    return request, locator, decision_id, raw, decision


@pytest.mark.asyncio
async def test_current_installed_v2_seal_has_no_preseal_pe_anchor_or_extra_member(
    tmp_path: Path,
) -> None:
    """Regression witness for the precise gap that the new anchor must close."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            request, locator, decision_id, raw, decision = await _direct_v3_prepare_then_v2_seal(
                runtime
            )

            # Raw journal shape is insufficient authority.  The installed V2
            # reader independently authenticates and selects this exact entry.
            native = H1V2RecoveryNativeSource(runtime).select(
                original_identity=request.identity,
                original_fingerprint=request.original_driver_command_fingerprint(),
                selected_seal=locator,
            )
            assert native.seal.decision_id == decision_id
            assert native.seal.decision_fingerprint == hashlib.sha256(raw).hexdigest()

            retained = RetainedExecutionCompleteSealV2.model_validate_json(
                _decision_text(decision, "execution_complete_seal")
            )
            envelope = ExecutionCompleteSealPhysicalEnvelopeV2.model_validate_json(
                _decision_text(decision, "execution_complete_seal_envelope")
            )
            assert envelope == build_complete_seal_envelope(retained)
            assert len(envelope.records) == 3
            assert tuple(member.record_id for member in envelope.records) == (
                retained.exchange.proposal.sealed_run.head,
                "record:" + retained.exchange.proposal.fan_out.response_seal.digest(),
                envelope.records[2].record_id,
            )
            assert "h1_preseal_pe_anchor" not in decision
            journal = runtime._h1_delivery_evidence_journal
            assert journal is not None
            assert journal._entries() == ()


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason=(
        "the installed V2 seal has no pre-decision P/E capture-and-bind seam and retains "
        "neither the versioned sibling anchor nor historical P/E projection"
    ),
)
async def test_installed_v3_prepare_v2_seal_retains_and_reopens_authenticated_preseal_pe_anchor(
    tmp_path: Path,
) -> None:
    """Minimal positive: anchor in the authenticated decision, then restart P/E replay."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            request, locator, decision_id, raw, decision = await _direct_v3_prepare_then_v2_seal(
                runtime
            )
            native = H1V2RecoveryNativeSource(runtime).select(
                original_identity=request.identity,
                original_fingerprint=request.original_driver_command_fingerprint(),
                selected_seal=locator,
            )
            assert native.seal.decision_id == decision_id
            assert native.seal.decision_fingerprint == hashlib.sha256(raw).hexdigest()
            anchor = decision["h1_preseal_pe_anchor"]
            assert isinstance(anchor, dict)
            assert anchor["version"] == 1

            retained = RetainedExecutionCompleteSealV2.model_validate_json(
                _decision_text(decision, "execution_complete_seal")
            )
            envelope = ExecutionCompleteSealPhysicalEnvelopeV2.model_validate_json(
                _decision_text(decision, "execution_complete_seal_envelope")
            )
            assert envelope == build_complete_seal_envelope(retained)
            assert len(envelope.records) == 3

        source_module = importlib.import_module(
            "chiplog.composition.h1_recovery_historical_pe_source"
        )
        source_type = source_module.H1RecoveryHistoricalPESource
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as reopened:
            source = source_type(reopened)
            issued = source.issue_completion_projection(
                original_identity=request.identity,
                original_fingerprint=request.original_driver_command_fingerprint(),
                selected_seal=locator,
            )
            reconstructed = source.reconstruct_completion_input(issued)
            assert reconstructed.delivery.captured_response == reconstructed.exact_captured_response
            assert reconstructed.fence.run_id == reconstructed.run.run_id
            assert reconstructed.fence.run_head == reconstructed.run.head
            assert reconstructed.delivery.worker_fence.fingerprint


@pytest.mark.asyncio
@pytest.mark.parametrize("changed_owner_fact", ("p_scope_policy", "p_custody", "e_worker_route"))
@pytest.mark.xfail(
    strict=True,
    reason=(
        "no installed preseal admission capability captures P/E then rechecks owner state under "
        "the decision gate; the required authoritative mutation boundary is absent"
    ),
)
async def test_preseal_anchor_denies_owner_change_before_v2_decision(
    tmp_path: Path, changed_owner_fact: str
) -> None:
    """P/E's own admission seam, not a caller DTO, supplies the stale-state denial."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    preseal = importlib.import_module("chiplog.composition.h1_preseal_pe_anchor")
    admission_type = preseal.H1PresealPEAnchorAdmission
    error_type = preseal.H1PresealPEAnchorAdmissionError
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            admission = admission_type(runtime)
            prepared = await admission.prepare_direct_v3_prepare_v2_seal()
            await admission.change_authoritative_owner_fact(prepared, changed_owner_fact)
            with pytest.raises(error_type, match=r"changed|stale|current"):
                await admission.seal_prepared_v2(prepared)
