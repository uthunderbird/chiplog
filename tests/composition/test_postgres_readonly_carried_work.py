"""New history attempts cannot skip an older carried effect's current semantics."""

from pathlib import Path
from typing import Literal

import pytest

from chiplog.capabilities.agent_loop.contracts import LoopHold
from chiplog.capabilities.agent_loop.readonly_runtime_contracts import (
    ReadOnlyLineageV2,
)
from chiplog.capabilities.effects.client_effect_contracts import NativeEffectSendV1
from chiplog.composition import postgres_readonly
from chiplog.composition.postgres_effects import (
    PostgresEffectsBinding,
    append_effect_receipt,
)
from chiplog.composition.postgres_execution import PostgresExecutionRuntime
from chiplog.platform.postgres import PostgresServer
from tests.support.composition.postgres_effects import PEER
from tests.support.composition.postgres_readonly_carried_work import (
    pending_after_closed_effect,
)


async def append_rival(
    runtime: PostgresExecutionRuntime, binding: PostgresEffectsBinding, send: NativeEffectSendV1
) -> None:
    raw = binding.provider.observe_late_receipt(send.transmission.transmission.subject_id)
    await append_effect_receipt(runtime, binding, PEER, send.original_call_id, raw)


@pytest.mark.parametrize("route", ["same_run", "successor"])
async def test_new_readonly_retry_holds_for_rival_in_an_older_carried_seal(
    postgres_server: PostgresServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    route: Literal["same_run", "successor"],
) -> None:
    runtime, binding, send, call_id, pending, reads = await pending_after_closed_effect(
        postgres_server, tmp_path, monkeypatch
    )
    await append_rival(runtime, binding, send)
    before = runtime.snapshot()
    source = runtime.execution_record("run")
    with pytest.raises(LoopHold):
        if route == "successor":
            await runtime.retry_readonly_in_successor(
                PEER, call_id, pending.head, source.head, "successor"
            )
        else:
            await runtime.execute_readonly_call(PEER, call_id, expected_counter_head=pending.head)
    assert runtime.snapshot() == before
    assert postgres_readonly.read_selected_readonly_lineage(runtime, PEER, call_id) == pending
    assert reads == ["read"]
    assert not any(run.run_id == "successor" for run in runtime._runs(before))


@pytest.mark.parametrize("route", ["same_run", "successor"])
async def test_rival_after_acceptance_does_not_discard_an_accepted_read_outcome(
    postgres_server: PostgresServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    route: Literal["same_run", "successor"],
) -> None:
    runtime, binding, send, call_id, pending, reads = await pending_after_closed_effect(
        postgres_server, tmp_path, monkeypatch
    )
    original_finish = postgres_readonly._finish_accepted

    async def finish(
        runtime: PostgresExecutionRuntime, peer: str, accepted: ReadOnlyLineageV2
    ) -> ReadOnlyLineageV2:
        persisted = postgres_readonly.read_selected_readonly_lineage(runtime, peer, call_id)
        assert persisted == accepted and accepted.state == "ACCEPTED"
        await append_rival(runtime, binding, send)
        return await original_finish(runtime, peer, accepted)

    monkeypatch.setattr(postgres_readonly, "_finish_accepted", finish)
    if route == "successor":
        result = await runtime.retry_readonly_in_successor(
            PEER, call_id, pending.head, runtime.execution_record("run").head, "successor"
        )
    else:
        result = await runtime.execute_readonly_call(
            PEER, call_id, expected_counter_head=pending.head
        )
    assert result.state == "SUCCEEDED" and result.attempts_consumed == 2
    assert reads == ["read", "read"]
    assert postgres_readonly.read_selected_readonly_lineage(runtime, PEER, call_id) == result
