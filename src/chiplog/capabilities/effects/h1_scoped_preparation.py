"""Pure H1 scoped external-delivery intent construction.

The broker authenticates candidate evidence and checks latest/currentness before
calling this evaluator.  This module checks only canonical candidate bytes and
their exact joins, then creates an inert intent preparation; it has no storage,
provider, or SEND dependency.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from chiplog.capabilities.agent_loop.delivery_contracts import AcceptedDelivery
from chiplog.capabilities.deployment_trust.prepared_external_delivery_contracts import (
    PreparedExternalDeliveryGrantV2,
)
from chiplog.capabilities.deployment_trust.prepared_external_delivery_grant_owner_contracts import (
    prepared_delivery_basis_head_v3,
)
from chiplog.capabilities.deployment_trust.prepared_external_delivery_policy_contracts import (
    PreparedExternalSelfDeliveryPolicyV1,
)

from .contracts import DeliverySendBinding, ExactHead, OriginSelection, ProviderRecipient
from .dispatch_authority_contracts import CapturedSource, Identity
from .dispatch_v2_contracts import MandateHorizon
from .fences import NonSchedulerFence
from .h1_normative_conflict_generation import require_h1_history_and_normative_conflict_generation
from .h1_prepared_delivery_basis import derive_h1_prepared_delivery_basis
from .h1_producer_semantics import H1_PRODUCER_SEMANTICS, require_h1_producer_semantics
from .h1_producer_source_contracts import (
    H1PreparedDeliveryMandateCandidateV1,
    require_h1_prepared_delivery_candidate,
    require_h1_prepared_delivery_sources,
)
from .h1_scoped_preparation_contracts import (
    H1ScopedDeliveryAuthorityEvidenceV1,
    H1ScopedDeliveryOwnerCallV1,
    H1ScopedDeliveryRejectedV1,
    PreparedH1ScopedDeliveryV1,
)
from .scoped_intent_contracts import (
    DispatchMandateV3,
    ExternalActionIntentV3,
    PreparedDeliveryAuthority,
    PreparedDeliveryBasisV3,
    PreparedDeliveryOriginV3,
    PreparedScopedIntentPublication,
    PrepareScopedIntentPublication,
    ScopedAuthorityRecord,
    ScopedDispatchAcquisition,
    ScopedPrecursorRequest,
    ScopedPrecursorResult,
)
from .scoped_intent_record_contracts import (
    make_scoped_intent_member,
    make_scoped_intent_retained_exchange,
    scoped_intent_complete_owner_commitment,
    scoped_intent_fingerprint,
    validate_scoped_intent_publication,
)


@dataclass(frozen=True)
class H1ScopedDeliveryDerivationV1:
    """Canonical H1 output from closed candidate inputs.

    This is a pure projection.  Its inputs can be authenticated by a mounted
    broker, but this function neither authenticates them nor establishes their
    currentness.
    """

    candidate: H1PreparedDeliveryMandateCandidateV1
    mandate: DispatchMandateV3
    policy_record: ScopedAuthorityRecord
    precursor_request: ScopedPrecursorRequest


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _effect_head(value: Any) -> ExactHead:
    """Translate a trust/loop head to the distinct Effects head shape."""
    identity = getattr(value, "identity", getattr(value, "subject_id", None))
    if identity is None:
        raise ValueError("head has no identity")
    return ExactHead(subject_id=identity, head=value.head, fingerprint=value.fingerprint)


def _loop_head(value: Any) -> ExactHead:
    revision = getattr(value, "revision", None)
    if revision is None or not hasattr(revision, "head"):
        raise ValueError("loop subject has no exact revision")
    return ExactHead(
        subject_id=value.subject_id,
        head=revision.head,
        fingerprint=revision.fingerprint,
    )


def _run_head(value: Any) -> ExactHead:
    return ExactHead(subject_id=value.run_id, head=value.head, fingerprint=value.digest())


def _recipient(value: Any) -> ProviderRecipient:
    return ProviderRecipient(
        provider=value.provider_id,
        account=value.account_id,
        recipient=value.recipient_id,
        endpoint=_effect_head(value.endpoint),
        canonical_address=value.canonical_address,
        credential_binding=_effect_head(value.credential_binding),
    )


def _delivery_binding(delivery: AcceptedDelivery) -> DeliverySendBinding:
    if delivery.selection.kind != "ORIGIN_EXACT":
        raise ValueError("H1 external delivery requires ORIGIN_EXACT selection")
    return DeliverySendBinding(
        delivery_id=delivery.delivery_id,
        acceptance=_effect_head(delivery.acceptance),
        render_digest=delivery.render_digest,
        manifest_digest=delivery.manifest_digest,
        selection=OriginSelection(
            kind="ORIGIN_EXACT",
            ingress_binding=_effect_head(delivery.selection.ingress_binding),
            recipient=_recipient(delivery.selection.recipient),
        ),
        visibility=tuple(_effect_head(item) for item in delivery.visibility),
        provenance=tuple(_effect_head(item) for item in delivery.provenance),
        disclosure=tuple(_effect_head(item) for item in delivery.disclosure),
        narrowing=tuple(_effect_head(item) for item in delivery.narrowing),
        policy=_effect_head(delivery.policy),
    )


def _policy_record(
    policy: PreparedExternalSelfDeliveryPolicyV1, raw: bytes, evidence: Any
) -> ScopedAuthorityRecord:
    anchor = evidence.policy_anchor
    return ScopedAuthorityRecord(
        owner="deployment_trust",
        head=_effect_head(anchor.policy),
        schema_id=policy.schema_id,
        canonical_record_bytes=raw,
        selected_decision=_effect_head(anchor.decision),
    )


def _derived_precursor_id(
    grant: PreparedExternalDeliveryGrantV2, delivery: AcceptedDelivery
) -> str:
    raw = json.dumps(
        ["chiplog.effects.h1-scoped-precursor.v1", grant.grant_id, delivery.delivery_id],
        separators=(",", ":"),
    ).encode()
    return "h1-scoped-precursor:" + _sha(raw)


def _derived_mandate_id(grant: PreparedExternalDeliveryGrantV2, delivery: AcceptedDelivery) -> str:
    return "h1-scoped-mandate:" + _sha((grant.grant_id + "\x00" + delivery.delivery_id).encode())


def _reject(reason: str) -> H1ScopedDeliveryRejectedV1:
    return H1ScopedDeliveryRejectedV1(disposition="DENIED", reason=reason)


def _same_head(left: Any, right: ExactHead) -> bool:
    return bool(
        left.identity == right.subject_id
        and left.head == right.head
        and left.fingerprint == right.fingerprint
    )


def derive_h1_scoped_delivery(
    *,
    basis: PreparedDeliveryBasisV3,
    original_run: ExactHead,
    captured_attempt: ExactHead,
    delivery: AcceptedDelivery,
    evidence: H1ScopedDeliveryAuthorityEvidenceV1,
    grant: PreparedExternalDeliveryGrantV2,
    policy: PreparedExternalSelfDeliveryPolicyV1,
    target_intent_id: Identity,
    original_history: CapturedSource,
    current_history: CapturedSource,
    original_generation: CapturedSource,
    current_generation: CapturedSource,
    history_observation: ExactHead,
    clock_contract: Identity,
    clock_epoch: Identity,
    valid_until_ns: int,
) -> H1ScopedDeliveryDerivationV1:
    """Derive the exact H1 candidate projection from closed owner DTOs.

    Checks that grant and policy bytes equal the closed evidence and
    reconstructs exact history/generation captures.  The caller must already
    validate grant, delivery, policy, and basis semantic joins.  This function
    neither authenticates inputs nor establishes their currentness.
    """
    if (
        grant.canonical_bytes() != evidence.canonical_grant_bytes
        or policy.canonical_bytes() != evidence.canonical_policy_bytes
    ):
        raise ValueError("H1 derivation grant or policy differs from closed authority evidence")
    normative_conflict_generation = require_h1_history_and_normative_conflict_generation(
        tenant_id=grant.tenant_id,
        target_intent_id=target_intent_id,
        original_history=original_history,
        current_history=current_history,
        original_generation=original_generation,
        current_generation=current_generation,
        history_observation=history_observation,
        clock_contract=clock_contract,
        clock_epoch=clock_epoch,
        valid_until_ns=valid_until_ns,
    )
    grant_scope = grant.scope
    terms = policy.terms
    policy_record = _policy_record(policy, evidence.canonical_policy_bytes, evidence)
    policy_head = _effect_head(evidence.policy_anchor.policy)
    grant_head = _effect_head(evidence.grant_anchor.grant)
    source_head = _effect_head(grant.authorization_source)
    command_head = _effect_head(grant.authorization_command)
    derived = DispatchMandateV3(
        mandate_id=_derived_mandate_id(grant, delivery),
        tenant_id=grant.tenant_id,
        principal_id=grant_scope.principal_id,
        actor_id=grant_scope.principal_id,
        operation_profile=policy_head,
        origin=PreparedDeliveryOriginV3(
            original_run=original_run,
            captured_attempt=captured_attempt,
            binding=_delivery_binding(delivery),
            preparation_basis=_effect_head(prepared_delivery_basis_head_v3(basis)),
        ),
        planning_revision=policy_head,
        preexisting_authority_basis=grant_head,
        authority_sources=(policy_head, command_head, source_head),
        affected_party_constraints=(),
        normative_conflict_generation=normative_conflict_generation,
        dependencies=(),
        factual_assertion_evidence=(source_head,),
        verification_contradiction=(),
        authority_applicability=(policy_head,),
        consequence_scope=grant_head,
        communication_mandate=policy_head,
        disclosure_projection=policy_head,
        channel_class=terms.channel_id,
        interaction_context=grant_head,
        recipient=_recipient(delivery.selection.recipient),
        payload=delivery.rendered_bytes,
        effect_fingerprint=_sha(delivery.rendered_bytes),
        bundle_members=(grant_head,),
        idempotency_fence_key=grant.grant_id + ":" + delivery.delivery_id,
        horizon=MandateHorizon(
            clock_contract=terms.clock_contract,
            clock_epoch=terms.clock_epoch,
            not_before_ns=terms.not_before_ns,
            expires_at_ns=terms.expires_at_ns,
            continuity_policy=policy_head,
        ),
        semantics=H1_PRODUCER_SEMANTICS,
    )
    mandate_bytes = derived.canonical_bytes()
    candidate = H1PreparedDeliveryMandateCandidateV1(
        prepared_delivery_basis_bytes=basis.canonical_bytes(),
        grant_anchor=evidence.grant_anchor,
        policy_anchor=evidence.policy_anchor,
        canonical_mandate_bytes=mandate_bytes,
        mandate_fingerprint=_sha(mandate_bytes),
    )
    precursor = ScopedPrecursorRequest(
        request_id=_derived_precursor_id(grant, delivery),
        mandate=derived,
        interpretation_policy=policy_record,
        preexisting_sources=(policy_record,),
    )
    return H1ScopedDeliveryDerivationV1(
        candidate=candidate,
        mandate=derived,
        policy_record=policy_record,
        precursor_request=precursor,
    )


def prepare_h1_scoped_delivery(
    call: H1ScopedDeliveryOwnerCallV1,
) -> PreparedH1ScopedDeliveryV1 | H1ScopedDeliveryRejectedV1:
    """Return one deterministic external intent candidate or a typed refusal.

    ``ACTIVE`` below describes the selected decoded revisions only.  It does
    not attest that no later revision exists; only the mounted broker reader can
    make that currentness decision.
    """
    try:
        call = H1ScopedDeliveryOwnerCallV1.model_validate_json(call.canonical_bytes())
        request = call.request
        evidence = request.authority_evidence
        grant = PreparedExternalDeliveryGrantV2.model_validate_json(evidence.canonical_grant_bytes)
        policy = PreparedExternalSelfDeliveryPolicyV1.model_validate_json(
            evidence.canonical_policy_bytes
        )
        first = request.original_completion_request
        basis = derive_h1_prepared_delivery_basis(first, request.prepared_completion)
        delivery = request.prepared_completion.delivery.manifest.ordered_deliveries[0]
        if delivery.selection.kind != "ORIGIN_EXACT":
            return _reject("H1 external delivery requires ORIGIN_EXACT selection")
        grant_scope = grant.scope
        terms = policy.terms
        mandate = grant_scope.mandate
        policy_record = _policy_record(policy, evidence.canonical_policy_bytes, evidence)
        policy_head = _effect_head(evidence.policy_anchor.policy)
        grant_head = _effect_head(evidence.grant_anchor.grant)
        original_run = _run_head(first.run)
        attempt = _loop_head(first.selected_attempt)
        loop_basis_head = prepared_delivery_basis_head_v3(basis)

        if request.current.supported_semantics != H1_PRODUCER_SEMANTICS:
            return _reject("H1 scoped delivery needs registered producer semantics")
        if not isinstance(request.fence, NonSchedulerFence):
            return _reject("H1 scoped delivery requires a non-scheduler fence")
        if (
            grant.status != "ACTIVE"
            or policy.status != "ACTIVE"
            or grant.selected_policy_anchor != evidence.policy_anchor
            or (grant.tenant_id, grant.database_id) != (first.run.tenant, first.source.database_id)
            or (policy.tenant_id, policy.database_id)
            != (first.run.tenant, first.source.database_id)
            or (grant_scope.principal_id, grant_scope.worker_session_id)
            != (first.run.principal, first.run.worker_session)
            or (request.fence.run_id, request.fence.run_head, request.fence.worker_session_id)
            != (original_run.subject_id, original_run.head, first.run.worker_session)
            or request.fence.runtime_generation != call.route.runtime_generation
            or (call.route.tenant_id, call.route.database_id, call.route.worker_session_id)
            != (first.run.tenant, first.source.database_id, first.run.worker_session)
            or request.identity.command_id != call.route.request_id
            or request.current.command_fingerprint != request.identity.fingerprint
            or request.selected_scope.current_request.expected_trust_observation
            != evidence.trust_observation
        ):
            return _reject("H1 scoped delivery route, scope, or fence differs from basis")
        selected_resource = request.selected_scope.scope.selected_resource_observation_ref
        if (
            grant_scope.selected_source.selected_initialization
            != selected_resource.selected_initialization
            or grant_scope.resources.signed_observation_fingerprint
            != selected_resource.signed_observation_fingerprint
            or grant_scope.resources.clock_epoch != terms.clock_epoch
            or request.current.clock_contract != terms.clock_contract
            or request.current.clock_epoch != terms.clock_epoch
            or not (terms.not_before_ns <= request.current.observed_time_ns < terms.expires_at_ns)
            or grant_scope.selected_source.source_class not in terms.source_classes
            or delivery.selection.kind not in terms.selection_modes
            or grant_scope.selected_source.ingress_binding != delivery.selection.ingress_binding
            or grant_scope.selected_source.selected_admission_record
            != request.retained_origin.selected_admitted_record_ref
            or grant_scope.selected_source.admitted_authentication
            != request.selected_scope.scope.admitted_authentication
            or _sha(request.retained_origin.initialization_envelope_bytes)
            != grant_scope.selected_source.selected_initialization.fingerprint
        ):
            return _reject("H1 scoped delivery resource, clock, source, or horizon differs")
        if (
            terms.communication_permission != "PREPARED_EXTERNAL_SEND"
            or terms.disclosure_permission != "EXACT_RENDERED_PAYLOAD"
            or terms.self_recipient_semantics != "OPERATOR_ATTESTED_SELF"
            or terms.principal_id != grant_scope.principal_id
            or terms.recipient != grant_scope.resources.recipient
            or terms.recipient != delivery.selection.recipient
            or terms.payload_digest != _sha(delivery.rendered_bytes)
            or terms.payload_byte_length != len(delivery.rendered_bytes)
        ):
            return _reject("H1 scoped delivery policy permissions differ")
        if (
            not _same_head(mandate.original_run, original_run)
            or not _same_head(mandate.captured_attempt, attempt)
            or mandate.delivery_id != delivery.delivery_id
            or mandate.payload_digest != _sha(delivery.rendered_bytes)
            or mandate.payload_byte_length != len(delivery.rendered_bytes)
        ):
            return _reject("H1 scoped delivery grant mandate differs from accepted delivery")
        if (
            mandate.preparation_basis != loop_basis_head
            or mandate.selection != delivery.selection.kind
            or mandate.clock_contract != terms.clock_contract
            or mandate.clock_epoch != terms.clock_epoch
            or (mandate.not_before_ns, mandate.expires_at_ns)
            != (terms.not_before_ns, terms.expires_at_ns)
        ):
            return _reject("H1 scoped delivery grant mandate basis or horizon differs")
        if (
            request.preexisting_communication_authority != policy_record
            or request.current_disclosure_authority != policy_record
            or request.complete_current_origin_sources != (policy_record,)
        ):
            return _reject("H1 scoped delivery authority records differ from policy projection")
        require_h1_producer_semantics(
            supported_semantics=request.current.supported_semantics,
            original=request.original_sources.semantic_registry,
            current=request.current.sources.semantic_registry,
            clock_contract=terms.clock_contract,
            clock_epoch=terms.clock_epoch,
            valid_until_ns=request.current.lease_expires_at_ns,
        )
        derivation = derive_h1_scoped_delivery(
            basis=basis,
            original_run=original_run,
            captured_attempt=attempt,
            delivery=delivery,
            evidence=evidence,
            grant=grant,
            policy=policy,
            target_intent_id=request.intent_id,
            original_history=request.original_sources.effects_history,
            current_history=request.current.sources.effects_history,
            original_generation=request.original_sources.normative_conflict_generation,
            current_generation=request.current.sources.normative_conflict_generation,
            history_observation=request.current.history_observation,
            clock_contract=terms.clock_contract,
            clock_epoch=terms.clock_epoch,
            valid_until_ns=request.current.lease_expires_at_ns,
        )
        candidate = derivation.candidate
        derived = derivation.mandate
        policy_record = derivation.policy_record
        precursor = derivation.precursor_request
        basis_bytes = candidate.prepared_delivery_basis_bytes
        mandate_bytes = candidate.canonical_mandate_bytes
        require_h1_prepared_delivery_sources(
            request.original_sources,
            basis_bytes=basis_bytes,
            grant_anchor=candidate.grant_anchor,
            policy_anchor=candidate.policy_anchor,
            mandate_bytes=mandate_bytes,
        )
        require_h1_prepared_delivery_candidate(
            request.current.immutable_mandate_candidate,
            basis_bytes=basis_bytes,
            grant_anchor=candidate.grant_anchor,
            policy_anchor=candidate.policy_anchor,
            mandate_bytes=mandate_bytes,
        )
        require_h1_prepared_delivery_sources(
            request.current.sources,
            basis_bytes=basis_bytes,
            grant_anchor=candidate.grant_anchor,
            policy_anchor=candidate.policy_anchor,
            mandate_bytes=mandate_bytes,
        )
        if request.precursor_request != precursor:
            return _reject("H1 scoped delivery precursor request differs from authority projection")
        precursor_result = ScopedPrecursorResult(
            source_request_fingerprint=_sha(precursor.canonical_bytes()),
            mandate_fingerprint=_sha(derived.canonical_bytes()),
            interpretation_policy=policy_record.head,
            complete_evaluation_evidence=(
                grant_head,
                policy_head,
                _effect_head(evidence.policy_anchor.decision),
            ),
        )
        acquisition = ScopedDispatchAcquisition(
            authority=PreparedDeliveryAuthority(
                basis=basis,
                preexisting_communication_authority=policy_record,
                current_disclosure_authority=policy_record,
                exact_mandate_bytes=derived.canonical_bytes(),
            ),
            precursor_request=precursor,
            precursor_result=precursor_result,
            original_sources=request.original_sources,
        )
        unsigned = ExternalActionIntentV3(
            intent_id=request.intent_id,
            fingerprint="0" * 64,
            mandate=derived,
            acquisition=acquisition,
        )
        intent = unsigned.model_copy(update={"fingerprint": scoped_intent_fingerprint(unsigned)})
        intent_request = PrepareScopedIntentPublication(
            identity=request.identity,
            intent=intent,
            expected_intent=request.expected_intent,
            current=request.current,
            complete_current_origin_sources=request.complete_current_origin_sources,
            fence=request.fence,
        )
        retained = make_scoped_intent_retained_exchange(intent_request)
        if request.retained_sources != retained.original_sources:
            return _reject(
                "H1 scoped delivery retained sources differ from exact intent projection"
            )
        member = make_scoped_intent_member(intent)
        intent_result = PreparedScopedIntentPublication(
            source_request_fingerprint=_sha(intent_request.canonical_bytes()),
            intent=intent,
            original_references=retained.original_references,
            complete_owner_commitment=scoped_intent_complete_owner_commitment(
                member, retained.original_sources
            ),
        )
        validate_scoped_intent_publication(intent_request, intent_result, retained)
        return PreparedH1ScopedDeliveryV1(
            source_request_fingerprint=_sha(request.canonical_bytes()),
            authority_evidence=evidence,
            delivery_id=delivery.delivery_id,
            basis=basis,
            precursor_request=precursor,
            precursor_result=precursor_result,
            intent_request=intent_request,
            intent_result=intent_result,
            acquisition_bytes=acquisition.canonical_bytes(),
            mandate_bytes=derived.canonical_bytes(),
            retained_sources=retained.original_sources,
        )
    except (TypeError, ValueError, IndexError, AttributeError) as error:
        return _reject(str(error))


__all__ = [
    "H1ScopedDeliveryDerivationV1",
    "derive_h1_scoped_delivery",
    "prepare_h1_scoped_delivery",
]
