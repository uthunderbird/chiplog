"""Inert H1 values, never issuance or evidence of current authority.

Even a self-consistent forgery can deserialize. The owner must authenticate the
journal lineage and exact materialized trust row under the shared authority gate.
These new wires do not register a durable record kind or implement an issuer.
"""

import hashlib
from typing import Annotated, Literal, Protocol, Self

from pydantic import Field, model_validator

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead, ProviderRecipient

from . import TrustReference
from .cli_custody_contracts import CliCustodyDTO, Digest, Identity, UInt64


class SelectedHermeticResourceObservationRefV1(CliCustodyDTO):
    """Locator for the selected R16 observation in retained inbox initialization.

    Fingerprint is SHA256 of canonical JSON [grant.hex(), credential.hex(),
    endpoint.hex(), clock_epoch, signature], using UTF-8, ensure_ascii=False and
    separators=(',', ':'). It binds exact bytes, not their authenticity. The
    registered reader must authenticate selection and R16 historical/current
    signatures under the shared gate. No dispatch grant becomes a disclosure or
    SEND permit by inclusion here.
    """

    signature_domain: Literal["dispatch-resources.v1"]
    selected_initialization: ExactHead
    signed_observation_fingerprint: Digest


class HermeticOutputPolicyV1(CliCustodyDTO):
    endpoint_ref: ExactHead
    selected_resource_observation_ref: SelectedHermeticResourceObservationRefV1
    selection: Literal["ORIGIN_EXACT"]
    ingress_class: Literal["AUTHENTICATED_R17_CLI"]
    payload_class: Literal["NonAuthoritativeText"]
    purpose: Literal["H1_LOCAL_COMMENTARY"]
    external_delivery: Literal[False]
    attempt_ordinal: Literal[0]
    call_count: Literal[0]


class HermeticOutputSourceV1(CliCustodyDTO):
    """Trust-owned disclosure source; R16 owns endpoint and credential bytes."""

    field_path: Literal["disclosure_policy"]
    ref: ExactHead
    canonical_source_bytes: bytes = Field(min_length=1)

    @model_validator(mode="after")
    def exact_bytes(self) -> Self:
        body = HermeticOutputPolicyV1.model_validate_json(self.canonical_source_bytes)
        if body.canonical_bytes() != self.canonical_source_bytes:
            raise ValueError("source bytes must use their registered canonicalization")
        if hashlib.sha256(self.canonical_source_bytes).hexdigest() != self.ref.fingerprint:
            raise ValueError("source fingerprint mismatch")
        return self


class HermeticOutputScopeV1(CliCustodyDTO):
    schema_id: Literal["chiplog.deployment-trust.hermetic-execution-scope.v1"] = (
        "chiplog.deployment-trust.hermetic-execution-scope.v1"
    )
    kind: Literal["HERMETIC_EXECUTION_SCOPE_V1"] = "HERMETIC_EXECUTION_SCOPE_V1"
    issuer: Literal["deployment_trust"]
    source_profile: Literal["chiplog.execution.h1-cli-hermetic-source-profile.v1"]
    slot: Literal["h1-cli-effects-origin"]
    tenant_id: Literal["hermetic-tenant"]
    database_id: Identity
    scope_id: Identity
    revision: UInt64
    predecessor: ExactHead | None
    principal_id: Literal["hermetic-principal"]
    worker_session_id: Identity
    contour_head: Identity
    admitted_authentication: ExactHead
    trust_state: ExactHead
    credential_state: ExactHead
    session_state: ExactHead
    recipient: ProviderRecipient
    selected_resource_observation_ref: SelectedHermeticResourceObservationRefV1
    disclosure_policy: HermeticOutputSourceV1
    mandate_applicability: Literal["HERMETIC_EFFECTS_ORIGIN_NO_EXTERNAL_ACTION_V1"]
    mandate_profile: Literal["h1-cli-effects-origin-zero-call-v1"]
    mandate_inventory_complete: Literal[True]
    ordered_mandates: tuple[ExactHead, ...] = Field(max_length=0)

    @model_validator(mode="after")
    def joined_sources(self) -> Self:
        policy = HermeticOutputPolicyV1.model_validate_json(
            self.disclosure_policy.canonical_source_bytes
        )
        recipient = self.recipient
        if (
            recipient.provider_id != "hermetic-effects"
            or recipient.account_id != "hermetic-account"
            or recipient.recipient_id != self.principal_id
            or recipient.canonical_address != b"hermetic://effects/hermetic-principal"
            or policy.endpoint_ref != recipient.endpoint
            or policy.selected_resource_observation_ref != self.selected_resource_observation_ref
        ):
            raise ValueError("scope source join mismatch")
        refs = (
            recipient.endpoint,
            recipient.credential_binding,
            self.disclosure_policy.ref,
        )
        if len({ref.identity for ref in refs}) != len(refs):
            raise ValueError("embedded source identities must be distinct")
        return self


class HermeticOutputScopeAnchorV1(CliCustodyDTO):
    """Locator for trust_records(decision_id, ordinal), not main records.record_id.

    Decision fingerprint hashes journal envelope bytes; record fingerprint hashes
    the full materialized record envelope, not merely its nested scope payload.
    Owner validation resolves both byte strings and checks revision/predecessor.
    """

    owner_id: Literal["deployment_trust"]
    decision: ExactHead
    record_ordinal: UInt64
    record_type_id: Literal["chiplog.deployment_trust.hermetic_output_scope"]
    schema_id: Literal["chiplog.deployment_trust.record.v1"]
    record: ExactHead
    scope_revision: UInt64
    predecessor: ExactHead | None
    selected_resource_observation_ref: SelectedHermeticResourceObservationRefV1


