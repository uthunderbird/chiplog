"""Inert prepared external self-send grant wires, never proof of permission.

Only the registered deployment_trust owner may issue these values from explicit
communication/disclosure authority. R17 provenance, R16 resource availability and
HermeticOutputScopeV1 (external_delivery=False) cannot supply that authority.
The future live reader must authenticate physical selection, owner lineage and
all current sources under AuthorityGate at publication and the consuming fence.
This module registers no record kind and implements neither issuer nor reader.
"""

import hashlib
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead, ProviderRecipient

from .cli_custody_contracts import CliCustodyDTO, Digest, Identity, UInt64
from .hermetic_output_scope_contracts import HermeticTrustObservationV1
from .prepared_external_delivery_policy_contracts import (
    PreparedExternalSelfDeliveryPolicyAnchorV1,
    PreparedExternalSelfDeliveryPolicyV1,
)
from .prepared_external_delivery_policy_owner_contracts import (
    prepared_self_delivery_policy_content_head,
)


class SelectedExternalDeliverySourceV1(CliCustodyDTO):
    """Exact selected R17 origin; references require independent retained-byte reads.

    The physical record hashes canonical retained admitted.record bytes. The
    authentication ref hashes its exact retained owner response. Initialization
    identifies the selected H0 envelope containing that admission and resources.
    Equal prompt text or an authenticated principal cannot substitute this source.
    """

    source_class: Literal["CLI", "TELEGRAM_PUSH", "TELEGRAM_POLL"]
    selected_initialization: ExactHead
    selected_admission_decision: ExactHead
    selected_admission_record: ExactHead
    admitted_authentication: ExactHead
    ingress_binding: ExactHead


class ExternalDeliveryResourcesV1(CliCustodyDTO):
    """R16 current resource claim, not communication or disclosure permission.

    Observation fingerprint hashes canonical JSON [grant.hex(), credential.hex(),
    endpoint.hex(), clock_epoch, signature], UTF-8, ensure_ascii=False, compact
    separators. Reader resolves and authenticates the original signed bytes and
    independently checks current grant/credential lifecycle and exact recipient.
    """

    signature_domain: Literal["dispatch-resources.v1"]
    signed_observation_fingerprint: Digest
    resource_grant: ExactHead
    recipient: ProviderRecipient
    clock_epoch: Identity


class BoundedExternalSelfSendMandateV1(CliCustodyDTO):
    """Explicit permission for one exact prepared payload to the authenticated self.

    Self identity is established by the trust-owned self_recipient_binding, not
    by assuming provider recipient_id equals principal_id. The owner verifies
    both policy sources authorize this payload/context/recipient. max_calls is a
    bound, not evidence of unused budget: durable consumption is checked at SEND.
    Digest and byte length bind the exact rendered payload, not a text class.
    """

    purpose: Literal["PREPARED_EXTERNAL_SELF_SEND"]
    external_delivery: Literal[True]
    selection: Literal["ORIGIN_EXACT", "MODEL_SELECTED_EXACT"]
    payload_class: Literal["NonAuthoritativeText"]
    communication_authority: ExactHead
    disclosure_authority: ExactHead
    self_recipient_binding: ExactHead
    original_run: ExactHead
    captured_attempt: ExactHead
    preparation_basis: ExactHead
    delivery_id: Identity
    payload_digest: Digest
    payload_byte_length: int = Field(gt=0, le=65_536)
    max_calls: Literal[1]
    clock_contract: Identity
    clock_epoch: Identity
    not_before_ns: UInt64
    expires_at_ns: UInt64

    @model_validator(mode="after")
    def bounded_horizon(self) -> Self:
        if self.expires_at_ns <= self.not_before_ns:
            raise ValueError("external self-send mandate requires a nonempty horizon")
        return self


class PreparedExternalDeliveryGrantV1(CliCustodyDTO):
    schema_id: Literal["chiplog.deployment-trust.prepared-external-delivery-grant.v1"] = (
        "chiplog.deployment-trust.prepared-external-delivery-grant.v1"
    )
    issuer: Literal["deployment_trust"]
    tenant_id: Identity
    database_id: Identity
    grant_id: Identity
    revision: UInt64
    # Previous selected grant payload, not an unrelated latest trust decision.
    predecessor: ExactHead | None
    principal_id: Identity
    worker_session_id: Identity
    contour_head: Identity
    authenticated_credential_head: Identity
    authenticated_session_head: Identity
    selected_source: SelectedExternalDeliverySourceV1
    resources: ExternalDeliveryResourcesV1
    mandate: BoundedExternalSelfSendMandateV1

    @model_validator(mode="after")
    def joined_claims(self) -> Self:
        if (self.predecessor is None) != (self.revision == 0):
            raise ValueError("genesis grant requires revision zero and no predecessor")
        if self.resources.clock_epoch != self.mandate.clock_epoch:
            raise ValueError("grant resource and mandate clock epochs differ")
        if not self.resources.recipient.canonical_address:
            raise ValueError("external delivery requires an exact recipient address")
        return self


