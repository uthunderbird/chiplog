"""Owner-local immutable external-effect boundary; no dispatch implementation."""

from __future__ import annotations

import json
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .fences import WorkerFence


class _Frozen(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        ser_json_bytes="base64",
        val_json_bytes="base64",
    )

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()


class ExactHead(_Frozen):
    subject_id: str = Field(min_length=1)
    head: str = Field(min_length=1)
    fingerprint: str = Field(min_length=1)


class DispatchSemanticBinding(_Frozen):
    normative_manifest: str = Field(min_length=1)
    reducer_version: str = Field(min_length=1)
    transition_registry_version: str = Field(min_length=1)
    canonicalization_fingerprint_version: str = Field(min_length=1)
    adapter_contract_version: str = Field(min_length=1)


AttemptState = Literal[
    "INTENT_RECORDED",
    "HELD_BEFORE_SEND",
    "CANCELLED_BEFORE_SEND",
    "SUPERSEDED_BEFORE_SEND",
    "DISPATCH_AUTHORIZED",
    "SEND_COMMITTED",
    "SENT",
    "CONFIRMED",
    "FAILED_NO_EFFECT",
    "OUTCOME_UNKNOWN",
    "PARTIAL",
    "PARTIAL_CONFIRMED",
]


class ProviderRecipient(_Frozen):
    provider: str = Field(min_length=1)
    account: str = Field(min_length=1)
    recipient: str = Field(min_length=1)
    endpoint: ExactHead
    canonical_address: bytes = Field(min_length=1)
    credential_binding: ExactHead


class DirectAuthorityAct(_Frozen):
    kind: Literal["DIRECT_AUTHENTICATED_ACT"]
    act: ExactHead
    ingress: ExactHead


class AdoptedAuthorityAct(_Frozen):
    kind: Literal["EXACT_PROPOSAL_ADOPTION"]
    proposal: ExactHead
    display_digest: str = Field(min_length=1)
    adoption: ExactHead
    ingress: ExactHead
    interpretation: ExactHead


class BoundedMandateAct(_Frozen):
    kind: Literal["BOUNDED_MANDATE"]
    mandate: ExactHead
    operation: str = Field(min_length=1)
    result_manifest_digest: str = Field(min_length=1)


AuthorityAct = Annotated[
    DirectAuthorityAct | AdoptedAuthorityAct | BoundedMandateAct, Field(discriminator="kind")
]


class AuthorityRead(_Frozen):
    source_id: str = Field(min_length=1)
    source_version: str = Field(min_length=1)
    head: ExactHead
    generation: str = Field(min_length=1)
    frontier: str = Field(min_length=1)
    valid_until_ns: int = Field(gt=0)
    canonical_value: bytes


class AuthorityBinding(_Frozen):
    tenant_id: str = Field(min_length=1)
    principal_id: str = Field(min_length=1)
    actor_id: str = Field(min_length=1)
    authenticated_session: ExactHead
    act: AuthorityAct
    planning_revision: ExactHead
    authorization_evidence: ExactHead
    authority_sources: tuple[ExactHead, ...] = Field(min_length=1)
    affected_party_constraints: tuple[ExactHead, ...]
    hold_conflict_order: ExactHead
    dependencies: tuple[ExactHead, ...]
    factual_assertion_evidence: tuple[ExactHead, ...]
    verification_contradiction: tuple[ExactHead, ...]
    authority_applicability: tuple[ExactHead, ...]
    consequence_scope: ExactHead
    communication_mandate: ExactHead
    disclosure_projection: ExactHead
    channel_class: str = Field(min_length=1)
    interaction_context: ExactHead
    recipient: ProviderRecipient
    reads: tuple[AuthorityRead, ...] = Field(min_length=1)
    registry_inputs: tuple[tuple[str, str], ...]
    valid_until_ns: int = Field(gt=0)


class OriginSelection(_Frozen):
    kind: Literal["ORIGIN_EXACT"]
    ingress_binding: ExactHead
    recipient: ProviderRecipient


class ModelSelection(_Frozen):
    kind: Literal["MODEL_SELECTED_EXACT"]
    recipient: ProviderRecipient


