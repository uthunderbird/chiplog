"""Shared builders extracted without changing their behavior."""

from __future__ import annotations

from typing import Any

from tests.support.composition.h1_horizontal_completion import _selected_native_v3_then_v2_source


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
