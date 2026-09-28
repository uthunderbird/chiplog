"""Inert explicit operator policy wires for prepared external self delivery.

No policy is installed or granted by constructing these values. Existing CLI
principal authentication and operator MACs on trust journal envelopes do not
establish explicit authorization of these new commands. A future owner must
authenticate an operator source permitting the exact ISSUE/REVOKE request bytes,
tenant/database and policy scope. Unsupported authorization sources fail closed.
This module registers no durable kind and implements no issuer or reader.
"""

from typing import Literal, Self

from pydantic import Field, model_validator

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead, ProviderRecipient

from .cli_custody_contracts import CliCustodyDTO, Digest, Identity, UInt64
from .hermetic_output_scope_contracts import HermeticTrustObservationV1


class PreparedExternalSelfDeliveryPolicyTermsV1(CliCustodyDTO):
    """Joint scope; all three permission semantics must be checked independently.

    The operator explicitly attests that this exact recipient belongs to this
    principal. Equality of recipient_id and principal_id is never that proof.
    Communication permits SEND to this endpoint; disclosure permits only these
    exact rendered bytes; the self assertion binds the same principal/recipient.
    Any absent/mismatched semantic denies. No wildcard or inferred origin grant.
    Validity is [not_before_ns, expires_at_ns) in the named clock contract/epoch.
    max_calls constrains each derived grant; consumption is independently durable.
    """

    principal_id: Identity
    channel_id: Identity
    recipient: ProviderRecipient
    communication_permission: Literal["PREPARED_EXTERNAL_SEND"]
    disclosure_permission: Literal["EXACT_RENDERED_PAYLOAD"]
    self_recipient_semantics: Literal["OPERATOR_ATTESTED_SELF"]
    payload_class: Literal["NonAuthoritativeText"]
    payload_digest: Digest
    payload_byte_length: int = Field(gt=0, le=65_536)
    source_classes: tuple[Literal["CLI", "TELEGRAM_PUSH", "TELEGRAM_POLL"], ...] = Field(
        min_length=1, max_length=3
    )
    selection_modes: tuple[Literal["ORIGIN_EXACT", "MODEL_SELECTED_EXACT"], ...] = Field(
        min_length=1, max_length=2
    )
    external_delivery: Literal[True]
    max_calls: Literal[1]
    clock_contract: Identity
    clock_epoch: Identity
    not_before_ns: UInt64
    expires_at_ns: UInt64

    @model_validator(mode="after")
    def bounded_exact_scope(self) -> Self:
        if not self.recipient.canonical_address:
            raise ValueError("policy requires an exact nonempty recipient address")
        if self.expires_at_ns <= self.not_before_ns:
            raise ValueError("policy requires a nonempty validity horizon")
        if len(set(self.source_classes)) != len(self.source_classes):
            raise ValueError("policy source classes must be unique")
        if len(set(self.selection_modes)) != len(self.selection_modes):
            raise ValueError("policy selection modes must be unique")
        return self


class PreparedExternalSelfDeliveryPolicyV1(CliCustodyDTO):
    """Trust-owned immutable revision; status is explicit, with no default grant.

    authorization_command hashes the retained Issue/Revoke *request* bytes (not
    the wrapper carrying authorization_source), avoiding a circular signature.
    authorization_source locates the retained authenticated operator decision
    over that request. The owner verifies their bytes, scope and authenticity.
    Revocation appends a successor preserving terms; it never deletes history.
    """

    schema_id: Literal["chiplog.deployment-trust.prepared-self-delivery-policy.v1"] = (
        "chiplog.deployment-trust.prepared-self-delivery-policy.v1"
    )
    issuer: Literal["deployment_trust"]
    tenant_id: Identity
    database_id: Identity
    policy_id: Identity
    revision: UInt64
    predecessor: ExactHead | None
    status: Literal["ACTIVE", "REVOKED"]
    terms: PreparedExternalSelfDeliveryPolicyTermsV1
    authorization_command: ExactHead
    authorization_source: ExactHead

    @model_validator(mode="after")
    def policy_lineage(self) -> Self:
        if (self.predecessor is None) != (self.revision == 0):
            raise ValueError("policy genesis requires revision zero and no predecessor")
        if self.predecessor is not None and self.predecessor.identity != self.policy_id:
            raise ValueError("policy predecessor belongs to another policy")
        if self.status == "REVOKED" and self.predecessor is None:
            raise ValueError("revocation requires an existing policy revision")
        return self


