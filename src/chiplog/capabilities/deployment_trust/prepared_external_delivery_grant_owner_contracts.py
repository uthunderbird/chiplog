"""Private J7 ISSUE call and proposal contracts for V2 delivery grants.

These values are still untrusted at the process boundary.  The broker pins and
authenticates the observations before calling the owner, then repeats its CAS
checks when it durably appends a checked proposal.
"""

import hashlib
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from chiplog.capabilities.agent_loop.delivery_contracts import AcceptedDelivery, ExactHead
from chiplog.capabilities.effects.fences import NonSchedulerFence as EffectsNonSchedulerFence
from chiplog.capabilities.effects.h1_local_preparation_contracts import H1SelectedScopeSourceV1
from chiplog.capabilities.effects.scoped_intent_contracts import PreparedDeliveryBasisV3

from .cli_custody_contracts import CliCustodyDTO, Digest, Identity, UInt64
from .h1_broker_evidence_contracts import H1RetainedSelectedWrapperV1
from .hermetic_output_scope_contracts import (
    HermeticOutputScopeAnchorV1,
    HermeticOutputScopeV1,
    HermeticTrustObservationV1,
)
from .operator_grant_authorization_contracts import (
    RetainedOperatorGrantAuthorizationSourceV2,
    SignedOperatorGrantAuthorizationV2,
)
from .prepared_external_delivery_contracts import (
    PreparedExternalDeliveryGrantAnchorV2,
    PreparedExternalDeliveryGrantV2,
    SelectedExternalDeliverySourceV1,
    prepared_external_delivery_grant_content_head_v2,
)
from .prepared_external_delivery_policy_contracts import PreparedExternalSelfDeliveryPolicyAnchorV1
from .prepared_external_delivery_policy_owner_contracts import (
    prepared_self_delivery_policy_content_head,
)


def prepared_external_delivery_grant_request_content_head_v2(raw: bytes) -> str:
    return hashlib.sha256(
        b"chiplog.deployment-trust.prepared-external-delivery-grant-request-content-head.v2\x00"
        + raw
    ).hexdigest()


def prepared_delivery_basis_head_v3(basis: PreparedDeliveryBasisV3) -> ExactHead:
    """The exact loop basis in a separate content domain."""
    raw = basis.canonical_bytes()
    return ExactHead(
        identity=basis.acceptance.subject_id,
        head=hashlib.sha256(
            b"chiplog.deployment-trust.prepared-delivery-basis-head.v3\x00" + raw
        ).hexdigest(),
        fingerprint=hashlib.sha256(raw).hexdigest(),
    )


class PreparedExternalDeliveryGrantRouteV2(CliCustodyDTO):
    tenant_id: Identity
    database_id: Identity
    worker_session_id: Identity
    broker_epoch: UInt64
    runtime_generation: Identity
    broker_session_id: Identity
    owner_session_id: Identity
    request_id: Identity
    caller_owner: Literal["broker"] = "broker"
    callee_owner: Literal["deployment_trust"] = "deployment_trust"
    operation: Literal["deployment_trust.authorize_prepared_external_delivery_grant"] = (
        "deployment_trust.authorize_prepared_external_delivery_grant"
    )


