"""Shared builders extracted without changing their behavior."""

from __future__ import annotations

import json
from pathlib import Path

from tests.support.h1_cli_execution import _admit
from tests.support.h1_installed_launch import TENANT

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.delivery_preparation import (
    Commentary,
    DeliveryCompletion,
    ProposedDelivery,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.composition.common_cli_execution_runtime import (
    CommonCliExecutionRuntime,
)
from chiplog.composition.common_execution_driver_contracts import (
    DriveInputRequestV1,
    SelectedExecutionReceiptV1,
)
from chiplog.composition.h1_selected_prepare import select_h1_v3_prepare_for_candidate
from chiplog.composition.r14_execution_complete_seal_records import (
    RetainedExecutionCompleteSealV2,
)
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources


def _resources(tmp_path: Path, label: str = "") -> HermeticDispatchResources:
    return HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / f"dispatch-custody{label}"
    )


async def _seal_v3_prepare_as_v2(
    runtime: CommonCliExecutionRuntime,
) -> tuple[DriveInputRequestV1, CallSubjectHead]:
    request = await _admit(runtime)
    initial = await runtime.drive_input(request)
    assert isinstance(initial, SelectedExecutionReceiptV1)
    runtime._execution_model._responses = (
        DeliveryCompletion(
            tenant=TENANT,
            run_id=initial.stable_run_lineage_id,
            turn_id=initial.stable_run_lineage_id + "/turn/1",
            deliveries=(ProposedDelivery(payload=(Commentary(text="historical P/E"),)),),
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
    raw = next(
        raw
        for _, _, raw in runtime._loop_decisions().entries()
        if json.loads(raw).get("operation_id") == sealed.head
    )
    retained = RetainedExecutionCompleteSealV2.model_validate_json(
        json.loads(raw)["execution_complete_seal"]
    )
    response_seal = retained.exchange.proposal.fan_out.response_seal
    return request, CallSubjectHead(
        subject_id=response_seal.response_seal_id,
        revision=Present(
            head="record:" + response_seal.digest(), fingerprint=response_seal.digest()
        ),
    )


def _selected_seal_decision(
    runtime: CommonCliExecutionRuntime, selected_seal: CallSubjectHead
) -> tuple[str, bytes, dict[str, object]]:
    for decision_id, _previous, raw in runtime._loop_decisions().entries():
        decision = json.loads(raw)
        if not isinstance(decision, dict):
            continue
        retained_raw = decision.get("execution_complete_seal")
        if not isinstance(retained_raw, str):
            continue
        retained = RetainedExecutionCompleteSealV2.model_validate_json(retained_raw)
        response_seal = retained.exchange.proposal.fan_out.response_seal
        if (
            response_seal.response_seal_id == selected_seal.subject_id
            and response_seal.digest() == selected_seal.revision.fingerprint
        ):
            return decision_id, raw, decision
    raise AssertionError("selected V2 seal has no DECIDED entry")
