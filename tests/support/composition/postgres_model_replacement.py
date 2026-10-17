"""Shared builders extracted without changing their behavior."""

import asyncio
import json

from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.capabilities.agent_loop.contracts import (
    ModelAttempt,
)
from chiplog.capabilities.agent_loop.execution_client_transitions import ExecutionAttempt

RESPONSE = json.dumps(
    {
        "kind": "Continue",
        "tool_calls": [
            {
                "call_id": "proposal",
                "tool": "propose_intent",
                "text": "replacement",
            }
        ],
    }
).encode()


class PausedModel(HermeticModel):
    def __init__(self) -> None:
        super().__init__((RESPONSE,))
        self.entered, self.release = asyncio.Event(), asyncio.Event()

    async def invoke(self, attempt: ModelAttempt | ExecutionAttempt) -> tuple[bytes, str]:
        self.entered.set()
        await self.release.wait()
        return await super().invoke(attempt)
