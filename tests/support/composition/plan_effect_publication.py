"""Shared builders extracted without changing their behavior."""

import base64
import json

from chiplog.capabilities.agent_loop.application import AgentLoop
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy, ProposalDisplay
from chiplog.composition.r14_runtime import R14PlanningRuntime
from chiplog.composition.r16_effects import (
    HermeticEffectProposal,
    R16EffectsProducer,
)


def response() -> bytes:
    proposal = HermeticEffectProposal(
        schema_id="chiplog.hermetic-effect-proposal.v1",
        purpose="My exact hermetic action",
        payload_base64=base64.b64encode(b"\xffexact\x00payload").decode(),
        bundle_members=("self-action",),
    )
    return json.dumps(
        {
            "kind": "Continue",
            "tool_calls": [
                {
                    "call_id": "effect",
                    "tool": "propose_intent",
                    "text": proposal.canonical_bytes().decode(),
                }
            ],
        }
    ).encode()


async def prepare_display(loop: AgentLoop) -> tuple[R14PlanningRuntime, ProposalDisplay]:
    created = await loop.create("r", "Propose my exact hermetic action", BudgetPolicy())
    active = await loop.activate("r", created.head)
    await loop.step("r", active.head)
    runtime = loop._planning
    assert isinstance(runtime, R14PlanningRuntime)
    display = await R16EffectsProducer(runtime).display_effect("r/turn/1/proposal/effect")
    return runtime, display
