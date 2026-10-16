"""New history attempts cannot skip an older carried effect's current semantics."""

import json
import time
from pathlib import Path
from typing import Literal

import pytest

from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.capabilities.agent_loop.contracts import LoopHold
from chiplog.capabilities.agent_loop.readonly_history_tool_contracts import ConversationHistoryQuery
from chiplog.capabilities.agent_loop.readonly_runtime_contracts import (
    HistorySnapshotSelectionV2,
    ReadOnlyLineageV2,
)
from chiplog.capabilities.effects.client_effect_contracts import NativeEffectSendV1
from chiplog.composition import postgres_readonly
from chiplog.composition.postgres_effects import (
    PostgresEffectsBinding,
    adopt_effect,
    append_effect_receipt,
    commit_effect_send,
    emit_effect,
    preview_effect,
    resolve_original_effect,
)
from chiplog.composition.postgres_execution import PostgresExecutionRuntime
from chiplog.platform.postgres import PostgresServer
from tests.composition.test_postgres_completion import eligibility, opened, prepared
from tests.composition.test_postgres_effects import PEER
from tests.support.deployment_gate import signature


async def pending_after_closed_effect(
    postgres_server: PostgresServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[
    PostgresExecutionRuntime,
    PostgresEffectsBinding,
    NativeEffectSendV1,
    str,
    ReadOnlyLineageV2,
    list[str],
]:
    raw = json.dumps(
        {
            "kind": "Continue",
            "tool_calls": [
                {
                    "call_id": "effect",
                    "tool": "request_self_effect",
                    "arguments": {"payload": "aGVsbG8=", "bundle_members": ["m"]},
                }
            ],
        }
    ).encode()
    runtime, binding, _ = await prepared(postgres_server, tmp_path, raw=raw, late=True)
    effect_id = runtime.initialized_calls()[0].original_call_id
    preview = await preview_effect(runtime, binding, PEER, effect_id, time.time_ns() + 10**12)
    adopted = await adopt_effect(runtime, binding, PEER, preview.head, "explicit-adoption")
    current, request = eligibility(adopted.canonical_bytes(), "effects.first_send", "original-send")
    runtime.import_entitlement(current, signature(current))
    send = await commit_effect_send(runtime, binding, PEER, effect_id, request)
    await emit_effect(runtime, binding, PEER, effect_id)
    await resolve_original_effect(runtime, binding, PEER, effect_id)
    history_response = json.dumps(
        {
            "kind": "Continue",
            "tool_calls": [
                {
                    "call_id": "history",
                    "tool": "read_conversation_history",
                    "arguments": {"limit": 10, "after_cursor": None},
                }
            ],
        }
    ).encode()
    runtime = opened(runtime, binding, HermeticModel((history_response,)))
    continued = await runtime.continue_execution(PEER, "run", runtime.execution_record("run").head)
    captured = await runtime.capture_execution(PEER, "run", continued.head)
    await runtime.seal_execution(PEER, "run", captured.head)
    readonly_id = runtime.initialized_calls()[-1].original_call_id
    reads: list[str] = []
    actual_read = runtime.read_history_snapshot

    def read(
        peer: str, selection: HistorySnapshotSelectionV2, query: ConversationHistoryQuery
    ) -> bytes:
        reads.append("read")
        if len(reads) == 1:
            raise postgres_readonly.HistoryReadUnavailable("definite temporary failure")
        return actual_read(peer, selection, query)

    monkeypatch.setattr(runtime, "read_history_snapshot", read)
    pending = await runtime.execute_readonly_call(PEER, readonly_id)
    assert pending.state == "RETRY_PENDING" and pending.attempts_consumed == 1
    return runtime, binding, send, readonly_id, pending, reads


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
