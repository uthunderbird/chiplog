"""R14 binds checkpoint admission to the complete selected-decision frontier."""

from pathlib import Path

import pytest

from chiplog.composition.r14 import open_r14_loop
from chiplog.composition.r14_runtime import R14PlanningRuntime
from chiplog.platform._sqlite import AuthorityMutationAdmissionError, FenceAdvanceCommand


async def test_r14_exposes_only_broker_owned_checkpoint_store(tmp_path: Path) -> None:
    async with open_r14_loop(tmp_path / "authority.sqlite", responses=()) as loop:
        runtime = loop._session
        assert isinstance(runtime, R14PlanningRuntime)
        runtime.activate_checkpoint_bundle()
        first = runtime._h1_checkpoint_store()
        assert first is runtime._h1_checkpoint_store()


async def test_r14_admission_rejects_fence_while_selected_frontier_is_pending(
    tmp_path: Path,
) -> None:
    async with open_r14_loop(tmp_path / "authority.sqlite", responses=()) as loop:
        runtime = loop._session
        assert isinstance(runtime, R14PlanningRuntime)
        runtime._append_decision(
            {
                "version": 1,
                "kind": "DECIDED",
                "operation_id": "pending",
                "operation_kind": "agent_loop",
                "expected_head": 0,
                "fingerprint": "request",
                "predecessor": runtime._commitment_journal.load(runtime._tenant_id),
                "resulting": "resulting",
                "records": [],
            }
        )
        with pytest.raises(AuthorityMutationAdmissionError, match="pending"):
            await runtime._appender.advance_fence(
                FenceAdvanceCommand(runtime._tenant_id, "next", 1)
            )
