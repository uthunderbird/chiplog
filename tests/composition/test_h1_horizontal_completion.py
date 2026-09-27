"""Mounted H1 four-owner-call and final-scope witness."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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
from chiplog.composition.r14_execution_complete_seal_records import RetainedExecutionCompleteSealV3
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from tests.support.h1_cli_execution import _admit
from tests.support.h1_installed_launch import TENANT, installed_slot, prepare_installed_slot


async def _selected_native_v3_then_v2_source(
    runtime: Any,
) -> tuple[Any, Any, CallSubjectHead, Any, Any]:
    """Make the installed selected V3 seal and reopen its native V2 source."""
    request = await _admit(runtime)
    initial = await runtime.drive_input(request)
    completion = DeliveryCompletion(
        tenant=TENANT,
        run_id=initial.stable_run_lineage_id,
        turn_id=initial.stable_run_lineage_id + "/turn/1",
        deliveries=(ProposedDelivery(payload=(Commentary(text="horizontal H1 completion"),)),),
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
    seal = CallSubjectHead(
        subject_id=response_seal.response_seal_id,
        revision=Present(
            head="record:" + response_seal.digest(), fingerprint=response_seal.digest()
        ),
    )
    first_path = runtime._h1_first_path_sources.capture_current(
        original_identity=request.identity,
        original_fingerprint=request.original_driver_command_fingerprint(),
        selected_seal=seal,
    )
    native = runtime._h1_native_member_sources.capture_current(first_path)
    return request, original, seal, first_path, native


@pytest.mark.asyncio
async def test_installed_h1_horizontal_four_owner_calls_and_final_scope(
    tmp_path: Path,
) -> None:
    """Installed A/P/E sources produce four owner calls and a final P scope read."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=resources) as runtime:
            request, original, seal, first_path, native = await _selected_native_v3_then_v2_source(
                runtime
            )
            port = runtime._h1_preissuance_registration_source_port
            assert port is not None
            scope = await port._capture_completion_scope(first_path, native, original)
            projection = port._replay_completion_scope(scope, native)
            assert projection.scope.worker_session_id == (
                native._native.source.complete_ordered_run_lineage[-1].worker_session
            )

            worker_owner = runtime._h1_installed_worker_evidence_owner
            worker_issuer = runtime._h1_pre_request_worker_evidence
            evidence_journal = runtime._h1_delivery_evidence_journal
            first_path_sources = runtime._h1_first_path_sources
            assert worker_owner is not None
            assert worker_issuer is not None
            assert evidence_journal is not None
            assert first_path_sources is not None
            worker_capability = worker_owner._capture_for_pre_request(native)
            worker_receipt = worker_issuer._issue_worker(native, worker_capability)
            worker_locator, worker_record, _ = worker_issuer._replay_worker(worker_receipt)
            readback = evidence_journal.read_exact(worker_locator)
            assert readback.canonical_bytes() == worker_record.canonical_bytes()

            enrollment = runtime._h1_live_completion_enrollment
            assert enrollment is not None
            session = enrollment._open_session()
            assert session._sources is first_path_sources
            assert (
                session.capture_first_path(
                    original_identity=request.identity,
                    original_fingerprint=request.original_driver_command_fingerprint(),
                    selected_seal=seal,
                ).source
                == first_path.source
            )

            exchange = await session.prepare_first_path_completion()

            preflight = session._preflight
            assert preflight is not None
            assert exchange.role == "completion"
            assert exchange.sent.operation_id == "agent_loop.prepare_first_path_completion"
            assert exchange.sent.schema_id == preflight.request.schema_id
            assert exchange.sent.canonical_payload == preflight.request.canonical_bytes()
            assert exchange.returned.request_id == exchange.sent.request_id
            assert exchange.returned.responder == exchange.sent.callee
            assert exchange.sent_at_ns <= exchange.returned_at_ns
            registry = runtime._h1_completion_exchange_registry
            assert registry is not None
            assert (
                registry._replay_completion_exchange(preflight.first_path, exchange).request_bytes
                == preflight.request.canonical_bytes()
            )

            conversation_cut = session.capture_current(
                original_identity=request.identity,
                original_fingerprint=request.original_driver_command_fingerprint(),
                selected_seal=seal,
                completion_exchange=exchange,
            )
            assert conversation_cut.conversation is not None
            conversation_owner = session._conversation_sources
            assert conversation_owner is runtime._h1_conversation_source_port
            conversation_request = conversation_owner._prepare_conversation_completion_request(
                conversation_cut.conversation
            )
            assert (
                conversation_request.canonical_json_bytes()
                == type(conversation_request)
                .model_validate_json(conversation_request.canonical_json_bytes())
                .canonical_json_bytes()
            )
            assert (
                conversation_request.original_completion_request_bytes
                == preflight.request.canonical_bytes()
            )
            assert (
                conversation_request.loop_preparation_bytes == exchange.returned.canonical_payload
            )
            assert session._completion_exchange is exchange

            conversation_exchange = await session.prepare_conversation_completion()
            assert conversation_exchange.role == "conversation"
            assert (
                conversation_exchange.sent.operation_id
                == "projections.prepare_conversation_completion"
            )
            assert conversation_exchange.sent.canonical_payload == (
                conversation_request.canonical_json_bytes()
            )
            assert (
                conversation_exchange.returned.request_id
                == conversation_exchange.sent.request_id
            )
            assert conversation_exchange.returned.responder == conversation_exchange.sent.callee
            assert conversation_exchange.sent_at_ns <= conversation_exchange.returned_at_ns
            assert session._conversation_exchange is conversation_exchange

            effects_exchange = await session.prepare_local_commentary()
            assert effects_exchange.role == "effects"
            assert effects_exchange.sent.operation_id == "effects.prepare_h1_local_commentary"
            assert effects_exchange.returned.request_id == effects_exchange.sent.request_id
            assert effects_exchange.returned.responder == effects_exchange.sent.callee
            assert effects_exchange.sent_at_ns <= effects_exchange.returned_at_ns
            assert session._effects_exchange is effects_exchange

            terminal_exchange = await session.prepare_terminal_work()
            assert terminal_exchange.role == "terminal_work"
            assert terminal_exchange.sent.operation_id == "agent_loop.prepare_terminal_work"
            assert terminal_exchange.returned.request_id == terminal_exchange.sent.request_id
            assert terminal_exchange.returned.responder == terminal_exchange.sent.callee
            assert terminal_exchange.sent_at_ns <= terminal_exchange.returned_at_ns
            assert session._terminal_work_exchange is terminal_exchange
            assert tuple(
                item.role
                for item in (
                    exchange,
                    conversation_exchange,
                    effects_exchange,
                    terminal_exchange,
                )
            ) == ("completion", "conversation", "effects", "terminal_work")

            final_fence = await port._capture_final_completion_fence(session)
            final_wire = port._replay_final_completion_fence(final_fence, session)
            assert final_wire.sent_at_ns <= final_wire.returned_at_ns
            assert final_wire.sent.operation_id == (
                "deployment_trust.read_current_hermetic_output_scope"
            )
