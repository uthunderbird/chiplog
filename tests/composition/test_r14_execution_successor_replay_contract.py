"""Successor orchestration contract; selected-history boundaries are explicit doubles."""

import asyncio
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock, create_autospec

import pytest

from chiplog.composition import r14_execution_successor as successor
from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
from chiplog.composition.common_execution_driver_contracts import DriveInputRequestV1
from tests.support.execution_versions import execution_run_v3


@pytest.mark.asyncio
@pytest.mark.parametrize("start_selected", (False, True), ids=("finish-start", "selected-start"))
async def test_successor_replay_returns_run_from_either_start_path(
    monkeypatch: pytest.MonkeyPatch, start_selected: bool
) -> None:
    """Test result forwarding only; doubles supply no production authority."""
    run = execution_run_v3()
    source = SimpleNamespace(result=SimpleNamespace(successor_run=run))
    started = SimpleNamespace(result=SimpleNamespace(run=run))
    runtime = create_autospec(CommonCliExecutionRuntime, instance=True)
    runtime._execution_lane = asyncio.Lock()
    runtime._execution_actor = AsyncMock(return_value=object())
    runtime._authority_gate.return_value.hold.return_value = nullcontext()
    request = create_autospec(DriveInputRequestV1, instance=True)
    request.original_driver_command_fingerprint.return_value = "retained-driver-fingerprint"
    replay = {"selected": "successor-batch"}
    start_entry = ("start", b"selected-start", {"selected": "start-batch"}, object())
    finish_batch = AsyncMock()
    finish_start = AsyncMock(return_value=started)
    publish_start = AsyncMock(return_value=started)
    monkeypatch.setattr(successor, "_find_replay", lambda *_args: replay)
    monkeypatch.setattr(successor, "_retained", lambda *_args: source)
    monkeypatch.setattr(
        successor, "_entry_for", lambda *_args: start_entry if start_selected else None
    )
    monkeypatch.setattr(successor, "_finish_batch", finish_batch)
    monkeypatch.setattr(successor, "_finish_start", finish_start)
    monkeypatch.setattr(successor, "_publish_start", publish_start)

    result = await successor.drive_successor(runtime, "peer", request, None)

    finish_batch.assert_awaited_once_with(runtime, replay)
    if start_selected:
        finish_start.assert_awaited_once_with(runtime, start_entry[2])
        publish_start.assert_not_awaited()
    else:
        publish_start.assert_awaited_once_with(runtime, "peer", source)
        finish_start.assert_not_awaited()
    assert result is run