class DeliverySendBinding(_Frozen):
    delivery_id: str = Field(min_length=1)
    acceptance: ExactHead
    render_digest: str = Field(min_length=1)
    manifest_digest: str = Field(min_length=1)
    selection: Annotated[OriginSelection | ModelSelection, Field(discriminator="kind")]
    visibility: tuple[ExactHead, ...] = Field(min_length=1)
    provenance: tuple[ExactHead, ...]
    disclosure: tuple[ExactHead, ...] = Field(min_length=1)
    narrowing: tuple[ExactHead, ...]
    policy: ExactHead


class OrdinaryPurpose(_Frozen):
    kind: Literal["ORDINARY_EFFECT"]


class DeliveryPurpose(_Frozen):
    kind: Literal["DELIVERY"]
    binding: DeliverySendBinding


class OriginalAmbiguity(_Frozen):
    original_intent: ExactHead
    original_binding: DispatchSemanticBinding
    ambiguity_reconciliation_head: ExactHead


class CompensationPurpose(_Frozen):
    kind: Literal["COMPENSATION"]
    original: OriginalAmbiguity
    consequence_addressed: ExactHead
    preview_adoption: AdoptedAuthorityAct


class DuplicateRiskPurpose(_Frozen):
    kind: Literal["AUTHORIZE_DUPLICATE_RISK"]
    unresolved_attempts: tuple[OriginalAmbiguity, ...] = Field(min_length=1)
    possible_duplicate_effects: tuple[ExactHead, ...] = Field(min_length=1)
    affected_parties_resources: tuple[ExactHead, ...] = Field(min_length=1)
    commitment_consequences: tuple[ExactHead, ...] = Field(min_length=1)
    safer_alternatives: tuple[ExactHead, ...] = Field(min_length=1)
    preview_adoption: AdoptedAuthorityAct


EffectPurpose = Annotated[
    OrdinaryPurpose | DeliveryPurpose | CompensationPurpose | DuplicateRiskPurpose,
    Field(discriminator="kind"),
]


class ExternalActionIntent(_Frozen):
    intent_id: str = Field(min_length=1)
    fingerprint: str = Field(min_length=1)
    authority: AuthorityBinding
    semantics: DispatchSemanticBinding
    payload: bytes = Field(min_length=1)
    effect_fingerprint: str = Field(min_length=1)
    idempotency_fence_key: str = Field(min_length=1)
    inseparable_bundle_members: tuple[ExactHead, ...] = Field(min_length=1)
    purpose: EffectPurpose


class CommandIdentity(_Frozen):
    command_id: str = Field(min_length=1)
    fingerprint: str = Field(min_length=1)
    expected_tenant_head: int = Field(ge=0)


class AcceptEffectCommand(_Frozen):
    identity: CommandIdentity
    initialized_call: ExactHead
    call_id: str = Field(min_length=1)
    tool_schema_policy: tuple[ExactHead, ...] = Field(min_length=1)
    fence: WorkerFence
    intent: ExternalActionIntent
    execution_intent: ExactHead
    complete_acceptance_manifest: tuple[ExactHead, ...] = Field(min_length=1)


class PublishPlanEffectCommand(_Frozen):
    identity: CommandIdentity
    intent: ExternalActionIntent
    planning_publication: ExactHead
    planning_owner_bytes: bytes = Field(min_length=1)
    complete_publication_manifest: tuple[ExactHead, ...] = Field(min_length=1)
    fence: WorkerFence


class PublishDeliveryIntentCommand(_Frozen):
    """Prepare effects bytes for the one CompleteAcceptance invariant batch."""

    identity: CommandIdentity
    intent: ExternalActionIntent
    complete_acceptance: ExactHead
    complete_delivery_manifest: tuple[ExactHead, ...] = Field(min_length=1)
    fence: WorkerFence


class DispatchAuthorization(_Frozen):
    authorization: ExactHead
    intent: ExactHead
    expected_attempt: ExactHead
    semantics: DispatchSemanticBinding
    current_authority: AuthorityBinding
    fence: WorkerFence


