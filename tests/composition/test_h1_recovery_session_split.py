"""Focused witnesses for the first H1 recovery-session completion split."""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from typing import Any, cast

import pytest

from chiplog.capabilities.agent_loop.execution_first_path_completion_contracts import (
    PrepareExecutionCompletionFirstPathV2,
)
from chiplog.composition.common_cli_execution_runtime import open_installed_h1_runtime
from chiplog.composition.h1_completion_preparation_session import (
    H1CompletionPreparationSession,
    H1CompletionPreparationUnavailable,
)
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform.broker import PublicPortCall
from tests.composition.test_h1_horizontal_completion import _selected_native_v3_then_v2_source
from tests.support.h1_installed_launch import installed_slot, prepare_installed_slot


class _RecordingEngine:
    """Keep the installed dispatcher while retaining the exact completion frames."""

    def __init__(self, installed: Any) -> None:
        self._installed = installed
        self.calls: list[PublicPortCall] = []
        self.failure: BaseException | None = None

    def session(self, owner: str) -> Any:
        return self._installed.session(owner)

    async def call(self, sent: PublicPortCall) -> Any:
        self.calls.append(sent)
        if self.failure is not None:
            raise self.failure
        return await self._installed.call(sent)


@pytest.mark.asyncio
async def test_completion_build_is_private_pre_ipc_and_dispatches_its_exact_bytes(
    tmp_path: Path,
) -> None:
    """A recovery pin can use the semantic request before B opens its completion route."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=resources) as runtime:
            runtime_value = cast(Any, runtime)
            (
                request,
                _original,
                seal,
                _first_path,
                _native,
            ) = await _selected_native_v3_then_v2_source(runtime_value)
            installed = runtime_value._supervisor.runtime()
            engine = _RecordingEngine(installed)
            runtime_value._supervisor.runtime = lambda: engine
            enrollment = runtime_value._h1_live_completion_enrollment
            assert enrollment is not None
            session = enrollment._open_session()
            session.capture_first_path(
                original_identity=request.identity,
                original_fingerprint=request.original_driver_command_fingerprint(),
                selected_seal=seal,
            )

            preflight = await session._build_first_path_completion_preflight()

            request_bytes = preflight.request.canonical_bytes()
            assert session._preflight is preflight
            assert (
                PrepareExecutionCompletionFirstPathV2.model_validate_json(
                    request_bytes
                ).canonical_bytes()
                == request_bytes
            )
            assert [
                call
                for call in engine.calls
                if call.operation_id == "agent_loop.prepare_first_path_completion"
            ] == []

            exchange = await session._dispatch_first_path_completion()

            completion_calls = [
                call
                for call in engine.calls
                if call.operation_id == "agent_loop.prepare_first_path_completion"
            ]
            assert completion_calls == [exchange.sent]
            assert exchange.sent.canonical_payload == request_bytes


def test_completion_public_api_has_no_caller_supplied_recovery_pin() -> None:
    """Only session-retained preflight state can reach the completion dispatcher."""
    prepare = inspect.signature(H1CompletionPreparationSession.prepare_first_path_completion)
    dispatch = inspect.signature(H1CompletionPreparationSession._dispatch_first_path_completion)

    assert tuple(prepare.parameters) == ("self",)
    assert tuple(dispatch.parameters) == ("self",)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", (RuntimeError("owner failed"), asyncio.CancelledError()))
async def test_direct_completion_dispatch_stays_consumed_after_ambiguous_owner_outcome(
    tmp_path: Path, failure: BaseException
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=resources) as runtime:
            runtime_value = cast(Any, runtime)
            (
                request,
                _original,
                seal,
                _first_path,
                _native,
            ) = await _selected_native_v3_then_v2_source(runtime_value)
            engine = _RecordingEngine(runtime_value._supervisor.runtime())
            runtime_value._supervisor.runtime = lambda: engine
            enrollment = runtime_value._h1_live_completion_enrollment
            assert enrollment is not None
            session = enrollment._open_session()
            session.capture_first_path(
                original_identity=request.identity,
                original_fingerprint=request.original_driver_command_fingerprint(),
                selected_seal=seal,
            )
            await session._build_first_path_completion_preflight()
            engine.failure = failure

            with pytest.raises(type(failure)):
                await session._dispatch_first_path_completion()
            with pytest.raises(H1CompletionPreparationUnavailable, match="already started"):
                await session._dispatch_first_path_completion()