class HermeticTrustObservationV1(CliCustodyDTO):
    """Distinct physical journal envelope and logical owner snapshot heads.

    Physical identity/head are the actual journal decision_id, fingerprint hashes
    its authenticated envelope. Logical head is owner_snapshot_entries()'s final
    ID, never substituted for a physical materialized decision locator.
    """

    physical_journal_head: ExactHead
    logical_snapshot_head: Identity


class ReadCurrentHermeticExecutionScopeV1(CliCustodyDTO):
    schema_id: Literal["chiplog.deployment-trust.read-current-hermetic-output-scope.v1"] = (
        "chiplog.deployment-trust.read-current-hermetic-output-scope.v1"
    )
    expected_trust_observation: HermeticTrustObservationV1
    source_anchor: HermeticOutputScopeAnchorV1
    expected_revision: UInt64 = Field(strict=True)
    admitted_authentication_ref: ExactHead
    authenticated_cli_ref: TrustReference
    tenant_id: Identity
    database_id: Identity
    scope_id: Identity
    expected_scope_ref: ExactHead
    expected_worker_session_id: Identity
    selected_resource_observation_ref: SelectedHermeticResourceObservationRefV1

    @model_validator(mode="after")
    def exact_anchor(self) -> Self:
        if (
            self.expected_revision != self.source_anchor.scope_revision
            or self.selected_resource_observation_ref
            != self.source_anchor.selected_resource_observation_ref
        ):
            raise ValueError("current scope request differs from its exact anchor")
        return self


class CurrentHermeticExecutionScopeV1(CliCustodyDTO):
    """Untrusted observation until authenticated by the owner at the consuming fence."""

    disposition: Literal["CURRENT"]
    scope_ref: ExactHead
    source_anchor: HermeticOutputScopeAnchorV1
    selector_generation: UInt64
    ordered_current_source_refs: tuple[ExactHead, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_sources(self) -> Self:
        refs = self.ordered_current_source_refs
        if len({ref.identity for ref in refs}) != len(refs):
            raise ValueError("duplicate current source identity")
        return self


class NonCurrentHermeticExecutionScopeV1(CliCustodyDTO):
    disposition: Literal["STALE", "UNSUPPORTED", "DENIED"]


CurrentHermeticExecutionScopeResultV1 = Annotated[
    CurrentHermeticExecutionScopeV1 | NonCurrentHermeticExecutionScopeV1,
    Field(discriminator="disposition"),
]


class IssueHermeticOutputScopeV1(CliCustodyDTO):
    """Intent only: selected R17 provenance must be verified by the future issuer."""

    schema_id: Literal["chiplog.deployment-trust.issue-hermetic-output-scope.v1"] = (
        "chiplog.deployment-trust.issue-hermetic-output-scope.v1"
    )
    slot_id: Literal["h1-cli-effects-origin"]
    database_id: Identity
    scope_id: Identity
    expected_trust_observation: HermeticTrustObservationV1
    # Previous scope issuance's physical authenticated journal envelope, not
    # the logical trust snapshot head or the latest unrelated trust decision.
    expected_scope_predecessor: ExactHead | None
    expected_revision: UInt64 = Field(strict=True)
    authenticated_cli_ref: TrustReference
    admitted_authentication_ref: ExactHead
    selected_resource_observation_ref: SelectedHermeticResourceObservationRefV1
    worker_session_id: Identity

    @model_validator(mode="after")
    def reference_shape(self) -> Self:
        # Preconstructed stdlib dataclasses otherwise bypass nested validation.
        if (self.expected_scope_predecessor is None) != (self.expected_revision == 0):
            raise ValueError("genesis scope requires no predecessor and revision zero")
        ref = self.authenticated_cli_ref
        values = (
            getattr(ref.tenant_id, "value", None),
            getattr(ref.principal_id, "value", None),
            ref.contour,
            ref.credential_head,
            ref.session_head,
            ref.source_head,
            ref.trust_head,
            ref.materialization_head,
            ref.peer_credential,
        )
        if any(type(value) is not str or not value for value in values):
            raise ValueError("trust reference identities must be nonempty strings")
        if type(ref.freshness_sequence) is not int or not 0 <= ref.freshness_sequence < 2**64:
            raise ValueError("trust reference freshness must be uint64")
        return self


class IssuedHermeticOutputScopeV1(CliCustodyDTO):
    """Inert result shape; constructing it never proves issuance."""

    disposition: Literal["ISSUED", "REPLAY"]
    anchor: HermeticOutputScopeAnchorV1
    scope_head: ExactHead
    revision: UInt64

    @model_validator(mode="after")
    def anchor_revision(self) -> Self:
        if self.revision != self.anchor.scope_revision:
            raise ValueError("issued scope revision differs from anchor")
        return self


class NonIssuedHermeticOutputScopeV1(CliCustodyDTO):
    disposition: Literal["STALE", "DENIED", "UNSUPPORTED"]


IssueHermeticOutputScopeResultV1 = Annotated[
    IssuedHermeticOutputScopeV1 | NonIssuedHermeticOutputScopeV1,
    Field(discriminator="disposition"),
]


class HermeticOutputScopeOwner(Protocol):
    def issue_hermetic_output_scope(
        self, request: IssueHermeticOutputScopeV1
    ) -> IssueHermeticOutputScopeResultV1: ...

    def read_current_hermetic_output_scope(
        self, request: ReadCurrentHermeticExecutionScopeV1
    ) -> CurrentHermeticExecutionScopeResultV1: ...
