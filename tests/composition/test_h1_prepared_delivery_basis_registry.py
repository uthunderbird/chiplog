"""Mounted live replay for the inert H1 prepared-delivery basis."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from chiplog.capabilities.agent_loop.execution_completion_contracts import (
    PreparedExecutionCompletion,
)
from chiplog.composition.common_cli_execution_runtime import open_installed_h1_runtime
from chiplog.composition.h1_completion_exchange_registry import (
    H1CompletionExchangeRegistryViolation,
)
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from tests.composition.test_h1_horizontal_completion import _selected_native_v3_then_v2_source
from tests.support.h1_installed_launch import installed_slot, prepare_installed_slot


async def _completed_session(runtime: Any) -> tuple[Any, Any, Any, Any]:
    request, _original, seal, _first_path, _native = await _selected_native_v3_then_v2_source(
        runtime
    )
    enrollment = runtime._h1_live_completion_enrollment
    assert enrollment is not None
    session = enrollment._open_session()
    session.capture_first_path(
        original_identity=request.identity,
        original_fingerprint=request.original_driver_command_fingerprint(),
        selected_seal=seal,
    )
    exchange = await session.prepare_first_path_completion()
    return request, seal, session, exchange


@pytest.mark.asyncio
async def test_installed_registry_captures_and_replays_only_a_genuine_b_completion(
    tmp_path: Path,
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=resources) as runtime:
            request, seal, session, exchange = await _completed_session(runtime)
            registry = runtime._h1_completion_exchange_registry
            journal = runtime._h1_delivery_evidence_journal
            assert registry is not None
            assert journal is not None
            before = journal._entries()

            capture = registry.capture_prepared_delivery_current(
                original_identity=request.identity,
                original_fingerprint=request.original_driver_command_fingerprint(),
                selected_seal=seal,
            )
            first = registry.replay_prepared_delivery_current(capture)
            second = registry.replay_prepared_delivery_current(capture)

            assert first == second
            assert journal._entries() == before
            assert (
                first.basis.completion_command_bytes
                == session._preflight.request.exact_captured_response
            )
            prepared = PreparedExecutionCompletion.model_validate_json(
                exchange.returned.canonical_payload
            )
            assert first.basis.loop_proposal_bytes == prepared.delivery.canonical_bytes()
            assert first.delivery.rendered_bytes
            assert (
                first.delivery.selection.recipient
                == session._preflight.request.run.origin.recipient
            )
            assert first.tenant_id == session._preflight.request.run.tenant
            assert first.database_id == session._preflight.request.source.database_id
            port = runtime._h1_preissuance_registration_source_port
            assert port is not None
            scope = port._replay_completion_scope(
                session._preflight.scope_cap, session._preflight.native_cap
            )
            assert first.delivery.selection.recipient == scope.recipient
            assert first.delivery.policy == scope.scope.disclosure_policy.ref

            with pytest.raises(TypeError, match="cannot be copied"):
                copy.copy(capture)
            with pytest.raises(H1CompletionExchangeRegistryViolation, match="locator"):
                registry.capture_prepared_delivery_current(
                    original_identity=request.identity.model_copy(
                        update={"driver_command_id": "different-driver-command"}
                    ),
                    original_fingerprint=request.original_driver_command_fingerprint(),
                    selected_seal=seal,
                )
            with pytest.raises(H1CompletionExchangeRegistryViolation, match="locator"):
                registry.capture_prepared_delivery_current(
                    original_identity=request.identity,
                    original_fingerprint="0" * 64,
                    selected_seal=seal,
                )
            with pytest.raises(H1CompletionExchangeRegistryViolation, match="locator"):
                registry.capture_prepared_delivery_current(
                    original_identity=request.identity,
                    original_fingerprint=request.original_driver_command_fingerprint(),
                    selected_seal=seal.model_copy(update={"subject_id": "other-seal"}),
                )


@pytest.mark.asyncio
async def test_registry_rejects_before_success_and_after_source_or_mount_change(
    tmp_path: Path,
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=resources) as runtime:
            request, _original, seal, _first_path, _native = (
                await _selected_native_v3_then_v2_source(runtime)
            )
            registry = runtime._h1_completion_exchange_registry
            assert registry is not None
            with pytest.raises(H1CompletionExchangeRegistryViolation, match="one B registration"):
                registry.capture_prepared_delivery_current(
                    original_identity=request.identity,
                    original_fingerprint=request.original_driver_command_fingerprint(),
                    selected_seal=seal,
                )

            enrollment = runtime._h1_live_completion_enrollment
            assert enrollment is not None
            session = enrollment._open_session()
            session.capture_first_path(
                original_identity=request.identity,
                original_fingerprint=request.original_driver_command_fingerprint(),
                selected_seal=seal,
            )
            await session.prepare_first_path_completion()
            capture = registry.capture_prepared_delivery_current(
                original_identity=request.identity,
                original_fingerprint=request.original_driver_command_fingerprint(),
                selected_seal=seal,
            )

            runtime._h1_preissuance_registration_source_port._delivery_receipts.clear()
            with pytest.raises(H1CompletionExchangeRegistryViolation, match="not current"):
                registry.replay_prepared_delivery_current(capture)

        with pytest.raises(H1CompletionExchangeRegistryViolation, match="closed"):
            registry.replay_prepared_delivery_current(capture)


@pytest.mark.asyncio
async def test_registry_rejects_a_changed_a_capture_source(tmp_path: Path) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=resources) as runtime:
            request, seal, _session, _exchange = await _completed_session(runtime)
            registry = runtime._h1_completion_exchange_registry
            sources = runtime._h1_first_path_sources
            assert registry is not None
            assert sources is not None
            capture = registry.capture_prepared_delivery_current(
                original_identity=request.identity,
                original_fingerprint=request.original_driver_command_fingerprint(),
                selected_seal=seal,
            )

            sources._issued.clear()

            with pytest.raises(H1CompletionExchangeRegistryViolation, match="not current"):
                registry.replay_prepared_delivery_current(capture)
