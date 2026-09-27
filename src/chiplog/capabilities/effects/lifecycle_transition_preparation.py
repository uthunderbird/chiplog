"""Pure all-child retransmission preparation.

This leaf checks only that retained DTO fields agree with one another.  It has
no selected-source reader, registry, writer, clock, provider, or SEND API.
Those boundaries must authenticate proof bytes and recheck the live cut.
"""

from __future__ import annotations

import hashlib

from .contracts import ExactHead, TransmissionAttempt
from .dispatch_outcome_contracts import DispatchObligationV2
from .dispatch_v2 import DispatchAuthorizationV2, reference, require_intent
from .dispatch_v2_contracts import DispatchBoundaryFailureV2
from .fences import Absent
from .lifecycle_transition_contracts import (
    AllPriorChildrenIncapable,
    EffectsLifecycleRequest,
    EffectsLifecycleResult,
    EffectsLineageCut,
    EffectsObligationRevision,
    PreparedSafeRetransmission,
    PrepareSafeRetransmission,
    RetransmissionDecisionRecord,
    RetriedParentRevision,
)
from .scoped_intent_contracts import ExternalActionIntentV3, ScopedDispatchAuthorizationRecord

_BATCH_DOMAIN = b"chiplog.effects.safe-retransmission-batch.v1\x00"
_TRANSMISSION_DOMAIN = b"chiplog.effects.safe-retransmission-transmission.v1\x00"


class _Rejected(ValueError):
    pass


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _reject(error: Exception) -> DispatchBoundaryFailureV2:
    return DispatchBoundaryFailureV2(
        schema_id="chiplog.effects.dispatch-boundary-failure.v2",
        kind="DENIED",
        source_role=None,
        reason=str(error) or "inconsistent safe retransmission input",
    )


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise _Rejected(reason)


def _intent_and_authorization(request: PrepareSafeRetransmission) -> tuple[ExactHead, ExactHead]:
    intent = request.observed.original_intent
    mandate = intent.mandate
    intent_head = reference(intent.intent_id, intent.canonical_bytes())
    mandate_head = reference(mandate.mandate_id, mandate.canonical_bytes())
    authorization = request.observed.original_authorization
    if isinstance(intent, ExternalActionIntentV3):
        if not isinstance(authorization, ScopedDispatchAuthorizationRecord):
            raise _Rejected("v3 authorization differs")
        _require(authorization.original_intent == intent_head, "authorization intent differs")
        _require(authorization.immutable_mandate == mandate_head, "authorization mandate differs")
        _require(authorization.semantics == mandate.semantics, "authorization semantics differs")
        return intent_head, reference(
            authorization.authorization_id, authorization.canonical_bytes()
        )
    _require(isinstance(authorization, DispatchAuthorizationV2), "v2 authorization differs")
    if not isinstance(authorization, DispatchAuthorizationV2):
        raise _Rejected("v2 authorization differs")
    require_intent(intent)
    _require(authorization.intent == intent_head, "authorization intent differs")
    _require(authorization.mandate == mandate_head, "authorization mandate differs")
    _require(authorization.semantics == mandate.semantics, "authorization semantics differs")
    return intent_head, authorization.authorization


def _require_children(cut: EffectsLineageCut, intent_head: ExactHead) -> None:
    mandate = cut.original_intent.mandate
    children = cut.complete_ordered_children
    _require(
        tuple(child.ordinal for child in children) == tuple(range(len(children))),
        "child ordinals differ",
    )
    _require(
        len({child.transmission for child in children}) == len(children),
        "duplicate child transmission",
    )
    _require(len(cut.complete_child_sources) == len(children), "child source count differs")
    for child, source in zip(children, cut.complete_child_sources, strict=True):
        _require(source.subject == child.transmission, "child source differs")
        _require(
            child.intent == intent_head
            and child.semantics == mandate.semantics
            and child.payload_fingerprint == mandate.effect_fingerprint
            and child.recipient == mandate.recipient
            and child.idempotency_fence_key == mandate.idempotency_fence_key,
            "child differs from original mandate",
        )


def _retained_obligation(cut: EffectsLineageCut, intent_head: ExactHead) -> ExactHead | Absent:
    obligation = cut.original_obligation
    if obligation is None or isinstance(obligation, Absent):
        return Absent()
    children = tuple(child.transmission for child in cut.complete_ordered_children)
    if isinstance(obligation, DispatchObligationV2):
        _require(
            obligation.original_intent == intent_head and obligation.original_children == children,
            "original obligation differs",
        )
        return obligation.obligation
    _require(isinstance(obligation, EffectsObligationRevision), "unknown original obligation")
    _require(
        obligation.original_intent == intent_head
        and obligation.complete_ordered_children == children,
        "original obligation differs",
    )
    return obligation.obligation