class PreparedExternalDeliveryGrantAnchorV1(CliCustodyDTO):
    """Physical trust journal/materialized envelope and logical grant payload heads.

    decision hashes the journal envelope; record hashes the entire materialized
    envelope at trust_records(decision_id, ordinal); grant hashes the canonical
    grant payload. No main-record or logical snapshot head may replace these.
    """

    owner_id: Literal["deployment_trust"]
    decision: ExactHead
    record_ordinal: UInt64
    record_type_id: Literal["chiplog.deployment_trust.prepared_external_delivery_grant"]
    schema_id: Literal["chiplog.deployment_trust.record.v1"]
    record: ExactHead
    grant: ExactHead
    revision: UInt64


class ReadCurrentPreparedExternalDeliveryGrantV1(CliCustodyDTO):
    """CAS-like expectations only; live reader must reject revocation/supersession.

    Reader independently captures current trust state, R17 selection, R16 resources
    and each mandate policy/self binding. Unknown readers/versions fail closed.
    Caller expectation equality and historical signatures alone are insufficient.
    """

    schema_id: Literal["chiplog.deployment-trust.read-current-external-delivery-grant.v1"] = (
        "chiplog.deployment-trust.read-current-external-delivery-grant.v1"
    )
    expected_trust_observation: HermeticTrustObservationV1
    source_anchor: PreparedExternalDeliveryGrantAnchorV1
    expected_grant: PreparedExternalDeliveryGrantV1

    @model_validator(mode="after")
    def exact_grant(self) -> Self:
        grant, anchor = self.expected_grant, self.source_anchor
        if (
            grant.grant_id != anchor.grant.identity
            or grant.revision != anchor.revision
            or hashlib.sha256(grant.canonical_bytes()).hexdigest() != anchor.grant.fingerprint
        ):
            raise ValueError("expected grant differs from exact selected anchor")
        return self


class CurrentPreparedExternalDeliveryGrantV1(CliCustodyDTO):
    """Inert observation; CURRENT proves no freshness outside the consuming fence."""

    disposition: Literal["CURRENT"]
    source_anchor: PreparedExternalDeliveryGrantAnchorV1
    trust_observation: HermeticTrustObservationV1
    selector_generation: UInt64


class NonCurrentPreparedExternalDeliveryGrantV1(CliCustodyDTO):
    disposition: Literal["STALE", "DENIED", "UNSUPPORTED"]


CurrentPreparedExternalDeliveryGrantResultV1 = Annotated[
    CurrentPreparedExternalDeliveryGrantV1 | NonCurrentPreparedExternalDeliveryGrantV1,
    Field(discriminator="disposition"),
]


# V2 is a separate lifecycle representation.  The V1 classes above deliberately
# remain import- and byte-compatible for historical records.
def prepared_external_delivery_grant_content_head_v2(canonical_grant_bytes: bytes) -> str:
    """Logical V2 payload revision, never evidence of permission or retention."""
    return hashlib.sha256(
        b"chiplog.deployment-trust.prepared-external-delivery-grant-content-head.v2\x00"
        + canonical_grant_bytes
    ).hexdigest()


class PreparedExternalDeliveryGrantScopeV2(CliCustodyDTO):
    """The exact V1 delivery scope retained by each V2 lifecycle revision."""

    principal_id: Identity
    worker_session_id: Identity
    contour_head: Identity
    authenticated_credential_head: Identity
    authenticated_session_head: Identity
    selected_source: SelectedExternalDeliverySourceV1
    resources: ExternalDeliveryResourcesV1
    mandate: BoundedExternalSelfSendMandateV1

    @model_validator(mode="after")
    def joined_claims(self) -> Self:
        if self.resources.clock_epoch != self.mandate.clock_epoch:
            raise ValueError("grant resource and mandate clock epochs differ")
        if not self.resources.recipient.canonical_address:
            raise ValueError("external delivery requires an exact recipient address")
        return self


