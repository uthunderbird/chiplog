"""Bounded H1 broker source attestation, never owner-independent proof.

Construction checks byte consistency only. The registered broker must capture
these bytes under AuthorityGate, authenticate the IPC route against ``route``,
and recheck selected/current R17/R16 and physical trust evidence at append/use.
The owner must compare the supplied snapshot digest/head and its live route.
No constructor, digest or detached candidate grants issuance/current authority.
Existing public H1 intent/result wires are unchanged.
"""

import hashlib
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead, ProviderRecipient

from .cli_custody_contracts import CliCustodyDTO, Digest, Identity, UInt64
from .hermetic_output_scope_contracts import (
    HermeticOutputPolicyV1,
    HermeticOutputScopeV1,
    HermeticTrustObservationV1,
    IssueHermeticOutputScopeV1,
)

# Bounds apply before JSON parsing at the IPC boundary, and again to decoded
# fields. The complete base64 wire is bounded independently of individual blobs.
H1_EVIDENCE_MAX_BYTES = 2_097_152
H1_CALL_MAX_BYTES = 3_145_728
RetainedBytes = Annotated[bytes, Field(min_length=1, max_length=524_288)]


class H1BrokerRouteBindingV1(CliCustodyDTO):
    tenant_id: Literal["hermetic-tenant"]
    database_id: Identity
    worker_session_id: Identity
    broker_epoch: UInt64
    runtime_generation: Identity
    broker_session_id: Identity
    owner_session_id: Identity
    request_id: Identity
    caller_owner: Literal["broker"] = "broker"
    callee_owner: Literal["deployment_trust"] = "deployment_trust"
    operation: Literal["deployment_trust.issue_hermetic_output_scope"] = (
        "deployment_trust.issue_hermetic_output_scope"
    )


class H1RetainedSelectedWrapperV1(CliCustodyDTO):
    """Exact retained bytes, not reconstructed policy or caller source objects.

    ``initialization_envelope_bytes`` is the physical selected H0 DECIDED envelope;
    it includes the retained initialization and its exact signed R16 fields.
    ``admitted_record_bytes`` is canonical(admitted.record), the retained R17 record;
    ``selected_admitted_record_ref`` is its independently selected physical locator.
    ``authentication_result_bytes`` is its exact retained owner response frame.
    The broker checks their embedding/selection and combined binding. Their opaque
    historical formats retain their own canonicalization; this wrapper uses sorted
    UTF-8 JSON/base64 inherited from CliCustodyDTO. No R16 key crosses this wire.
    The physical locator follows R17's identity/fingerprint head format; checking
    that format does not authenticate selection or the embedded command identity.
    """

    schema_id: Literal["chiplog.h1.retained-selected-wrapper.v1"] = (
        "chiplog.h1.retained-selected-wrapper.v1"
    )
    initialization_envelope_bytes: RetainedBytes
    admitted_record_bytes: RetainedBytes
    selected_admitted_record_ref: ExactHead
    authentication_result_bytes: Annotated[bytes, Field(min_length=1, max_length=65_536)]
    admitted_record_digest: Digest

    @model_validator(mode="after")
    def byte_consistency(self) -> Self:
        if hashlib.sha256(self.admitted_record_bytes).hexdigest() != self.admitted_record_digest:
            raise ValueError("retained admitted record digest mismatch")
        selected = self.selected_admitted_record_ref
        if selected.fingerprint != self.admitted_record_digest:
            raise ValueError("retained admitted record differs from selected physical ref")
        if selected.head != selected.identity + "/" + selected.fingerprint:
            raise ValueError("selected admitted record physical head differs from identity/digest")
        return self