class PreparedExternalSelfDeliveryPolicyAnchorV1(CliCustodyDTO):
    """Exact physical envelopes plus logical payload, never a latest-head claim.

    decision hashes the retained journal envelope; record hashes the complete
    materialized envelope at (decision.identity, record_ordinal); policy hashes
    canonical PreparedExternalSelfDeliveryPolicyV1 bytes. The future reader must
    authenticate all three and enumerate latest revisions under AuthorityGate.
    """

    owner_id: Literal["deployment_trust"]
    decision: ExactHead
    record_ordinal: UInt64
    record_type_id: Literal["chiplog.deployment_trust.prepared_self_delivery_policy"]
    schema_id: Literal["chiplog.deployment_trust.record.v1"]
    record: ExactHead
    policy: ExactHead
    revision: UInt64


class IssuePreparedExternalSelfDeliveryPolicyRequestV1(CliCustodyDTO):
    """Operator-authenticated content; expected_policy=None means create-only.

    At commit the owner atomically compares BOTH trust observation and physical
    latest policy anchor. It denies absent/duplicate/superseded predecessors,
    derives revision zero or predecessor+1, and stores ACTIVE explicitly.
    Updating terms never retroactively changes an earlier derived grant.
    """

    schema_id: Literal["chiplog.deployment-trust.issue-self-delivery-policy-request.v1"] = (
        "chiplog.deployment-trust.issue-self-delivery-policy-request.v1"
    )
    operation: Literal["ISSUE_PREPARED_EXTERNAL_SELF_DELIVERY_POLICY"]
    command_id: Identity
    tenant_id: Identity
    database_id: Identity
    policy_id: Identity
    expected_trust_observation: HermeticTrustObservationV1
    expected_policy: PreparedExternalSelfDeliveryPolicyAnchorV1 | None
    terms: PreparedExternalSelfDeliveryPolicyTermsV1

    @model_validator(mode="after")
    def expected_policy_identity(self) -> Self:
        if self.expected_policy is not None:
            if self.expected_policy.policy.identity != self.policy_id:
                raise ValueError("expected head belongs to another policy")
            if self.expected_policy.revision == 2**64 - 1:
                raise ValueError("policy revision is exhausted")
        return self


class RevokePreparedExternalSelfDeliveryPolicyRequestV1(CliCustodyDTO):
    """Append REVOKED with the exact prior terms and predecessor revision+1.

    Same atomic trust/latest-policy CAS and command authorization as ISSUE.
    Historical ACTIVE references cannot bypass a current revocation. Revocation
    invalidates all grants derived from this policy at their next consuming fence.
    """

    schema_id: Literal["chiplog.deployment-trust.revoke-self-delivery-policy-request.v1"] = (
        "chiplog.deployment-trust.revoke-self-delivery-policy-request.v1"
    )
    operation: Literal["REVOKE_PREPARED_EXTERNAL_SELF_DELIVERY_POLICY"]
    command_id: Identity
    tenant_id: Identity
    database_id: Identity
    policy_id: Identity
    expected_trust_observation: HermeticTrustObservationV1
    expected_policy: PreparedExternalSelfDeliveryPolicyAnchorV1

    @model_validator(mode="after")
    def expected_policy_identity(self) -> Self:
        if self.expected_policy.policy.identity != self.policy_id:
            raise ValueError("expected head belongs to another policy")
        if self.expected_policy.revision == 2**64 - 1:
            raise ValueError("policy revision is exhausted")
        return self


class IssuePreparedExternalSelfDeliveryPolicyV1(CliCustodyDTO):
    """Source must authorize ISSUE and request.canonical_bytes(), not CLI login."""

    request: IssuePreparedExternalSelfDeliveryPolicyRequestV1
    authenticated_operator_source: ExactHead


class RevokePreparedExternalSelfDeliveryPolicyV1(CliCustodyDTO):
    """Source must authorize REVOKE and request.canonical_bytes(), not a journal MAC."""

    request: RevokePreparedExternalSelfDeliveryPolicyRequestV1
    authenticated_operator_source: ExactHead