class AuthorizeDispatchCommand(_Frozen):
    identity: CommandIdentity
    intent: ExactHead
    expected_attempt: ExactHead
    current_authority: AuthorityBinding
    semantics: DispatchSemanticBinding
    fence: WorkerFence


class BeforeSendDispositionCommand(_Frozen):
    identity: CommandIdentity
    intent: ExactHead
    expected_attempt: ExactHead
    disposition: Literal["HELD_BEFORE_SEND", "CANCELLED_BEFORE_SEND", "SUPERSEDED_BEFORE_SEND"]
    current_authority: AuthorityBinding
    decision_evidence: ExactHead
    fence: WorkerFence


class PublishRecoveryIntentCommand(_Frozen):
    identity: CommandIdentity
    intent: ExternalActionIntent
    original_heads: tuple[ExactHead, ...] = Field(min_length=1)
    current_authority: AuthorityBinding
    fence: WorkerFence


class FirstTransmission(_Frozen):
    kind: Literal["FIRST_TRANSMISSION"]
    ordinal: Literal[0]


class SafeRetransmission(_Frozen):
    kind: Literal["SAFE_RETRANSMISSION"]
    ordinal: int = Field(gt=0, le=2**64 - 1)
    prior_children: tuple[ExactHead, ...] = Field(min_length=1)
    proof_kind: Literal["PROVIDER_IDEMPOTENCY_COVERS_ALL", "ALL_PRIOR_PERMANENTLY_INCAPABLE"]
    proof: ExactHead
    covered_effect_fingerprint: str = Field(min_length=1)
    coverage_starts_ns: int = Field(ge=0)
    coverage_expires_ns: int = Field(gt=0)


class CommitSendCommand(_Frozen):
    identity: CommandIdentity
    expected_attempt: ExactHead
    authorization: DispatchAuthorization
    transmission: Annotated[FirstTransmission | SafeRetransmission, Field(discriminator="kind")]
    current_time_ns: int = Field(ge=0)


class TransmissionAttempt(_Frozen):
    transmission: ExactHead
    intent: ExactHead
    ordinal: int = Field(ge=0, le=2**64 - 1)
    semantics: DispatchSemanticBinding
    dispatch_time_ns: int = Field(ge=0)
    payload_fingerprint: str = Field(min_length=1)
    recipient: ProviderRecipient
    idempotency_fence_key: str = Field(min_length=1)
    send_commit: ExactHead
    coverage_proof: ExactHead | None


class EvidenceAuthentication(_Frozen):
    kind: Literal["AUTHENTICATED_PROVIDER_EVIDENCE"]
    broker_custody: ExactHead
    ingress_surface: str = Field(min_length=1)
    source_contract_version: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    source_identity: str = Field(min_length=1)
    provider_account: str = Field(min_length=1)
    endpoint: ExactHead
    credential_key: ExactHead
    audience: str = Field(min_length=1)
    raw_digest: str = Field(min_length=1)
    source_replay_identity: str = Field(min_length=1)
    intent: ExactHead
    transmission: ExactHead
    freshness: ExactHead


class TransportObservationBinding(_Frozen):
    kind: Literal["BROKER_TRANSPORT_OBSERVATION"]
    tenant_id: str = Field(min_length=1)
    broker_epoch: ExactHead
    issued_operation: ExactHead
    transmission: ExactHead
    exact_recipient: ProviderRecipient
    adapter_contract_version: str = Field(min_length=1)
    raw_digest: str = Field(min_length=1)


class RecordEvidenceCommand(_Frozen):
    identity: CommandIdentity
    expected_attempt: ExactHead
    evidence_id: str = Field(min_length=1)
    raw_bytes: bytes = Field(min_length=1)
    authentication: Annotated[
        EvidenceAuthentication | TransportObservationBinding, Field(discriminator="kind")
    ]
    semantics: DispatchSemanticBinding
    observation: Literal[
        "TRANSPORT_SENT",
        "EXACT_EFFECT_CONFIRMED",
        "PERMANENTLY_INCAPABLE",
        "ABSENCE_OBSERVED",
        "TIMEOUT",
        "CONNECTION_LOST",
        "MALFORMED_RESPONSE",
        "CONFLICTING_EVIDENCE",
        "INCOMPLETE_EVIDENCE",
        "MIXED_BUNDLE",
    ]
    covered_children: tuple[ExactHead, ...]
    occurred_members: tuple[ExactHead, ...]
    permanently_incapable_members: tuple[ExactHead, ...]