class BrokerSelectedH1EvidenceV1(CliCustodyDTO):
    """Broker assertion of verified combined provenance, not a policy grant.

    The intent contains the exact H0 selection and R17/R16 refs plus CLI identity.
    An arbitrary caller can create a self-consistent instance. Acceptance therefore
    requires the authenticated broker call site; source authentication cannot be
    implemented by accepting this DTO or comparing attacker-recomputed hashes.
    """

    schema_id: Literal["chiplog.h1.broker-selected-evidence.v1"] = (
        "chiplog.h1.broker-selected-evidence.v1"
    )
    source_profile: Literal["chiplog.execution.h1-cli-hermetic-source-profile.v1"] = (
        "chiplog.execution.h1-cli-hermetic-source-profile.v1"
    )
    attestation_kind: Literal["BROKER_SELECTED_CURRENT_SOURCE_ATTESTATION"] = (
        "BROKER_SELECTED_CURRENT_SOURCE_ATTESTATION"
    )
    route: H1BrokerRouteBindingV1
    selected_request_bytes: Annotated[bytes, Field(min_length=1, max_length=65_536)]
    request_digest: Digest
    retained: H1RetainedSelectedWrapperV1
    retained_wrapper_digest: Digest
    recipient: ProviderRecipient
    trust_observation: HermeticTrustObservationV1
    trust_snapshot_digest: Digest

    @model_validator(mode="after")
    def byte_consistency(self) -> Self:
        request = IssueHermeticOutputScopeV1.model_validate_json(self.selected_request_bytes)
        if request.canonical_bytes() != self.selected_request_bytes:
            raise ValueError("H1 intent bytes are not canonical")
        if hashlib.sha256(self.selected_request_bytes).hexdigest() != self.request_digest:
            raise ValueError("H1 request digest mismatch")
        if (
            hashlib.sha256(self.retained.canonical_bytes()).hexdigest()
            != self.retained_wrapper_digest
        ):
            raise ValueError("selected wrapper digest mismatch")
        if (
            hashlib.sha256(self.retained.initialization_envelope_bytes).hexdigest()
            != request.selected_resource_observation_ref.selected_initialization.fingerprint
            or hashlib.sha256(self.retained.authentication_result_bytes).hexdigest()
            != request.admitted_authentication_ref.fingerprint
        ):
            raise ValueError("retained bytes differ from selected source refs")
        if (
            request.expected_trust_observation != self.trust_observation
            or request.database_id != self.route.database_id
            or request.worker_session_id != self.route.worker_session_id
            or request.authenticated_cli_ref.tenant_id.value != self.route.tenant_id
            or request.authenticated_cli_ref.principal_id.value != "hermetic-principal"
            or request.authenticated_cli_ref.contour != "CLI"
        ):
            raise ValueError("H1 selected request/context differs")
        if len(self.canonical_bytes()) > H1_EVIDENCE_MAX_BYTES:
            raise ValueError("H1 evidence exceeds wire bound")
        return self


class H1OwnerCandidateCallV1(CliCustodyDTO):
    """New request_bytes variant inside the existing H1 TrustOwnerCall mode."""

    schema_id: Literal["chiplog.h1.owner-candidate-call.v1"] = "chiplog.h1.owner-candidate-call.v1"
    evidence: BrokerSelectedH1EvidenceV1
    evidence_digest: Digest

    @model_validator(mode="after")
    def byte_consistency(self) -> Self:
        if hashlib.sha256(self.evidence.canonical_bytes()).hexdigest() != self.evidence_digest:
            raise ValueError("H1 evidence digest mismatch")
        if len(self.canonical_bytes()) > H1_CALL_MAX_BYTES:
            raise ValueError("H1 candidate call exceeds wire bound")
        return self


class H1OwnerCandidateV1(CliCustodyDTO):
    """Proposed scope only; broker must compare with its pinned original call.

    HermeticOutputScopeV1 closes policy, slot/profile and empty mandate inventory.
    Neither an anchor nor ISSUED/CURRENT is returned before fenced durable append.
    """

    schema_id: Literal["chiplog.h1.owner-candidate.v1"] = "chiplog.h1.owner-candidate.v1"
    disposition: Literal["CANDIDATE"] = "CANDIDATE"
    route: H1BrokerRouteBindingV1
    request_digest: Digest
    evidence_digest: Digest
    scope: HermeticOutputScopeV1

    def check_pinned_call(self, call: H1OwnerCandidateCallV1) -> None:
        """Consistency against broker-retained call; never authenticate a caller DTO."""
        evidence = call.evidence
        request = IssueHermeticOutputScopeV1.model_validate_json(evidence.selected_request_bytes)
        scope = self.scope
        policy = HermeticOutputPolicyV1.model_validate_json(
            scope.disclosure_policy.canonical_source_bytes
        )
        if (
            self.route != evidence.route
            or self.request_digest != evidence.request_digest
            or self.evidence_digest != call.evidence_digest
            or scope.database_id != request.database_id
            or scope.scope_id != request.scope_id
            or scope.revision != request.expected_revision
            or scope.predecessor != request.expected_scope_predecessor
            or scope.worker_session_id != request.worker_session_id
            or scope.admitted_authentication != request.admitted_authentication_ref
            or scope.selected_resource_observation_ref != request.selected_resource_observation_ref
            or scope.recipient != evidence.recipient
            or policy.endpoint_ref != evidence.recipient.endpoint
            or scope.trust_state.head != request.authenticated_cli_ref.trust_head
            or scope.credential_state.head != request.authenticated_cli_ref.credential_head
            or scope.session_state.head != request.authenticated_cli_ref.session_head
        ):
            raise ValueError("H1 candidate differs from pinned broker call")


def decode_h1_candidate_call(raw: bytes) -> H1OwnerCandidateCallV1:
    """Bound and check canonical wire before routing; this authenticates nothing."""
    if type(raw) is not bytes or len(raw) > H1_CALL_MAX_BYTES:
        raise ValueError("H1 candidate call exceeds wire bound")
    value = H1OwnerCandidateCallV1.model_validate_json(raw)
    if value.canonical_bytes() != raw:
        raise ValueError("H1 candidate call is not canonical")
    return value
