"""Corrupt a real owner's returned proposal before the broker accepts it."""

from pathlib import Path

import pytest

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.effects.application import _bind_record
from chiplog.capabilities.effects.contracts import (
    PreparedEffectPublication,
    PublishPlanEffectCommand,
)
from chiplog.composition.r14 import open_r14_loop
from chiplog.platform.broker import PublicPortCall, PublicPortRejected, PublicPortResult
from tests.composition.test_plan_effect_publication import prepare_display, response


@pytest.mark.parametrize("mutation", ("state", "rehash_state", "attempt", "record", "evidence"))
async def test_actual_owner_reply_cannot_substitute_initial_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    async with open_r14_loop(tmp_path / "result.sqlite", responses=(response(),)) as loop:
        runtime, display = await prepare_display(loop)
        broker = runtime._supervisor.runtime()
        original = broker.call

        async def corrupt(call: PublicPortCall) -> PublicPortResult:
            result = await original(call)
            if call.operation_id != "effects.prepare_transition":
                return result
            assert not isinstance(result, PublicPortRejected)
            prepared = PreparedEffectPublication.model_validate_json(result.canonical_payload)
            record = prepared.record
            snapshot = record.snapshot
            if mutation in {"state", "rehash_state"}:
                snapshot = snapshot.model_copy(update={"state": "SENT"})
            elif mutation == "attempt":
                snapshot = snapshot.model_copy(update={"attempt": record.record})
            elif mutation == "evidence":
                snapshot = snapshot.model_copy(update={"evidence": (record.record,)})
            else:
                record = record.model_copy(update={"record": snapshot.attempt})
            prepared = prepared.model_copy(
                update={"record": record.model_copy(update={"snapshot": snapshot})}
            )
            if mutation in {"rehash_state", "evidence"}:
                command = PublishPlanEffectCommand.model_validate_json(record.source_command)
                prepared = _bind_record(
                    command,
                    prepared.expected_store,
                    None,
                    snapshot,
                    "PLAN_EFFECT_PUBLISHED",
                    prepared.exact_companion_manifest,
                )
            return result.model_copy(update={"canonical_payload": prepared.canonical_bytes()})

        monkeypatch.setattr(broker, "call", corrupt)
        with pytest.raises(LoopRejected, match="exact initial publication"):
            await runtime.publish_effect(
                "hermetic-ingress",
                display.display_id,
                display.display_digest,
                display.adoption_act_id,
            )
        assert not runtime._owner_decisions().snapshot().decisions
