"""Scoped v3 pre-send boundary and all-child v2/v3 evidence/reconciliation inputs.

No proposal grants SEND. Source authentication, exact origin equality and fresh
writer-cut checks remain mandatory; independently authenticated late evidence
does not borrow or require the original worker's live Run fence.
"""

from typing import Annotated, Literal, Protocol

from pydantic import Field

from .contracts import CommandIdentity, ExactHead, RecordEvidenceCommand, TransmissionAttempt
from .dispatch_authority_contracts import Digest, DispatchObservationDTO
from .dispatch_v2_contracts import CurrentDispatchInputsV2, DispatchBoundaryFailureV2
from .fences import Absent, WorkerFence
from .lifecycle_transition_contracts import (
    EffectsLineageCut,
    EffectsObligationRevision,
    EffectsSemanticReductionRecord,
    SelectedEffectsSource,
)
from .scoped_intent_contracts import (
    ExternalActionIntentV3,
    ScopedAuthorityRecord,
    ScopedDispatchAuthorizationRecord,
)


class SelectedScopedAuthorization(DispatchObservationDTO):
    kind: Literal["SELECTED_SCOPED_AUTHORIZATION"] = "SELECTED_SCOPED_AUTHORIZATION"
    source: SelectedEffectsSource
    record: ScopedDispatchAuthorizationRecord


class ScopedPreSendCut(DispatchObservationDTO):
    """There is no child or recovery obligation before first SEND."""

    original_intent: ExternalActionIntentV3
    selected_intent: SelectedEffectsSource
    current_parent: SelectedEffectsSource
    state: Literal[
        "INTENT_RECORDED",
        "HELD_BEFORE_SEND",
        "DISPATCH_AUTHORIZED",
        "CANCELLED_BEFORE_SEND",
        "SUPERSEDED_BEFORE_SEND",
    ]
    authorization: Annotated[Absent | SelectedScopedAuthorization, Field(discriminator="kind")]


class ScopedBoundaryCommand(DispatchObservationDTO):
    identity: CommandIdentity
    observed: ScopedPreSendCut
    current: CurrentDispatchInputsV2
    complete_current_origin_sources: tuple[ScopedAuthorityRecord, ...]
    fence: WorkerFence


class PrepareScopedAuthorization(ScopedBoundaryCommand):
    kind: Literal["PREPARE_SCOPED_AUTHORIZATION_V3"] = "PREPARE_SCOPED_AUTHORIZATION_V3"


class PrepareScopedPreSendDisposition(ScopedBoundaryCommand):
    kind: Literal["PREPARE_SCOPED_PRE_SEND_DISPOSITION_V3"] = (
        "PREPARE_SCOPED_PRE_SEND_DISPOSITION_V3"
    )
    target: Literal["HELD_BEFORE_SEND", "CANCELLED_BEFORE_SEND", "SUPERSEDED_BEFORE_SEND"]
    decision_evidence: ScopedAuthorityRecord


class PrepareScopedFirstSend(ScopedBoundaryCommand):
    kind: Literal["PREPARE_SCOPED_FIRST_SEND_V3"] = "PREPARE_SCOPED_FIRST_SEND_V3"
    selected_authorization: ExactHead
    ordinal: int = Field(default=0, strict=True, ge=0, le=0)


class ScopedPreSendRevision(DispatchObservationDTO):
    original_intent: ExactHead
    predecessor: ExactHead
    state: Literal[
        "HELD_BEFORE_SEND", "CANCELLED_BEFORE_SEND", "SUPERSEDED_BEFORE_SEND", "DISPATCH_AUTHORIZED"
    ]
    authorization: Absent | ExactHead
    retired_authorizations: tuple[ExactHead, ...]
    current_inputs_fingerprint: Digest
    complete_current_origin_sources: tuple[ScopedAuthorityRecord, ...]


class PreparedScopedAuthorization(DispatchObservationDTO):
    """Authorization primitive precedes parent; selected metadata is not fabricated here."""

    kind: Literal["PREPARED_SCOPED_AUTHORIZATION_V3"] = "PREPARED_SCOPED_AUTHORIZATION_V3"
    source_request_fingerprint: Digest
    authorization: ScopedDispatchAuthorizationRecord
    parent: ScopedPreSendRevision
    complete_batch_fingerprint: Digest


