"""Broker-private H1 original-workspace source reopening seam.

The callable takes retained bytes and a typed immutable ref only.  It does not
open a workspace, invoke a provider, or accept an authority token from callers.
"""

from __future__ import annotations

from chiplog.adapters.driven.workspace_issuance import WorkspaceIssuanceJournal

from .r14_h1_workspace_issuance import H1WorkspaceIssuanceJournal, verify_h1_original_workspace
from .r14_h1_workspace_issuance_contracts import (
    H1VerifiedWorkspaceClosure,
    H1WorkspaceIssuanceRefV1,
)


def reopen_h1_original_workspace(
    ref: H1WorkspaceIssuanceRefV1,
    workspace_member_bytes: bytes,
    proposal_context_bytes: bytes,
    journal: H1WorkspaceIssuanceJournal,
    dashboard_issuance: WorkspaceIssuanceJournal,
) -> H1VerifiedWorkspaceClosure:
    return verify_h1_original_workspace(
        ref,
        workspace_member_bytes,
        proposal_context_bytes,
        journal,
        dashboard_issuance,
    )


__all__ = ["reopen_h1_original_workspace"]