class PreparedExternalDeliveryGrantEvidenceV2(CliCustodyDTO):
    """Broker-pinned loop evidence from which the whole requested scope is checked."""

    basis: PreparedDeliveryBasisV3
    selected_scope: H1SelectedScopeSourceV1
    retained_origin: H1RetainedSelectedWrapperV1
    selected_source: SelectedExternalDeliverySourceV1
    fence: EffectsNonSchedulerFence
    original_run: ExactHead
    captured_attempt: ExactHead
    accepted_delivery: AcceptedDelivery
    channel_id: Identity
    resource_grant: ExactHead
    canonical_resource_grant_bytes: bytes = Field(min_length=1)
    clock_contract: Literal["chiplog.dispatch.monotonic.v2"]
    clock_epoch: Identity
    now_ns: UInt64
    route: PreparedExternalDeliveryGrantRouteV2

    @model_validator(mode="after")
    def exact_scope_basis(self) -> Self:
        if (
            self.resource_grant.fingerprint
            != hashlib.sha256(self.canonical_resource_grant_bytes).hexdigest()
        ):
            raise ValueError("resource grant bytes differ from resource grant head")
        scope = self.selected_scope.scope
        if self.route.worker_session_id != scope.worker_session_id:
            raise ValueError("route worker differs from selected scope")
        if (
            self.fence.worker_session_id != scope.worker_session_id
            or self.fence.runtime_generation != self.route.runtime_generation
        ):
            raise ValueError("fence differs from selected route and worker")
        acceptance = self.basis.acceptance
        if (
            acceptance.subject_id != self.accepted_delivery.acceptance.identity
            or acceptance.head != self.accepted_delivery.acceptance.head
            or acceptance.fingerprint != self.accepted_delivery.acceptance.fingerprint
        ):
            raise ValueError("loop basis acceptance differs from accepted delivery")
        if self.accepted_delivery.selection.recipient != scope.recipient:
            raise ValueError("accepted delivery recipient differs from selected scope")
        if (
            self.selected_source.selected_initialization
            != scope.selected_resource_observation_ref.selected_initialization
            or self.selected_source.selected_admission_record
            != self.retained_origin.selected_admitted_record_ref
            or self.selected_source.admitted_authentication != scope.admitted_authentication
        ):
            raise ValueError("selected source differs from retained H1 preimages")
        if (
            hashlib.sha256(self.retained_origin.initialization_envelope_bytes).hexdigest()
            != self.selected_source.selected_initialization.fingerprint
        ):
            raise ValueError("retained initialization differs from selected source")
        if (
            self.fence.run_id != self.original_run.identity
            or self.fence.run_head != self.original_run.head
        ):
            raise ValueError("fence differs from original run")
        if (
            self.accepted_delivery.render_digest
            != hashlib.sha256(self.accepted_delivery.rendered_bytes).hexdigest()
        ):
            raise ValueError("accepted delivery rendered bytes differ from digest")
        return self


class ReconstructedPreparedExternalDeliveryGrantSelectorV1(CliCustodyDTO):
    """Immutable B evidence used only to select a current durable grant.

    This intentionally omits the issuance-local route, channel, and clock facts
    carried by ``PreparedExternalDeliveryGrantEvidenceV2``.  It is not a grant
    and cannot establish provenance without the authenticated durable lookup.
    """

    basis: PreparedDeliveryBasisV3
    scope_anchor: HermeticOutputScopeAnchorV1
    scope: HermeticOutputScopeV1
    selected_decision_bytes: bytes = Field(min_length=1)
    selected_record_bytes: bytes = Field(min_length=1)
    retained_origin: H1RetainedSelectedWrapperV1
    selected_source: SelectedExternalDeliverySourceV1
    fence: EffectsNonSchedulerFence
    original_run: ExactHead
    captured_attempt: ExactHead
    accepted_delivery: AcceptedDelivery
    resource_grant: ExactHead
    canonical_resource_grant_bytes: bytes = Field(min_length=1)

    @model_validator(mode="after")
    def exact_scope_basis(self) -> Self:
        if (
            self.resource_grant.fingerprint
            != hashlib.sha256(self.canonical_resource_grant_bytes).hexdigest()
        ):
            raise ValueError("resource grant bytes differ from resource grant head")
        if (
            hashlib.sha256(self.selected_decision_bytes).hexdigest()
            != self.scope_anchor.decision.fingerprint
            or hashlib.sha256(self.selected_record_bytes).hexdigest()
            != self.scope_anchor.record.fingerprint
        ):
            raise ValueError("retained scope bytes differ from physical anchor")
        if (
            self.scope_anchor.scope_revision != self.scope.revision
            or self.scope_anchor.predecessor != self.scope.predecessor
            or self.scope_anchor.selected_resource_observation_ref
            != self.scope.selected_resource_observation_ref
        ):
            raise ValueError("scope differs from physical anchor")
        return self