class PreparedScopedPreSendDisposition(DispatchObservationDTO):
    kind: Literal["PREPARED_SCOPED_PRE_SEND_DISPOSITION_V3"] = (
        "PREPARED_SCOPED_PRE_SEND_DISPOSITION_V3"
    )
    source_request_fingerprint: Digest
    parent: ScopedPreSendRevision
    complete_batch_fingerprint: Digest


class ScopedFirstSendDecision(DispatchObservationDTO):
    identity: CommandIdentity
    source_request_fingerprint: Digest
    original_intent: ExactHead
    authorization: ExactHead
    prior_parent: ExactHead
    ordinal: int = Field(default=0, strict=True, ge=0, le=0)
    current_inputs_fingerprint: Digest
    complete_current_origin_sources: tuple[ScopedAuthorityRecord, ...]
    fence: WorkerFence


class ScopedFirstSendParent(DispatchObservationDTO):
    original_intent: ExactHead
    predecessor: ExactHead
    state: Literal["SEND_COMMITTED"] = "SEND_COMMITTED"
    send_decision: ExactHead
    first_child: ExactHead


class PreparedScopedFirstSend(DispatchObservationDTO):
    kind: Literal["PREPARED_SCOPED_FIRST_SEND_V3"] = "PREPARED_SCOPED_FIRST_SEND_V3"
    source_request_fingerprint: Digest
    decision: ScopedFirstSendDecision
    child: TransmissionAttempt
    parent: ScopedFirstSendParent
    complete_batch_fingerprint: Digest


class PrepareAllChildEvidence(DispatchObservationDTO):
    kind: Literal["PREPARE_ALL_CHILD_EVIDENCE_V1"] = "PREPARE_ALL_CHILD_EVIDENCE_V1"
    command: RecordEvidenceCommand
    observed: EffectsLineageCut
    selected_raw_custody: SelectedEffectsSource
    source_authentication: SelectedEffectsSource
    registered_reducer: SelectedEffectsSource


class PrepareAllChildReconciliation(DispatchObservationDTO):
    kind: Literal["PREPARE_ALL_CHILD_RECONCILIATION_V1"] = "PREPARE_ALL_CHILD_RECONCILIATION_V1"
    identity: CommandIdentity
    observed: EffectsLineageCut
    registered_resolver: SelectedEffectsSource
    authenticated_invocation: SelectedEffectsSource
    registered_reducer: SelectedEffectsSource


class AllChildEvidenceRecord(DispatchObservationDTO):
    original_intent: ExactHead
    exact_child: ExactHead
    command: RecordEvidenceCommand
    selected_raw_custody: ExactHead
    source_authentication: ExactHead


class AllChildOutcomeParent(DispatchObservationDTO):
    original_intent: ExactHead
    predecessor: ExactHead
    state: Literal[
        "SENT", "OUTCOME_UNKNOWN", "PARTIAL", "CONFIRMED", "FAILED_NO_EFFECT", "PARTIAL_CONFIRMED"
    ]
    complete_ordered_children: tuple[ExactHead, ...] = Field(min_length=1)
    complete_ordered_evidence: tuple[ExactHead, ...]
    original_obligation: ExactHead | Absent


class PreparedAllChildOutcome(DispatchObservationDTO):
    """Evidence (if appended) → parent/obligation → reduction; one complete batch."""

    kind: Literal["PREPARED_ALL_CHILD_OUTCOME_V1"] = "PREPARED_ALL_CHILD_OUTCOME_V1"
    source_request_fingerprint: Digest
    appended_evidence: AllChildEvidenceRecord | None
    parent: AllChildOutcomeParent
    obligation: EffectsObligationRevision | None
    reduction: EffectsSemanticReductionRecord
    complete_batch_fingerprint: Digest


ScopedDispatchRequest = Annotated[
    PrepareScopedAuthorization
    | PrepareScopedPreSendDisposition
    | PrepareScopedFirstSend
    | PrepareAllChildEvidence
    | PrepareAllChildReconciliation,
    Field(discriminator="kind"),
]
ScopedDispatchResult = (
    Annotated[
        PreparedScopedAuthorization
        | PreparedScopedPreSendDisposition
        | PreparedScopedFirstSend
        | PreparedAllChildOutcome,
        Field(discriminator="kind"),
    ]
    | DispatchBoundaryFailureV2
)


class ScopedDispatchPreparationPort(Protocol):
    async def prepare_scoped_dispatch(
        self, request: ScopedDispatchRequest
    ) -> ScopedDispatchResult: ...
