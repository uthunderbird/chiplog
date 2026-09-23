"""Inert original-stream evidence and resolver contracts; no SEND authority."""

from typing import Literal

from pydantic import Field

from .contracts import CommandIdentity, ExactHead, TransmissionAttempt
from .dispatch_authority_contracts import Digest, DispatchObservationDTO, Identity
from .dispatch_v2 import DispatchAuthorizationV2, DispatchRecordV2
from .dispatch_v2_contracts import ExternalActionIntentV2


class DispatchEvidenceV2(DispatchObservationDTO):
    """Broker-authenticated original child observation; raw bytes remain addressable."""

    evidence_id: Identity
    transmission: ExactHead
    source_identity: Identity
    raw_bytes: bytes = Field(min_length=1)
    raw_digest: Digest
    observation: Literal[
        "PROVIDER_RECEIPT", "BOUNDARY_CROSSED", "TIMEOUT", "ABSENCE_OBSERVED", "MALFORMED_RESPONSE"
    ]
    occurred_members: tuple[Identity, ...]
    permanently_incapable_members: tuple[Identity, ...]


class DispatchOutcomeCommandV2(DispatchObservationDTO):
    schema_id: Literal["chiplog.effects.dispatch-outcome-command.v2"]
    identity: CommandIdentity
    operation: Literal["APPEND_EVIDENCE", "RESOLVE_OBLIGATION"]
    intent: ExactHead
    original_send: ExactHead
    expected_attempt: ExactHead
    complete_children: tuple[ExactHead, ...] = Field(min_length=1)
    prior_evidence: tuple[ExactHead, ...]
    evidence: DispatchEvidenceV2 | None
    resolver: ExactHead | None


class DispatchObligationV2(DispatchObservationDTO):
    """Original effects stream; call recovery must separately consume its exact head."""

    obligation: ExactHead
    original_send: ExactHead
    original_intent: ExactHead
    original_children: tuple[ExactHead, ...] = Field(min_length=1)
    closure_predicate: Literal["ALL_ORIGINAL_CHILDREN_TERMINAL_V2"]
    resolver_binding: Literal["AUTHENTICATED_ORIGINAL_PRINCIPAL_V2"]
    state: Literal["OPEN", "CLOSED"]
    closure_evidence: tuple[ExactHead, ...]


class DispatchOutcomeSnapshotV2(DispatchObservationDTO):
    intent: ExternalActionIntentV2
    attempt: ExactHead
    state: Literal[
        "OUTCOME_UNKNOWN", "PARTIAL", "CONFIRMED", "FAILED_NO_EFFECT", "PARTIAL_CONFIRMED"
    ]
    authorizations: tuple[DispatchAuthorizationV2, ...]
    transmissions: tuple[TransmissionAttempt, ...]
    evidence: tuple[DispatchEvidenceV2, ...]
    evidence_heads: tuple[ExactHead, ...]
    obligation: DispatchObligationV2
    conflicting_evidence: bool


class DispatchOutcomeRecordV2(DispatchObservationDTO):
    schema_id: Literal["chiplog.effects.dispatch-outcome-record.v2"]
    record: ExactHead
    predecessor: ExactHead
    kind: Literal["EVIDENCE_RECORDED", "RECONCILED"]
    command: DispatchOutcomeCommandV2
    snapshot: DispatchOutcomeSnapshotV2


class DispatchOutcomePreparationV2(DispatchObservationDTO):
    schema_id: Literal["chiplog.effects.dispatch-outcome-preparation.v2"]
    original_send: DispatchRecordV2
    previous: DispatchRecordV2 | DispatchOutcomeRecordV2
    command: DispatchOutcomeCommandV2
