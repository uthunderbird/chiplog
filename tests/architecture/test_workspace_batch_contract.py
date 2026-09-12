from typing import get_type_hints

from chiplog.capabilities.projections.batch_boundary import (
    ProposalContext,
    WorkspaceBatch,
    WorkspaceBatchPort,
)
from chiplog.capabilities.projections.workspace_boundary import WorkspaceReadResult


def test_public_batch_contract_keeps_external_cut_and_evidence_only_proposal() -> None:
    assert {"context", "external_context", "history_complete", "label"} <= set(
        WorkspaceBatch.model_fields
    )
    assert get_type_hints(WorkspaceBatchPort.proposal_context)["return"] is ProposalContext
    assert ProposalContext.model_fields["kind"].default == "WORKSPACE_EVIDENCE_ONLY"
    assert WorkspaceBatch.model_config["frozen"]
    assert WorkspaceBatch.model_config["extra"] == "forbid"
    assert get_type_hints(WorkspaceBatchPort.history)["return"] is WorkspaceReadResult
