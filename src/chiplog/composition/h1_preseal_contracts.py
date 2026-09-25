"""Private evidence carriers used while preflighting an H1 V2 seal.

These objects deliberately have no wire encoding and grant no authority.  The
broker creates them only from authenticated runtime reads under its authority
gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.capabilities.agent_loop.recovery_contracts import Digest, Identity, RecoveryDTO
from chiplog.capabilities.projections.workspace_boundary import SourceReference
from chiplog.composition.r14_execution_transition_records import RetainedExecutionTransitionV3
from chiplog.composition.r14_h1_workspace_issuance_contracts import (
    H1VerifiedWorkspaceClosure,
    H1WorkspaceIssuanceRefV1,
)
from chiplog.platform._sqlite import PhysicalPublicationCommand, PhysicalRecord
from chiplog.platform.owner_decision_journal import OwnerJournalSnapshot


class H1OwnerAsOfV1(RecoveryDTO):
    """Exact authenticated owner-journal prefix selected with an H1 V2 seal."""

    kind: Literal["H1_OWNER_AS_OF_V1"] = "H1_OWNER_AS_OF_V1"
    tenant_id: Identity
    owner_head: Digest | None


@dataclass(frozen=True, slots=True)
class H1SelectedPrepare:
    retained: RetainedExecutionTransitionV3
    retained_bytes: bytes
    decision_id: str
    decision_bytes: bytes
    publication: PhysicalPublicationCommand
    physical_run: PhysicalRecord
    started_run: ExecutionRunRecord
    source_tenant_sequence: int
    workspace_member_bytes: bytes
    proposal_context_bytes: bytes
    issuance_ref: H1WorkspaceIssuanceRefV1


@dataclass(frozen=True, slots=True)
class H1SelectedSeal:
    """Authenticated selected seal boundary for historical inventory replay."""

    command: PhysicalPublicationCommand
    decision_id: str
    decision_bytes: bytes
    commit_sequence: int


@dataclass(frozen=True, slots=True)
class H1Scope:
    tenant: str
    principal: str
    run_id: str
    turn_id: str
    run_heads: tuple[str, ...]
    reachable_source_refs: tuple[SourceReference, ...]


@dataclass(frozen=True, slots=True)
class H1InventoryReceipt:
    scope: H1Scope
    database_identity: tuple[str, int, int]
    tenant_sequence: int
    commitment: str
    loop_entries: tuple[tuple[str, str | None, bytes], ...]
    owner_snapshot: OwnerJournalSnapshot
    physical_inventory_bytes: bytes
    evidence_inbox_bytes: bytes
    pending_inventory_bytes: bytes
    source_inventory_bytes: bytes
    decoder_registry_fingerprint: str
    fingerprint: str


@dataclass(frozen=True, slots=True)
class H1V2SealPreflight:
    captured_run: ExecutionRunRecord
    prepare: H1SelectedPrepare
    workspace: H1VerifiedWorkspaceClosure
    inventory: H1InventoryReceipt
    worker_session: str


__all__ = [
    "H1InventoryReceipt",
    "H1OwnerAsOfV1",
    "H1Scope",
    "H1SelectedPrepare",
    "H1SelectedSeal",
    "H1V2SealPreflight",
]
