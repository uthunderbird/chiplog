"""Non-tool previews and lifecycle records; original tool schemas stay unchanged."""

import hashlib
from typing import Annotated, Literal

from pydantic import Field

from .contracts import ExactHead, ProviderRecipient, TransmissionAttempt
from .dispatch_authority_contracts import DispatchObservationDTO, Identity


class ActionProposal(DispatchObservationDTO):
    tenant_id: Identity
    principal_id: Identity
    intent_id: Identity
    payload: bytes
    bundle_members: tuple[Identity, ...] = Field(min_length=1)
    target: ProviderRecipient
    expires_at_ns: int = Field(gt=0)


class PlanActionProposal(ActionProposal):
    kind: Literal["PLAN"] = "PLAN"
    planning_command_schema: Identity
    planning_command_bytes: bytes = Field(min_length=1)
    expected_planning_revision: ExactHead | None


class CompensationActionProposal(ActionProposal):
    kind: Literal["COMPENSATION"] = "COMPENSATION"
    original_intent: ExactHead
    original_stream_id: Identity
    original_evidence_head: ExactHead


ActionProposalValue = Annotated[
    PlanActionProposal | CompensationActionProposal, Field(discriminator="kind")
]


class NonToolEffectPreview(DispatchObservationDTO):
    schema_id: Literal["chiplog.effects.action-preview.v1"] = "chiplog.effects.action-preview.v1"
    head: Identity
    proposal: ActionProposalValue
    receipt_key_fingerprint: Identity


class ActionRecord(DispatchObservationDTO):
    head: str
    tenant_id: Identity
    principal_id: Identity
    effect_stream_id: Identity

    def digest(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


class ActionSendV1(ActionRecord):
    schema_id: Literal["chiplog.effects.action-send.v1"] = "chiplog.effects.action-send.v1"
    intent_head: str
    transmission: TransmissionAttempt
    ticket_bytes: bytes
    gate_request_bytes: bytes


class ActionClaimV1(ActionRecord):
    schema_id: Literal["chiplog.effects.action-claim.v1"] = "chiplog.effects.action-claim.v1"
    send_head: str
    worker: str


class ActionEvidenceV1(ActionRecord):
    schema_id: Literal["chiplog.effects.action-evidence.v1"] = "chiplog.effects.action-evidence.v1"
    send_head: str
    predecessor_evidence: str | None
    raw_receipt: bytes


class ActionInvocationV1(ActionRecord):
    schema_id: Literal["chiplog.effects.action-invocation.v1"] = (
        "chiplog.effects.action-invocation.v1"
    )
    send_head: str
    credential_id: str
    session_id: str
    trust_materialization_head: str
    complete_evidence_heads: tuple[str, ...]


class ActionClosureV1(ActionRecord):
    schema_id: Literal["chiplog.effects.action-closure.v1"] = "chiplog.effects.action-closure.v1"
    obligation_id: str
    send_head: str
    invocation_head: str
    complete_evidence_heads: tuple[str, ...]
    outcome: Literal["CONFIRMED", "FAILED_NO_EFFECT", "PARTIAL_CONFIRMED"]


class ActionReductionV1(ActionRecord):
    schema_id: Literal["chiplog.effects.action-reduction.v1"] = (
        "chiplog.effects.action-reduction.v1"
    )
    send_head: str
    closure_head: str | None
    complete_evidence_heads: tuple[str, ...]
    outcome: Literal[
        "OUTCOME_UNKNOWN", "PARTIAL", "CONFIRMED", "FAILED_NO_EFFECT", "PARTIAL_CONFIRMED"
    ]