class ReconcileEffectCommand(_Frozen):
    identity: CommandIdentity
    intent: ExactHead
    expected_attempt: ExactHead
    complete_children: tuple[ExactHead, ...] = Field(min_length=1)
    durable_evidence: tuple[ExactHead, ...] = Field(min_length=1)
    semantics: DispatchSemanticBinding
    authorized_reconciler: ExactHead
    fence: WorkerFence


class OpenEffectObligation(_Frozen):
    kind: Literal["OPEN"] = "OPEN"
    owner: Literal["effects"] = "effects"
    obligation: ExactHead
    intent: ExactHead
    original_attempt: ExactHead
    original_crossed_children: tuple[ExactHead, ...] = Field(min_length=1)
    semantics: DispatchSemanticBinding
    closure_predicate: Literal["ALL_CHILDREN_TERMINAL_REDUCED_V1"] = (
        "ALL_CHILDREN_TERMINAL_REDUCED_V1"
    )
    safe_actions: tuple[
        Literal[
            "AUTHENTICATED_PROVIDER_READ", "BOUND_RECONCILIATION", "EXPLICIT_RECOVERY_AUTHORIZATION"
        ],
        ...,
    ]
    denied_actions: tuple[
        Literal["BLIND_RETRY", "CONFLICTING_REPLACEMENT", "DEPENDENT_CONTINUATION"], ...
    ]


class ClosedEffectObligation(_Frozen):
    kind: Literal["CLOSED"] = "CLOSED"
    obligation: ExactHead
    opening: OpenEffectObligation
    closure_evidence: tuple[ExactHead, ...] = Field(min_length=1)
    complete_children: tuple[ExactHead, ...] = Field(min_length=1)
    outcome: Literal["CONFIRMED", "FAILED_NO_EFFECT", "PARTIAL_CONFIRMED"]
    semantics: DispatchSemanticBinding


EffectRecoveryObligation = Annotated[
    OpenEffectObligation | ClosedEffectObligation, Field(discriminator="kind")
]


class EffectSnapshot(_Frozen):
    intent: ExternalActionIntent
    attempt: ExactHead
    state: AttemptState
    authorizations: tuple[DispatchAuthorization, ...]
    transmissions: tuple[TransmissionAttempt, ...]
    evidence: tuple[ExactHead, ...]
    unresolved_obligations: tuple[ExactHead, ...]
    evidence_records: tuple[RecordEvidenceCommand, ...] = ()
    recovery_obligation: EffectRecoveryObligation | None = None


class EffectRecord(_Frozen):
    record: ExactHead
    command: CommandIdentity
    predecessor: ExactHead | None
    kind: Literal[
        "INTENT_ACCEPTED",
        "PLAN_EFFECT_PUBLISHED",
        "DELIVERY_PREPARED",
        "RECOVERY_INTENT_PUBLISHED",
        "DISPATCH_AUTHORIZED",
        "BEFORE_SEND_DISPOSITION",
        "SEND_COMMITTED",
        "TRANSMISSION_ALLOCATED",
        "EVIDENCE_RECORDED",
        "RECONCILED",
    ]
    snapshot: EffectSnapshot
    source_command: bytes = Field(min_length=1)


class EffectStoreSnapshot(_Frozen):
    tenant_id: str = Field(min_length=1)
    tenant_head: int = Field(ge=0)
    records: tuple[EffectRecord, ...]


class PreparedEffectPublication(_Frozen):
    """Owner bytes for a registered broker batch; construction grants no authority."""

    record: EffectRecord
    exact_companion_manifest: tuple[ExactHead, ...]
    expected_store: EffectStoreSnapshot


