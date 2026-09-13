from pathlib import Path

import pytest

from chiplog.capabilities.agent_loop.contracts import BudgetPolicy, LoopRejected
from chiplog.composition.r13 import open_r13_loop

PROPOSAL = (
    b'{"kind":"Continue","tool_calls":[{"call_id":"plan",'
    b'"tool":"propose_planning","text":"Swim every week"}]}'
)


async def test_exact_display_adoption_commits_once_and_replays_after_frontier_change(
    tmp_path: Path,
) -> None:
    async with open_r13_loop(tmp_path / "loop.sqlite", responses=(PROPOSAL,)) as loop:
        created = await loop.create("r", "Help me plan", BudgetPolicy())
        active = await loop.activate("r", created.head)
        await loop.step("r", active.head)
        display = await loop.display("r/turn/1/proposal/plan")
        assert loop.record("r").planning_receipts == ()
        with pytest.raises(LoopRejected):
            await loop.adopt(
                "model", display.display_id, display.display_digest, display.adoption_act_id
            )
        receipt = await loop.adopt(
            "hermetic-ingress", display.display_id, display.display_digest, display.adoption_act_id
        )
        assert receipt.purpose == "Swim every week"
        assert loop.record("r").planning_receipts == (receipt,)
        # Even a structurally valid receipt cannot rewrite what the owner committed.
        for field in ("purpose", "revision_id", "display_id", "evidence_id", "command_id"):
            forged = receipt.model_copy(update={field: "forged"})
            with pytest.raises(LoopRejected):
                loop._planning._validate_receipt(forged)  # type: ignore[union-attr]
        replay = await loop.adopt(
            "hermetic-ingress", display.display_id, display.display_digest, display.adoption_act_id
        )
        assert replay == receipt
        assert loop.record("r").planning_receipts == (receipt,)


async def test_stale_display_requires_new_display_revision_and_new_adoption(tmp_path: Path) -> None:
    async with open_r13_loop(tmp_path / "loop.sqlite", responses=(PROPOSAL,)) as loop:
        created = await loop.create("r", "Help me plan", BudgetPolicy())
        active = await loop.activate("r", created.head)
        await loop.step("r", active.head)
        display = await loop.display("r/turn/1/proposal/plan")
        await loop.create("another", "Changed canonical frontier", BudgetPolicy())
        with pytest.raises(LoopRejected, match="redisplay"):
            await loop.adopt(
                "hermetic-ingress",
                display.display_id,
                display.display_digest,
                display.adoption_act_id,
            )
        fresh = await loop.display(display.proposal_id)
        assert fresh.display_id != display.display_id
        assert fresh.adoption_act_id != display.adoption_act_id
        receipt = await loop.adopt(
            "hermetic-ingress", fresh.display_id, fresh.display_digest, fresh.adoption_act_id
        )
        assert loop.record("r").planning_receipts == (receipt,)
