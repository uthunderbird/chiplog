"""Shared builders extracted without changing their behavior."""

from typing import Literal

from chiplog.capabilities.agent_loop.application import AgentLoop
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy
from chiplog.capabilities.effects.contracts import EffectRecord, ExactHead
from chiplog.composition.r14_runtime import R14PlanningRuntime
from chiplog.composition.r16_denial_registry import DenialIngress
from chiplog.composition.r16_effects import R16EffectsProducer
from chiplog.platform._owner_publication_contracts import (
    JournalSelectedPublication,
)


async def _effect(loop: AgentLoop, run_id: str = "r") -> tuple[R14PlanningRuntime, EffectRecord]:
    created = await loop.create(run_id, "Prepare my action for " + run_id, BudgetPolicy())
    active = await loop.activate(run_id, created.head)
    await loop.step(run_id, active.head)
    runtime = loop._planning
    assert isinstance(runtime, R14PlanningRuntime)
    display = await R16EffectsProducer(runtime).display_effect(run_id + "/turn/1/proposal/effect")
    result = await runtime.publish_effect(
        "hermetic-ingress", display.display_id, display.display_digest, display.adoption_act_id
    )
    assert isinstance(result, JournalSelectedPublication), result
    effect_records = tuple(
        member
        for member in result.complete_records
        if member.owner == "effects" and member.schema_id == "chiplog.effects.record.v1"
    )
    assert len(effect_records) == 1
    return runtime, EffectRecord.model_validate_json(effect_records[0].canonical_bytes)


def _ingress(
    record: EffectRecord,
    disposition: Literal[
        "HELD_BEFORE_SEND", "CANCELLED_BEFORE_SEND", "SUPERSEDED_BEFORE_SEND"
    ] = "CANCELLED_BEFORE_SEND",
    successor: ExactHead | None = None,
) -> DenialIngress:
    return DenialIngress(
        schema_id="chiplog.hermetic-denial-ingress.v1",
        act_id="cancel-act",
        intent_id=record.snapshot.intent.intent_id,
        expected_attempt=record.snapshot.attempt,
        disposition=disposition,
        successor=successor,
    )
