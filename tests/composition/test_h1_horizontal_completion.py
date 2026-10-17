"""Mounted H1 four-owner-call and final-scope witness."""

from __future__ import annotations

from pathlib import Path

import pytest

from chiplog.composition.common_cli_execution_runtime import open_installed_h1_runtime
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from tests.support.composition.h1_horizontal_completion import (
    _selected_native_v3_then_v2_source,
)
from tests.support.h1_installed_launch import installed_slot, prepare_installed_slot


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
                conversation_exchange.returned.request_id == conversation_exchange.sent.request_id
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
