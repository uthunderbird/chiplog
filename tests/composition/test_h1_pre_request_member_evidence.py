"""Mounted witnesses for the private H1 native-member evidence issuer."""

from __future__ import annotations

import json
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
from chiplog.composition.common_cli_execution_runtime import open_installed_h1_runtime
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.h1_native_member_sources import H1CurrentNativeMemberSourceCut
from chiplog.composition.h1_pre_request_member_evidence import (
    H1PreRequestMemberEvidence,
    H1PreRequestMemberEvidenceReceipt,
)
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
        deliveries=(ProposedDelivery(payload=(Commentary(text="member issuer"),)),),
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
    first = runtime._h1_first_path_sources.capture_current(
        original_identity=request.identity,
        original_fingerprint=request.original_driver_command_fingerprint(),
        selected_seal=CallSubjectHead(
            subject_id=response_seal.response_seal_id,
            revision=Present(
                head="record:" + response_seal.digest(), fingerprint=response_seal.digest()
            ),
        ),
    )
    return original, runtime._h1_native_member_sources.capture_current(first)


@pytest.mark.asyncio
async def test_installed_member_issuer_retains_complete_ordered_native_vector(
    tmp_path: Path,
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )
    issuer: H1PreRequestMemberEvidence
    receipt: H1PreRequestMemberEvidenceReceipt
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=resources) as runtime:
            original, native = await _original_and_native(runtime)
            port = runtime._h1_preissuance_registration_source_port
            assert port is not None
            capability = await port._capture_completion_scope(native._capture, native, original)
            issuer = cast(H1PreRequestMemberEvidence, runtime._h1_pre_request_member_evidence)
            assert type(issuer) is H1PreRequestMemberEvidence
            assert issuer._journal is runtime._h1_delivery_evidence_journal
            assert issuer._native_sources is runtime._h1_native_member_sources
            assert issuer._port is port

            before = runtime._h1_delivery_evidence_journal._entries()
            receipt = issuer._issue_members(native, capability)
            replayed = issuer._replay_members(receipt)

            assert len(replayed) == len(native._occurrences)
            assert [record._value["member_index"] for _, record, _ in replayed] == list(
                range(len(replayed))
            )
            assert len(runtime._h1_delivery_evidence_journal._entries()) == (
                len(before) + len(replayed)
            )
            assert issuer._replay_members(issuer._issue_members(native, capability)) == replayed

        assert not hasattr(runtime, "_h1_pre_request_member_evidence")
        with pytest.raises(ValueError, match=r"closed|issuer-owned"):
            issuer._replay_members(receipt)


def test_member_issuer_requires_canonical_installed_dependencies() -> None:
    with pytest.raises(TypeError, match="canonical"):
        H1PreRequestMemberEvidence(cast(Any, object()), cast(Any, object()), cast(Any, object()))
