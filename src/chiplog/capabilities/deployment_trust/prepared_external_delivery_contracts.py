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
