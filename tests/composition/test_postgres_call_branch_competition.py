"""Actual effect adoption and cancellation select one original-call branch."""

import time
from pathlib import Path
from typing import Literal

import pytest

from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.composition.postgres_effects import adopt_effect, preview_effect
from chiplog.composition.postgres_execution import PostgresExecutionRuntime
from chiplog.platform.postgres import PostgresServer
from tests.composition.test_postgres_completion import opened, prepared
from tests.composition.test_postgres_effects import PEER
from tests.composition.test_postgres_two_seals import effect_response


@pytest.mark.parametrize("first", ["accept", "cancel"])
async def test_actual_adoption_and_cancellation_keep_one_branch_after_reopen(
    postgres_server: PostgresServer,
    tmp_path: Path,
    first: Literal["accept", "cancel"],
) -> None:
    runtime, binding, _ = await prepared(postgres_server, tmp_path, raw=effect_response("choice"))
    call_id = runtime.initialized_calls()[0].original_call_id
    initial_head = runtime.call_state(call_id).head
    preview = await preview_effect(runtime, binding, PEER, call_id, time.time_ns() + 10**12)

    async def choose(client: PostgresExecutionRuntime) -> bytes:
        if first == "accept":
            return (
                await adopt_effect(client, binding, PEER, preview.head, "explicit-adoption")
            ).canonical_bytes()
        return (
            await client.cancel_execution_call(
                PEER, call_id, "explicit-cancel", expected_head=initial_head
            )
        ).canonical_bytes()

    winner = await choose(runtime)
    selected = runtime.call_state(call_id)
    assert selected.state == ("ACCEPTED" if first == "accept" else "CANCELLED")
    before = runtime.snapshot()
    assert len(before.batches[-1].records) == (2 if first == "accept" else 1)
    with pytest.raises(LoopRejected, match="branch"):
        if first == "accept":
            await runtime.cancel_execution_call(
                PEER, call_id, "explicit-cancel", expected_head=selected.head
            )
        else:
            await adopt_effect(runtime, binding, PEER, preview.head, "explicit-adoption")
    assert runtime.snapshot() == before
    assert runtime.call_state(call_id) == selected
    assert binding.provider.transfers == ()

    runtime = opened(runtime, binding, HermeticModel())
    assert await choose(runtime) == winner
    assert runtime.call_state(call_id) == selected
    assert runtime.snapshot() == before
    assert binding.provider.transfers == ()
