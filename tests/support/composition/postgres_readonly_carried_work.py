"""Shared builders extracted without changing their behavior."""

import json
import time
from pathlib import Path

import pytest
from tests.support.composition.postgres_completion import eligibility, opened, prepared
from tests.support.composition.postgres_effects import PEER
from tests.support.deployment_gate import signature

from chiplog.adapters.driven.loop_hermetic import HermeticModel
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
    commit_effect_send,
    emit_effect,
    preview_effect,
    resolve_original_effect,
)
from chiplog.composition.postgres_execution import PostgresExecutionRuntime
from chiplog.platform.postgres import PostgresServer


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