class AuthorizePreparedExternalDeliveryGrantCallV2(CliCustodyDTO):
    schema_id: Literal[
        "chiplog.deployment-trust.authorize-prepared-external-delivery-grant-call.v2"
    ] = "chiplog.deployment-trust.authorize-prepared-external-delivery-grant-call.v2"
    canonical_signed_source_bytes: bytes = Field(min_length=1)
    snapshot_bytes: bytes = Field(min_length=1)
    expected_trust_observation: HermeticTrustObservationV1
    latest_grant_anchor: PreparedExternalDeliveryGrantAnchorV2 | None
    latest_grant_bytes: bytes | None
    current_policy_anchor: PreparedExternalSelfDeliveryPolicyAnchorV1
    current_policy_bytes: bytes = Field(min_length=1)
    evidence: PreparedExternalDeliveryGrantEvidenceV2

    @model_validator(mode="after")
    def canonical_inputs(self) -> Self:
        source = SignedOperatorGrantAuthorizationV2.model_validate_json(
            self.canonical_signed_source_bytes
        )
        if source.canonical_bytes() != self.canonical_signed_source_bytes:
            raise ValueError("signed operator source bytes must be canonical")
        if (self.latest_grant_anchor is None) != (self.latest_grant_bytes is None):
            raise ValueError("latest grant anchor and bytes must both be present or absent")
        if self.latest_grant_anchor is not None and self.latest_grant_bytes is not None:
            grant = PreparedExternalDeliveryGrantV2.model_validate_json(self.latest_grant_bytes)
            raw = grant.canonical_bytes()
            if (
                raw != self.latest_grant_bytes
                or self.latest_grant_anchor.grant
                != ExactHead(
                    identity=grant.grant_id,
                    head=prepared_external_delivery_grant_content_head_v2(raw),
                    fingerprint=hashlib.sha256(raw).hexdigest(),
                )
                or self.latest_grant_anchor.revision != grant.revision
            ):
                raise ValueError("latest grant anchor differs from grant bytes")
        from .prepared_external_delivery_policy_contracts import (
            PreparedExternalSelfDeliveryPolicyV1,
        )

        policy = PreparedExternalSelfDeliveryPolicyV1.model_validate_json(self.current_policy_bytes)
        if (
            policy.canonical_bytes() != self.current_policy_bytes
            or self.current_policy_anchor.policy
            != ExactHead(
                identity=policy.policy_id,
                head=prepared_self_delivery_policy_content_head(self.current_policy_bytes),
                fingerprint=hashlib.sha256(self.current_policy_bytes).hexdigest(),
            )
            or self.current_policy_anchor.revision != policy.revision
        ):
            raise ValueError("current policy anchor differs from policy bytes")
        return self


class PreparedExternalDeliveryGrantProposalV2(CliCustodyDTO):
    """Uncommitted consistency proposal, not proof that its inputs were authenticated.

    The broker authenticates provenance and route, verifies the signature, and
    repeats current-policy/latest-grant CAS when it appends this proposal.
    """

    schema_id: Literal["chiplog.deployment-trust.prepared-external-delivery-grant-result.v2"] = (
        "chiplog.deployment-trust.prepared-external-delivery-grant-result.v2"
    )
    disposition: Literal["PROPOSED"] = "PROPOSED"
    call_sha256: Digest
    operator_source: RetainedOperatorGrantAuthorizationSourceV2
    evidence: PreparedExternalDeliveryGrantEvidenceV2
    grant: PreparedExternalDeliveryGrantV2

    def check_pinned_call(self, call: AuthorizePreparedExternalDeliveryGrantCallV2) -> None:
        from ._j7_grant_process import derive_grant, request_from_source, retained_source

        call = AuthorizePreparedExternalDeliveryGrantCallV2.model_validate_json(
            call.canonical_bytes()
        )
        proposal = type(self).model_validate_json(self.canonical_bytes())
        if proposal.call_sha256 != hashlib.sha256(call.canonical_bytes()).hexdigest():
            raise ValueError("proposal call SHA256 mismatch")
        if proposal.operator_source.canonical_source_bytes != call.canonical_signed_source_bytes:
            raise ValueError("proposal source differs from pinned call")
        if proposal.operator_source != retained_source(call.canonical_signed_source_bytes):
            raise ValueError("proposal source reference differs from pinned call")
        request = request_from_source(call.canonical_signed_source_bytes)
        if request.expected_trust_observation != call.expected_trust_observation:
            raise ValueError("proposal trust CAS differs from signed request")
        if request.expected_grant != call.latest_grant_anchor:
            raise ValueError("proposal grant CAS differs from signed request")
        if proposal.evidence != call.evidence:
            raise ValueError("proposal evidence differs from pinned call")
        if proposal.grant != derive_grant(request, proposal.operator_source.ref, call):
            raise ValueError("proposal grant differs from signed request and pinned evidence")


class PreparedExternalDeliveryGrantRejectedV2(CliCustodyDTO):
    schema_id: Literal["chiplog.deployment-trust.prepared-external-delivery-grant-result.v2"] = (
        "chiplog.deployment-trust.prepared-external-delivery-grant-result.v2"
    )
    disposition: Literal["DENIED", "STALE"]
    call_sha256: Digest
    reason: Identity


PreparedExternalDeliveryGrantResultV2 = Annotated[
    PreparedExternalDeliveryGrantProposalV2 | PreparedExternalDeliveryGrantRejectedV2,
    Field(discriminator="disposition"),
]
