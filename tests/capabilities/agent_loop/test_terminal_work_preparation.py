"""H1 terminal-work owner preparation remains a pure, unmounted boundary."""

from __future__ import annotations

import asyncio
import base64
import hashlib

from tests.support.completion_assembly import (
    AcceptedAssemblyFixture,
    accepted_completion_fixture,
)

from chiplog.capabilities.agent_loop import _terminal_work_process as process
from chiplog.capabilities.agent_loop import terminal_work_preparation as preparation
from chiplog.capabilities.agent_loop.post_terminal_contracts import (
    PreparedPostTerminalWork,
    PrepareTerminalWork,
    WorkPreparationRejected,
)


def _request(fixture: AcceptedAssemblyFixture) -> PrepareTerminalWork:
    return fixture.assembly.terminal_work_request


def test_h1_zero_obligation_terminal_work_preserves_exact_source_and_result_bytes() -> None:
    async def exercise() -> None:
        fixture = await accepted_completion_fixture("v3", "empty")
        request = _request(fixture)

        result = preparation.prepare_h1_terminal_work(request)

        assert isinstance(result, PreparedPostTerminalWork)
        assert result.source_request_fingerprint == hashlib.sha256(
            request.canonical_bytes()
        ).hexdigest()
        assert result.ordered_work == ()
        assert result.complete_records == ()
        assert result.complete_commitment == preparation.prepared_post_terminal_work_commitment(
            request, result.ordered_work, result.complete_records
        )
        assert result.canonical_bytes() == (
            b'{"kind":"PREPARED_POST_TERMINAL_WORK_V1","complete_commitment":"'
            b'2a96a8a4423c4c4c6fb630fcd458969581df4bda21b4fcd0039918b39baf3dbb",'
            b'"complete_records":[],"ordered_work":[],"source_request_fingerprint":"'
            b'1f91a165c22aa871daa7e808ee45c71c76c76423de514f13f3d6a76da0229490"}'
        )
        assert result.canonical_bytes() == preparation.prepare_h1_terminal_work(
            request
        ).canonical_bytes()
        assert (
            request.original_terminalization_request
            == fixture.assembly.work_source.canonical_bytes()
        )

    asyncio.run(exercise())


def test_h1_terminal_work_dispatch_rejects_noncanonical_or_mismatched_accepted_source() -> None:
    async def exercise() -> None:
        fixture = await accepted_completion_fixture("v3", "empty")
        request = _request(fixture)
        malformed = request.model_copy(update={"original_terminalization_request": b"not-source"})
        mismatched = request.model_copy(
            update={"terminal_run": request.terminal_run.model_copy(update={"head": "foreign"})}
        )

        for value in (malformed, mismatched):
            reply = process.dispatch(process.OPERATION, value.canonical_bytes())
            assert reply["failure"] == "PROTOCOL_REJECTED"

    asyncio.run(exercise())


def test_h1_terminal_work_rejects_nonempty_obligation_genesis() -> None:
    async def exercise() -> None:
        fixture = await accepted_completion_fixture("v3", "nonempty")
        request = _request(fixture)

        result = preparation.prepare_h1_terminal_work(request)
        assert isinstance(result, WorkPreparationRejected)
        assert result.code == "DENIED"

        reply = process.dispatch(process.OPERATION, request.canonical_bytes())
        payload = reply["payload"]
        assert isinstance(payload, str)
        decoded = WorkPreparationRejected.model_validate_json(base64.b64decode(payload))
        assert decoded == result

    asyncio.run(exercise())