class PreparedExternalDeliveryGrantV2(CliCustodyDTO):
    """Inert ACTIVE/REVOKED grant revision; construction establishes no authority."""

    schema_id: Literal["chiplog.deployment-trust.prepared-external-delivery-grant.v2"] = (
        "chiplog.deployment-trust.prepared-external-delivery-grant.v2"
    )
    issuer: Literal["deployment_trust"]
    tenant_id: Identity
    database_id: Identity
    grant_id: Identity
    revision: UInt64
    predecessor: ExactHead | None
    status: Literal["ACTIVE", "REVOKED"]
    selected_policy_anchor: PreparedExternalSelfDeliveryPolicyAnchorV1
    scope: PreparedExternalDeliveryGrantScopeV2
    authorization_command: ExactHead
    authorization_source: ExactHead

    @model_validator(mode="after")
    def lifecycle_and_policy_join(self) -> Self:
        if (self.predecessor is None) != (self.revision == 0):
            raise ValueError("genesis grant requires revision zero and no predecessor")
        if self.predecessor is not None and self.predecessor.identity != self.grant_id:
            raise ValueError("grant predecessor belongs to another grant")
        if self.status == "REVOKED" and self.predecessor is None:
            raise ValueError("revocation requires an existing grant revision")
        policy = self.selected_policy_anchor.policy
        authorities = (
            self.scope.mandate.communication_authority,
            self.scope.mandate.disclosure_authority,
            self.scope.mandate.self_recipient_binding,
        )
        if any(authority != policy for authority in authorities):
            raise ValueError("grant mandate authorities must equal selected policy payload")
        return self


class PreparedExternalDeliveryGrantAnchorV2(CliCustodyDTO):
    """Physical V2 grant retention anchor plus the canonical logical payload head."""

    owner_id: Literal["deployment_trust"]
    decision: ExactHead
    record_ordinal: UInt64
    record_type_id: Literal["chiplog.deployment_trust.prepared_external_delivery_grant"]
    schema_id: Literal["chiplog.deployment_trust.record.v1"]
    record: ExactHead
    grant: ExactHead
    revision: UInt64


def _require_exact_v2_grant_anchor(
    grant: PreparedExternalDeliveryGrantV2, anchor: PreparedExternalDeliveryGrantAnchorV2
) -> None:
    canonical = grant.canonical_bytes()
    expected = ExactHead(
        identity=grant.grant_id,
        head=prepared_external_delivery_grant_content_head_v2(canonical),
        fingerprint=hashlib.sha256(canonical).hexdigest(),
    )
    if anchor.grant != expected or anchor.revision != grant.revision:
        raise ValueError("grant anchor differs from exact selected grant")


def _require_exact_active_policy(
    policy_bytes: bytes,
    anchor: PreparedExternalSelfDeliveryPolicyAnchorV1,
    *,
    policy_id: str,
    tenant_id: str,
    database_id: str,
) -> None:
    policy = PreparedExternalSelfDeliveryPolicyV1.model_validate_json(policy_bytes)
    if policy.canonical_bytes() != policy_bytes:
        raise ValueError("selected policy bytes must be canonical")
    expected = ExactHead(
        identity=policy.policy_id,
        head=prepared_self_delivery_policy_content_head(policy_bytes),
        fingerprint=hashlib.sha256(policy_bytes).hexdigest(),
    )
    if anchor.policy != expected or anchor.revision != policy.revision:
        raise ValueError("selected policy anchor differs from policy bytes")
    if policy.status != "ACTIVE":
        raise ValueError("issue requires an ACTIVE selected policy")
    if (policy.tenant_id, policy.database_id, policy.policy_id) != (
        tenant_id,
        database_id,
        policy_id,
    ):
        raise ValueError("selected policy scope differs from grant request")