def _require_coverage(request: PrepareSafeRetransmission, intent_head: ExactHead) -> None:
    cut, subject = request.observed, request.coverage.subject
    mandate = cut.original_intent.mandate
    children = cut.complete_ordered_children
    _require(subject.original_intent == intent_head, "coverage intent differs")
    _require(
        subject.exact_effect_fingerprint == mandate.effect_fingerprint
        and subject.exact_payload_fingerprint == _sha(mandate.payload)
        and subject.recipient == mandate.recipient
        and subject.idempotency_fence_key == mandate.idempotency_fence_key,
        "coverage mandate differs",
    )
    _require(
        subject.complete_prior_children == tuple(child.transmission for child in children),
        "coverage children differ",
    )
    _require(subject.next_ordinal == len(children), "coverage next ordinal differs")
    _require(
        subject.next_transmission_id not in {child.transmission.subject_id for child in children},
        "coverage transmission id is not fresh",
    )
    _require(
        subject.clock_contract == request.current.clock_contract
        and subject.clock_epoch == request.current.clock_epoch
        and subject.coverage_starts_ns
        <= request.current.observed_time_ns
        < subject.coverage_expires_ns,
        "coverage interval or clock differs",
    )
    if isinstance(request.coverage, AllPriorChildrenIncapable):
        proofs = request.coverage.complete_ordered_proofs
        _require(len(proofs) == len(children), "incapacity proof count differs")
        _require(
            tuple(proof.child for proof in proofs)
            == tuple(child.transmission for child in children),
            "incapacity proof children differ",
        )


def prepare_safe_retransmission(
    request: PrepareSafeRetransmission,
) -> PreparedSafeRetransmission | DispatchBoundaryFailureV2:
    """Derive decision → child → parent records, or reject before publication."""
    try:
        request = PrepareSafeRetransmission.model_validate_json(request.canonical_bytes())
        cut = request.observed
        _require(cut.state == request.expected_parent_state, "parent state differs")
        intent_head, authorization_head = _intent_and_authorization(request)
        _require_children(cut, intent_head)
        retained_obligation = _retained_obligation(cut, intent_head)
        _require_coverage(request, intent_head)
        request_fingerprint = _sha(request.canonical_bytes())
        decision = RetransmissionDecisionRecord(
            identity=request.identity,
            source_request_fingerprint=request_fingerprint,
            original_intent=intent_head,
            original_authorization=authorization_head,
            prior_parent=cut.current_parent,
            coverage=request.coverage,
            current_inputs_fingerprint=_sha(request.current.canonical_bytes()),
            complete_current_origin_sources=request.complete_current_origin_sources,
            fence=request.fence,
        )
        decision_head = reference(
            "retry-decision/" + request.identity.command_id, decision.canonical_bytes()
        )
        subject = request.coverage.subject
        transmission = reference(
            subject.next_transmission_id,
            _TRANSMISSION_DOMAIN + request_fingerprint.encode(),
        )
        child = TransmissionAttempt(
            transmission=transmission,
            intent=intent_head,
            ordinal=subject.next_ordinal,
            semantics=cut.original_intent.mandate.semantics,
            dispatch_time_ns=request.current.observed_time_ns,
            payload_fingerprint=cut.original_intent.mandate.effect_fingerprint,
            recipient=cut.original_intent.mandate.recipient,
            idempotency_fence_key=cut.original_intent.mandate.idempotency_fence_key,
            send_commit=decision_head,
            coverage_proof=decision_head,
        )
        child_record_head = reference(subject.next_transmission_id, child.canonical_bytes())
        parent = RetriedParentRevision(
            original_intent=intent_head,
            original_authorization=authorization_head,
            send_decision=decision_head,
            predecessor=cut.current_parent,
            state=request.expected_parent_state,
            complete_ordered_children=(*subject.complete_prior_children, child_record_head),
            retained_evidence=tuple(source.subject for source in cut.complete_ordered_evidence),
            retained_obligation=retained_obligation,
            coverage=request.coverage,
            current_command_fingerprint=request.current.command_fingerprint,
            fence=request.fence,
        )
        return PreparedSafeRetransmission(
            source_request_fingerprint=request_fingerprint,
            decision=decision,
            child=child,
            parent=parent,
            complete_batch_fingerprint=_sha(
                _BATCH_DOMAIN
                + decision.canonical_bytes()
                + child.canonical_bytes()
                + parent.canonical_bytes()
            ),
        )
    except (TypeError, ValueError, AttributeError) as error:
        return _reject(error)


async def prepare_lifecycle(request: EffectsLifecycleRequest) -> EffectsLifecycleResult:
    """Port-shaped retry entrypoint; semantic reduction remains a separate leaf."""
    if isinstance(request, PrepareSafeRetransmission):
        return prepare_safe_retransmission(request)
    return _reject(_Rejected("semantic reduction is not implemented by retry preparation"))


__all__ = ["prepare_lifecycle", "prepare_safe_retransmission"]
