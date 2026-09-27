"""Mounted source-only witnesses for the P-owned H1 completion-scope capability."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.delivery_preparation import (
    Commentary,
    DeliveryCompletion,
    ProposedDelivery,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.effects.fences import NonSchedulerFence as EffectsNonSchedulerFence
from chiplog.composition.common_cli_execution_runtime import open_installed_h1_runtime
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.h1_native_member_sources import H1CurrentNativeMemberSourceCut
from chiplog.composition.h1_preissuance_registration import H1PreissuanceSourceViolation
from chiplog.composition.h1_selected_prepare import select_h1_v3_prepare_for_candidate
from chiplog.composition.r14_execution_complete_seal_records import RetainedExecutionCompleteSealV3
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from tests.support.h1_cli_execution import _admit
from tests.support.h1_installed_launch import TENANT, installed_slot, prepare_installed_slot


async def _original_and_native(runtime: Any) -> tuple[object, H1CurrentNativeMemberSourceCut]:
    request = await _admit(runtime)
    initial = await runtime.drive_input(request)
    completion = DeliveryCompletion(
        tenant=TENANT,
        run_id=initial.stable_run_lineage_id,
        turn_id=initial.stable_run_lineage_id + "/turn/1",
        deliveries=(ProposedDelivery(payload=(Commentary(text="mounted completion scope"),)),),
    )
    runtime._execution_model._responses = (completion.canonical_bytes(),)
    started = await runtime.begin_execution(
        "hermetic-ingress", initial.stable_run_lineage_id, initial.selected_run_head.head
    )
    captured = await runtime.capture_execution(
        "hermetic-ingress", initial.stable_run_lineage_id, started.head
    )
    selected = select_h1_v3_prepare_for_candidate(runtime, captured, expected_head=captured.head)
    port = runtime._h1_preissuance_registration_source_port
    assert port is not None
    original = await port.verify_selected_original(selected)
    sealed = await runtime.seal_execution_complete(
        "hermetic-ingress", initial.stable_run_lineage_id, captured.head, profile="H1_V3"
    )
    decision = next(
        json.loads(raw)
        for _, _, raw in runtime._loop_decisions().entries()
        if json.loads(raw).get("operation_id") == sealed.head
    )
    retained = RetainedExecutionCompleteSealV3.model_validate_json(
        decision["execution_complete_seal"]
    )
    response_seal = retained.exchange.proposal.fan_out.response_seal
    first_path = runtime._h1_first_path_sources.capture_current(
        original_identity=request.identity,
        original_fingerprint=request.original_driver_command_fingerprint(),
        selected_seal=CallSubjectHead(
            subject_id=response_seal.response_seal_id,
            revision=Present(
                head="record:" + response_seal.digest(), fingerprint=response_seal.digest()
            ),
        ),
    )
    native = runtime._h1_native_member_sources.capture_current(first_path)
    return original, cast(H1CurrentNativeMemberSourceCut, native)


def _delivery_receipt(
    runtime: Any, native: H1CurrentNativeMemberSourceCut, scope_cap: object
) -> object:
    """Issue the one P receipt that binds this exact scope/native cut."""
    port = runtime._h1_preissuance_registration_source_port
    assert port is not None
    member_issuer = runtime._h1_pre_request_member_evidence
    worker_owner = runtime._h1_installed_worker_evidence_owner
    worker_issuer = runtime._h1_pre_request_worker_evidence
    member_receipt = member_issuer._issue_members(native, scope_cap)
    worker_cap = worker_owner._capture_for_pre_request(native)
    worker_receipt = worker_issuer._issue_worker(native, worker_cap)
    return port._prepare_delivery_inputs(
        native._capture, native, scope_cap, member_receipt, worker_receipt
    )


@pytest.mark.asyncio
async def test_installed_port_captures_and_replays_only_its_authenticated_completion_scope(
    tmp_path: Path,
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=resources) as runtime:
            original, native = await _original_and_native(runtime)
            port = runtime._h1_preissuance_registration_source_port
            assert port is not None

            capability = await port._capture_completion_scope(native._capture, native, original)
            projection = port._replay_completion_scope(capability, native)
            issue_wire, current_wire = port._replay_completion_scope_wires(capability, native)
            original_cut = port._original_cut(original)
            entry = launch.custody.select(
                projection.scope.tenant_id, projection.scope.principal_id, "hermetic-local"
            )

            assert (
                projection.scope.worker_session_id
                == native._native.source.complete_ordered_run_lineage[-1].worker_session
            )
            assert projection.scope.recipient.endpoint == projection.recipient.endpoint
            assert (
                projection.policy_bytes == projection.scope.disclosure_policy.canonical_source_bytes
            )
            assert projection.scope_ref == original_cut.cut.issued.scope_head
            assert projection.scope_bytes == projection.scope.canonical_bytes()
            assert projection.policy_ref == projection.scope.disclosure_policy.ref
            assert projection.custody_entry_generation == entry.generation
            assert (
                projection.custody_entry_digest
                == hashlib.sha256(entry.canonical_bytes()).hexdigest()
            )
            assert projection.source_signature_digest == (
                projection.scope.selected_resource_observation_ref.signed_observation_fingerprint
            )
            assert issue_wire is original_cut.cut.scope_issue_wire
            assert current_wire is port._completion_scopes[id(capability)][1].scope_current_wire
            assert current_wire.sent.request_id == current_wire.returned.request_id
            assert current_wire.sent_at_ns <= current_wire.returned_at_ns < (
                current_wire.sent.budget.absolute_deadline_ns
            )
            with pytest.raises(TypeError, match="cannot be copied"):
                copy.copy(capability)
            with pytest.raises(H1PreissuanceSourceViolation, match="capability"):
                port._replay_completion_scope(cast(Any, object()), native)
            with pytest.raises(H1PreissuanceSourceViolation, match="wire capability"):
                port._replay_completion_scope_wires(cast(Any, object()), native)

            native_sources = runtime._h1_native_member_sources
            assert native_sources is not None
            sibling = native_sources.capture_current(native._capture)
            with pytest.raises(H1PreissuanceSourceViolation, match="native"):
                port._replay_completion_scope(capability, sibling)
            with pytest.raises(H1PreissuanceSourceViolation, match="wire capability/native"):
                port._replay_completion_scope_wires(capability, sibling)
            with pytest.raises(H1PreissuanceSourceViolation, match="first-path"):
                await port._capture_completion_scope(cast(Any, object()), native, original)


@pytest.mark.asyncio
async def test_effects_source_replays_only_one_p_bound_scope_delivery_cut(tmp_path: Path) -> None:
    """The effects source is P-issued evidence, never a caller-built DTO."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=resources) as runtime:
            original, native = await _original_and_native(runtime)
            port = runtime._h1_preissuance_registration_source_port
            assert port is not None
            scope_cap = await port._capture_completion_scope(native._capture, native, original)
            receipt = _delivery_receipt(runtime, native, scope_cap)

            source = port._replay_completion_effects_source(
                scope_cap, native, receipt, native._capture
            )

            assert source.selected_scope.scope == port._replay_completion_scope(
                scope_cap, native
            ).scope
            assert source.fence.canonical_bytes() == port._replay_delivery_inputs(
                receipt, native._capture
            )[1].canonical_bytes()
            assert type(source.fence) is EffectsNonSchedulerFence
            assert (
                source.selected_scope.current_result.source_anchor
                == source.selected_scope.anchor
            )

            other_cap = await port._capture_completion_scope(native._capture, native, original)
            with pytest.raises(H1PreissuanceSourceViolation, match=r"delivery.*scope"):
                port._replay_completion_effects_source(
                    other_cap, native, receipt, native._capture
                )
            with pytest.raises(H1PreissuanceSourceViolation, match="receipt"):
                port._replay_completion_effects_source(
                    scope_cap, native, cast(Any, object()), native._capture
                )


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ("stale_physical", "current_wire"))
async def test_effects_source_rejects_stale_physical_or_substituted_current_wire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, defect: str
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=resources) as runtime:
            original, native = await _original_and_native(runtime)
            port = runtime._h1_preissuance_registration_source_port
            assert port is not None
            scope_cap = await port._capture_completion_scope(native._capture, native, original)
            receipt = _delivery_receipt(runtime, native, scope_cap)
            if defect == "stale_physical":
                monkeypatch.setattr(runtime._trust._materializer, "record", lambda *_: None)
                expected_error = "scope"
            else:
                issued, cut = port._completion_scopes[id(scope_cap)]
                bad_returned = cut.scope_current_wire.returned.model_copy(
                    update={"request_id": "substituted-current-wire"}
                )
                port._completion_scopes[id(scope_cap)] = (
                    issued,
                    replace(
                        cut,
                        scope_current_wire=replace(cut.scope_current_wire, returned=bad_returned),
                    ),
                )
                expected_error = "owner wire"

            with pytest.raises(H1PreissuanceSourceViolation, match=expected_error):
                port._replay_completion_effects_source(
                    scope_cap, native, receipt, native._capture
                )