class IssuePreparedExternalDeliveryGrantRequestV2(CliCustodyDTO):
    """Signed ISSUE input; caller-provided values remain unauthoritative.

    ``expected_grant=None`` is create-only.  The future owner enforces that
    expectation against the durable latest-grant CAS at append time.
    """

    schema_id: Literal["chiplog.deployment-trust.issue-prepared-external-delivery-grant.v2"] = (
        "chiplog.deployment-trust.issue-prepared-external-delivery-grant.v2"
    )
    operation: Literal["ISSUE_PREPARED_EXTERNAL_DELIVERY_GRANT"]
    command_id: Identity
    tenant_id: Identity
    database_id: Identity
    grant_id: Identity
    policy_id: Identity
    expected_trust_observation: HermeticTrustObservationV1
    expected_grant: PreparedExternalDeliveryGrantAnchorV2 | None
    selected_policy_anchor: PreparedExternalSelfDeliveryPolicyAnchorV1
    canonical_selected_policy_bytes: bytes = Field(min_length=1)
    proposed_scope: PreparedExternalDeliveryGrantScopeV2

    @model_validator(mode="after")
    def issue_inputs(self) -> Self:
        if self.expected_grant is not None:
            if self.expected_grant.grant.identity != self.grant_id:
                raise ValueError("expected grant belongs to another grant")
            if self.expected_grant.revision == 2**64 - 1:
                raise ValueError("grant revision is exhausted")
        _require_exact_active_policy(
            self.canonical_selected_policy_bytes,
            self.selected_policy_anchor,
            policy_id=self.policy_id,
            tenant_id=self.tenant_id,
            database_id=self.database_id,
        )
        authorities = (
            self.proposed_scope.mandate.communication_authority,
            self.proposed_scope.mandate.disclosure_authority,
            self.proposed_scope.mandate.self_recipient_binding,
        )
        if any(authority != self.selected_policy_anchor.policy for authority in authorities):
            raise ValueError("proposed mandate authorities must equal selected policy payload")
        return self


class RevokePreparedExternalDeliveryGrantRequestV2(CliCustodyDTO):
    """Signed REVOKE input bound to an ACTIVE predecessor's exact retained scope.

    Policy and resource currentness are intentionally not checked here.  A future
    owner derives the revoked successor by copying ``expected_grant``'s policy
    anchor and scope, then changing only lifecycle lineage and authorization refs.
    """

    schema_id: Literal["chiplog.deployment-trust.revoke-prepared-external-delivery-grant.v2"] = (
        "chiplog.deployment-trust.revoke-prepared-external-delivery-grant.v2"
    )
    operation: Literal["REVOKE_PREPARED_EXTERNAL_DELIVERY_GRANT"]
    command_id: Identity
    tenant_id: Identity
    database_id: Identity
    grant_id: Identity
    policy_id: Identity
    expected_trust_observation: HermeticTrustObservationV1
    expected_grant_anchor: PreparedExternalDeliveryGrantAnchorV2
    expected_grant: PreparedExternalDeliveryGrantV2

    @model_validator(mode="after")
    def active_predecessor(self) -> Self:
        _require_exact_v2_grant_anchor(self.expected_grant, self.expected_grant_anchor)
        grant = self.expected_grant
        if grant.status != "ACTIVE":
            raise ValueError("revocation requires an ACTIVE predecessor grant")
        if (grant.tenant_id, grant.database_id, grant.grant_id) != (
            self.tenant_id,
            self.database_id,
            self.grant_id,
        ):
            raise ValueError("predecessor grant scope differs from revoke request")
        if grant.selected_policy_anchor.policy.identity != self.policy_id:
            raise ValueError("predecessor policy differs from revoke request")
        if grant.revision == 2**64 - 1:
            raise ValueError("grant revision is exhausted")
        return self


class ReadPreparedExternalDeliveryGrantLifecycleV2(CliCustodyDTO):
    """Lifecycle locator only; it does not claim currentness or SEND permission."""

    schema_id: Literal["chiplog.deployment-trust.read-external-delivery-grant-lifecycle.v2"] = (
        "chiplog.deployment-trust.read-external-delivery-grant-lifecycle.v2"
    )
    expected_trust_observation: HermeticTrustObservationV1
    source_anchor: PreparedExternalDeliveryGrantAnchorV2
    expected_grant: PreparedExternalDeliveryGrantV2

    @model_validator(mode="after")
    def exact_grant(self) -> Self:
        _require_exact_v2_grant_anchor(self.expected_grant, self.source_anchor)
        return self


class ObservedPreparedExternalDeliveryGrantLifecycleV2(CliCustodyDTO):
    """Untrusted lifecycle observation; the consuming authority gate rechecks it."""

    disposition: Literal["OBSERVED"] = "OBSERVED"
    status: Literal["ACTIVE", "REVOKED"]
    source_anchor: PreparedExternalDeliveryGrantAnchorV2
    trust_observation: HermeticTrustObservationV1
    selector_generation: UInt64


class UnobservedPreparedExternalDeliveryGrantLifecycleV2(CliCustodyDTO):
    disposition: Literal["STALE", "DENIED", "UNSUPPORTED"]


PreparedExternalDeliveryGrantLifecycleResultV2 = Annotated[
    ObservedPreparedExternalDeliveryGrantLifecycleV2
    | UnobservedPreparedExternalDeliveryGrantLifecycleV2,
    Field(discriminator="disposition"),
]