class EffectCommitted(_Frozen):
    disposition: Literal["COMMITTED", "REPLAY"]
    command_id: str
    journal_decision: ExactHead
    snapshot: EffectSnapshot


class EffectDenied(_Frozen):
    disposition: Literal[
        "CONFLICT",
        "STALE",
        "DENIED",
        "INDETERMINATE",
        "RECOVERY_HOLD",
        "DISPATCH_VERSION_DENIED",
        "DISPATCH_VERSION_HOLD",
    ]
    command_id: str
    reason: str = Field(min_length=1)


EffectResult = EffectCommitted | EffectDenied


class EffectsPort(Protocol):
    async def accept(self, command: AcceptEffectCommand) -> EffectResult: ...
    async def publish_plan_effect(self, command: PublishPlanEffectCommand) -> EffectResult: ...
    async def prepare_delivery(
        self, command: PublishDeliveryIntentCommand
    ) -> PreparedEffectPublication | EffectDenied: ...
    async def authorize(self, command: AuthorizeDispatchCommand) -> EffectResult: ...
    async def before_send(self, command: BeforeSendDispositionCommand) -> EffectResult: ...
    async def publish_recovery_intent(
        self, command: PublishRecoveryIntentCommand
    ) -> EffectResult: ...
    async def commit_send(self, command: CommitSendCommand) -> EffectResult: ...
    async def record_evidence(self, command: RecordEvidenceCommand) -> EffectResult: ...
    async def reconcile(self, command: ReconcileEffectCommand) -> EffectResult: ...


class EffectsQueryPort(Protocol):
    def snapshot(self, intent: ExactHead) -> EffectSnapshot | EffectDenied: ...


class EffectsPublicationPort(Protocol):
    """Consumer-owned storage seam; broker chooses the registered handler internally.

    Delivery preparation enters CompleteAcceptance's combined transaction; it is
    not independently committed through this method after acceptance.
    """

    def snapshot(self) -> EffectStoreSnapshot: ...
    def replay(self, command: EffectCommand) -> EffectResult | None: ...
    async def publish(self, publication: PreparedEffectPublication) -> EffectResult: ...


class CurrentEffectInputs(_Frozen):
    """Broker-reproduced observation, never authority because a caller constructs it."""

    command_id: str = Field(min_length=1)
    command_fingerprint: str = Field(min_length=1)
    store_frontier: int = Field(ge=0)
    observed_time_ns: int = Field(ge=0)
    authority: AuthorityBinding
    supported_semantics: DispatchSemanticBinding | None
    fence: WorkerFence
    authority_decision: ExactHead
    # Complete broker-derived conflict/dependency scope, expressed as current
    # logical attempt heads. The writer independently reproduces this inventory.
    blocking_effect_heads: tuple[ExactHead, ...]
    current_original_ambiguity_heads: tuple[ExactHead, ...]
    initialized_call: ExactHead | None
    active_run_head: str | None
    independently_verified_safe_proof: SafeRetransmission | None
    authenticated_evidence: RecordEvidenceCommand | None
    original_reducer_semantics: DispatchSemanticBinding | None
    authorized_reconciler: ExactHead | None


EffectCommand = (
    AcceptEffectCommand
    | PublishPlanEffectCommand
    | PublishDeliveryIntentCommand
    | AuthorizeDispatchCommand
    | BeforeSendDispositionCommand
    | PublishRecoveryIntentCommand
    | CommitSendCommand
    | RecordEvidenceCommand
    | ReconcileEffectCommand
)


class EffectsObservationPort(Protocol):
    def observe(
        self, command: EffectCommand, expected: EffectStoreSnapshot
    ) -> CurrentEffectInputs: ...


class EffectPreparationRequest(_Frozen):
    operation: Literal[
        "effects.accept_call",
        "effects.publish_plan_effect",
        "effects.prepare_delivery",
        "effects.publish_recovery_intent",
        "effects.authorize",
        "effects.before_send",
        "effects.commit_send",
        "effects.record_evidence",
        "effects.reconcile",
    ]
    command_bytes: bytes = Field(min_length=1)
    expected: EffectStoreSnapshot
    current: CurrentEffectInputs
