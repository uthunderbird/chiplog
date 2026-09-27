"""Reachability of the installed B preparation path from a genuine H1 V2 seal."""

from __future__ import annotations

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
from chiplog.composition.common_cli_execution_runtime import open_installed_h1_runtime
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.h1_selected_prepare import select_h1_v3_prepare_for_candidate
from chiplog.composition.r14_execution_complete_seal_records import RetainedExecutionCompleteSealV2
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from tests.support.h1_cli_execution import _admit
from tests.support.h1_installed_launch import TENANT, installed_slot, prepare_installed_slot


@pytest.mark.asyncio
async def test_installed_b_reaches_four_owners_from_genuine_h1_v2_seal(tmp_path: Path) -> None:
    """A native V2 seal can reach B, then each installed completion owner."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=resources) as runtime:
            request = await _admit(runtime)
            initial = await runtime.drive_input(request)
            runtime._execution_model._responses = (
                DeliveryCompletion(
                    tenant=TENANT,
                    run_id=initial.stable_run_lineage_id,
                    turn_id=initial.stable_run_lineage_id + "/turn/1",
                    deliveries=(ProposedDelivery(payload=(Commentary(text="V2 B reachability"),)),),
                ).canonical_bytes(),
            )
            started = await runtime.begin_execution(
                "hermetic-ingress", initial.stable_run_lineage_id, initial.selected_run_head.head
            )
            captured = await runtime.capture_execution(
                "hermetic-ingress", initial.stable_run_lineage_id, started.head
            )
            select_h1_v3_prepare_for_candidate(runtime, captured, expected_head=captured.head)
            sealed = await runtime.seal_execution_complete(
                "hermetic-ingress", initial.stable_run_lineage_id, captured.head, profile="H1_V2"
            )
            decision = next(
                json.loads(raw)
                for _, _, raw in runtime._loop_decisions().entries()
                if json.loads(raw).get("operation_id") == sealed.head
            )
            retained = RetainedExecutionCompleteSealV2.model_validate_json(
                decision["execution_complete_seal"]
            )
            assert retained.profile == "H1_V2"
            response_seal = retained.exchange.proposal.fan_out.response_seal
            seal = CallSubjectHead(
                subject_id=response_seal.response_seal_id,
                revision=Present(
                    head="record:" + response_seal.digest(), fingerprint=response_seal.digest()
                ),
            )

            enrollment = runtime._h1_live_completion_enrollment
            assert enrollment is not None
            session = enrollment._open_session()
            assert (
                session.capture_first_path(
                    original_identity=request.identity,
                    original_fingerprint=request.original_driver_command_fingerprint(),
                    selected_seal=seal,
                ).source.selected_response_seal
                == seal
            )

            completion = await session.prepare_first_path_completion()
            preflight = session._preflight
            assert preflight is not None
            assert completion.role == "completion"
            assert completion.sent.operation_id == "agent_loop.prepare_first_path_completion"
            assert completion.sent.schema_id == preflight.request.schema_id
            assert completion.sent.canonical_payload == preflight.request.canonical_bytes()

            session.capture_current(
                original_identity=request.identity,
                original_fingerprint=request.original_driver_command_fingerprint(),
                selected_seal=seal,
                completion_exchange=completion,
            )
            conversation = await session.prepare_conversation_completion()
            effects = await session.prepare_local_commentary()
            terminal = await session.prepare_terminal_work()
            exchanges = (completion, conversation, effects, terminal)
            assert tuple(exchange.role for exchange in exchanges) == (
                "completion",
                "conversation",
                "effects",
                "terminal_work",
            )
