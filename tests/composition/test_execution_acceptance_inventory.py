"""Accepted inventory requires the complete original cross-owner byte graph."""

import pytest

from chiplog.capabilities.agent_loop.recovery_contracts import Absent, Present
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import ConsequentialAcceptedCall
from chiplog.composition.r14_execution_inventory import accepted_inventory
from tests.support.acceptance_v2 import prepared_acceptance


def test_acceptance_retains_original_initialization_and_complete_manifest_without_terminal() -> (
    None
):
    retained = prepared_acceptance()
    cut = retained.loop_request.binding.cut
    before = cut.predecessor_inventory.model_copy(
        update={"tenant_commit_sequence": cut.tenant_commit_sequence + 1}
    )
    after = accepted_inventory(before, (retained,))
    row = after.ordered_calls[0]
    assert row.initialized_record == before.ordered_calls[0].initialized_record
    assert row.initialized == before.ordered_calls[0].initialized
    assert isinstance(row.acceptance, ConsequentialAcceptedCall)
    assert row.acceptance.complete_acceptance_manifest == tuple(
        ref.revision for ref in retained.loop_proposal.complete_acceptance_manifest
    )
    assert isinstance(row.terminal, Absent)
    assert after.tenant_commit_sequence == before.tenant_commit_sequence
    assert accepted_inventory(after, ()) == after
    with pytest.raises(ValueError, match="live initialized branch"):
        accepted_inventory(after, (retained,))


@pytest.mark.parametrize(
    "mutation", ["frontier", "foreign", "missing", "terminal", "initialization", "duplicate"]
)
def test_acceptance_rejects_wrong_cut_or_lost_original_branch(mutation: str) -> None:
    retained = prepared_acceptance()
    cut = retained.loop_request.binding.cut
    inventory = cut.predecessor_inventory.model_copy(
        update={"tenant_commit_sequence": cut.tenant_commit_sequence + 1}
    )
    row = inventory.ordered_calls[0]
    if mutation == "frontier":
        inventory = cut.predecessor_inventory
    elif mutation == "foreign":
        inventory = inventory.model_copy(update={"tenant_id": "other"})
    elif mutation == "missing":
        inventory = inventory.model_copy(update={"ordered_calls": ()})
    elif mutation == "terminal":
        row = row.model_copy(update={"terminal": Present(head="cancelled", fingerprint="0" * 64)})
        inventory = inventory.model_copy(update={"ordered_calls": (row,)})
    elif mutation == "initialization":
        altered = row.initialized_record.model_copy(update={"original_call_id": "other"})
        inventory = inventory.model_copy(
            update={"ordered_calls": (row.model_copy(update={"initialized_record": altered}),)}
        )
    elif mutation == "duplicate":
        inventory = inventory.model_copy(update={"ordered_calls": (row, row)})
    with pytest.raises(ValueError):
        accepted_inventory(inventory, (retained,))


def test_acceptance_rejects_duplicate_selection_and_altered_owner_companion() -> None:
    retained = prepared_acceptance()
    cut = retained.loop_request.binding.cut
    inventory = cut.predecessor_inventory.model_copy(
        update={"tenant_commit_sequence": cut.tenant_commit_sequence + 2}
    )
    with pytest.raises(ValueError, match="strictly preceding"):
        accepted_inventory(inventory, (retained, retained))
    corrupted = retained.model_copy(
        update={
            "effects_proposal": retained.effects_proposal.model_copy(
                update={
                    "record": retained.effects_proposal.record.model_copy(
                        update={"fingerprint": "0" * 64}
                    )
                }
            )
        }
    )
    with pytest.raises(ValueError):
        accepted_inventory(inventory, (corrupted,))
