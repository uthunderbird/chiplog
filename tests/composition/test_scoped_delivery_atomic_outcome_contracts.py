"""Wire-only checks for selected initial scoped-delivery terminal outcomes."""

from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from chiplog.capabilities.effects.contracts import ExactHead
from chiplog.capabilities.effects.fences import Absent, Present
from chiplog.capabilities.effects.lifecycle_transition_contracts import (
    EffectsObligationRevision,
    EffectsReductionConsumable,
    EffectsSemanticReductionRecord,
)
from chiplog.capabilities.effects.scoped_dispatch_contracts import AllChildOutcomeParent
from chiplog.composition.scoped_delivery_atomic_outcome_contracts import (
    SCOPED_DELIVERY_ATOMIC_OUTCOME_V3,
    ScopedDeliveryAtomicConfirmedV3,
    ScopedDeliveryAtomicNoEffectV3,
    ScopedDeliveryAtomicOutcomeV3,
    ScopedDeliveryAtomicUnknownV3,
    SelectedAllChildOutcomeParentSource,
    SelectedEffectsObligationRevisionSource,
    SelectedEffectsSemanticReductionSource,
)


def _head(subject_id: str, raw: bytes | None = None) -> ExactHead:
    material = raw if raw is not None else subject_id.encode()
    return ExactHead(
        subject_id=subject_id,
        head="head:" + subject_id,
        fingerprint=hashlib.sha256(material).hexdigest(),
    )


def _parent_source(
    record: AllChildOutcomeParent, decision: ExactHead
) -> SelectedAllChildOutcomeParentSource:
    raw = record.canonical_bytes()
    physical = _head("parent-revision", raw)
    return SelectedAllChildOutcomeParentSource(
        subject=physical,
        canonical_record_bytes=raw,
        selected_decision=decision,
        physical_record=physical,
        record=record,
    )


def _obligation_source(
    record: EffectsObligationRevision, decision: ExactHead
) -> SelectedEffectsObligationRevisionSource:
    raw = record.canonical_bytes()
    physical = _head("obligation-revision", raw)
    return SelectedEffectsObligationRevisionSource(
        subject=physical,
        canonical_record_bytes=raw,
        selected_decision=decision,
        physical_record=physical,
        record=record,
    )


def _reduction_source(
    record: EffectsSemanticReductionRecord, decision: ExactHead
) -> SelectedEffectsSemanticReductionSource:
    raw = record.canonical_bytes()
    physical = _head("reduction-revision", raw)
    return SelectedEffectsSemanticReductionSource(
        subject=physical,
        canonical_record_bytes=raw,
        selected_decision=decision,
        physical_record=physical,
        record=record,
    )


def _terminal(
    kind: str = "CONFIRMED",
) -> ScopedDeliveryAtomicConfirmedV3 | ScopedDeliveryAtomicNoEffectV3:
    intent = _head("intent")
    send = _head("original-send")
    decision = _head("atomic-selection")
    stable_obligation = _head("stable-obligation")
    children = (_head("child-0"), _head("child-1"))
    evidence = (_head("provider-evidence-0"), _head("provider-evidence-1"))
    parent = AllChildOutcomeParent(
        original_intent=intent,
        predecessor=_head("parent-predecessor"),
        state="CONFIRMED" if kind == "CONFIRMED" else "FAILED_NO_EFFECT",
        complete_ordered_children=children,
        complete_ordered_evidence=evidence,
        original_obligation=stable_obligation,
    )
    obligation = EffectsObligationRevision(
        obligation=stable_obligation,
        predecessor=Absent(),
        original_intent=intent,
        original_send=send,
        complete_ordered_children=children,
        state="CLOSED",
        closure_predicate=_head("closure-predicate"),
        resolver_binding=_head("resolver-binding"),
        closure_evidence=evidence,
    )
    parent_source = _parent_source(parent, decision)
    obligation_source = _obligation_source(obligation, decision)
    reduction = EffectsSemanticReductionRecord(
        stream_id="delivery-stream",
        reduction_id="reduction-id",
        original_intent=intent,
        predecessor=Absent(),
        reducer=_head("registered-reducer"),
        complete_ordered_children=children,
        complete_ordered_evidence=evidence,
        current_parent=parent_source.subject,
        original_obligation=stable_obligation,
        original_terminal_closure=Present(
            head=obligation_source.physical_record.head,
            fingerprint=obligation_source.physical_record.fingerprint,
        ),
        disposition=EffectsReductionConsumable(semantic_class="delivery-terminal"),
    )
    reduction_source = _reduction_source(reduction, decision)
    if kind == "CONFIRMED":
        return ScopedDeliveryAtomicConfirmedV3(
            original_intent=intent,
            original_send=send,
            parent=parent_source,
            obligation=obligation_source,
            reduction=reduction_source,
        )
    return ScopedDeliveryAtomicNoEffectV3(
        original_intent=intent,
        original_send=send,
        parent=parent_source,
        obligation=obligation_source,
        reduction=reduction_source,
    )


@pytest.mark.parametrize("kind", ["CONFIRMED", "NO_EFFECT", "UNKNOWN"])
def test_round_trip_discriminated_atomic_outcomes(kind: str) -> None:
    value: ScopedDeliveryAtomicOutcomeV3
    if kind == "CONFIRMED":
        value = _terminal()
    elif kind == "NO_EFFECT":
        value = _terminal("NO_EFFECT")
    else:
        value = ScopedDeliveryAtomicUnknownV3(parent=_terminal().parent)
    assert SCOPED_DELIVERY_ATOMIC_OUTCOME_V3.validate_json(value.canonical_bytes()) == value


def test_same_selected_sources_cannot_be_relabeled_as_opposite_terminal_outcome() -> None:
    confirmed = _terminal()
    with pytest.raises(ValidationError):
        ScopedDeliveryAtomicNoEffectV3.model_validate(
            {**confirmed.model_dump(), "kind": "NO_EFFECT"}
        )


def test_rejects_cross_record_evidence_mutation_with_recomputed_source_hash() -> None:
    confirmed = _terminal()
    altered = confirmed.parent.record.model_copy(
        update={"complete_ordered_evidence": (_head("different-evidence"),)}
    )
    with pytest.raises(ValidationError):
        ScopedDeliveryAtomicConfirmedV3.model_validate(
            confirmed.model_copy(
                update={"parent": _parent_source(altered, confirmed.parent.selected_decision)}
            ).model_dump()
        )


def test_rejects_terminal_claim_with_missing_evidence_despite_a_valid_source_hash() -> None:
    confirmed = _terminal()
    empty_evidence_parent = confirmed.parent.record.model_copy(
        update={"complete_ordered_evidence": ()}
    )
    empty_evidence_obligation = confirmed.obligation.record.model_copy(
        update={"closure_evidence": ()}
    )
    empty_evidence_reduction = confirmed.reduction.record.model_copy(
        update={"complete_ordered_evidence": ()}
    )
    with pytest.raises(ValidationError):
        ScopedDeliveryAtomicConfirmedV3.model_validate(
            confirmed.model_copy(
                update={
                    "parent": _parent_source(
                        empty_evidence_parent, confirmed.parent.selected_decision
                    ),
                    "obligation": _obligation_source(
                        empty_evidence_obligation, confirmed.obligation.selected_decision
                    ),
                    "reduction": _reduction_source(
                        empty_evidence_reduction, confirmed.reduction.selected_decision
                    ),
                }
            ).model_dump()
        )


def test_rejects_missing_closure_or_cross_batch_decision_with_recomputed_sources() -> None:
    confirmed = _terminal()
    no_closure = confirmed.reduction.record.model_copy(
        update={"original_terminal_closure": Absent()}
    )
    with pytest.raises(ValidationError):
        ScopedDeliveryAtomicConfirmedV3.model_validate(
            confirmed.model_copy(
                update={
                    "reduction": _reduction_source(
                        no_closure, confirmed.reduction.selected_decision
                    )
                }
            ).model_dump()
        )

    other_decision = _head("other-atomic-selection")
    with pytest.raises(ValidationError):
        ScopedDeliveryAtomicConfirmedV3.model_validate(
            confirmed.model_copy(
                update={"reduction": _reduction_source(confirmed.reduction.record, other_decision)}
            ).model_dump()
        )


def test_rejects_v2_or_unknown_selected_source_schemas() -> None:
    confirmed = _terminal()
    wire = confirmed.model_dump(mode="json")
    wire["parent"]["schema_id"] = "chiplog.effects.all-child-outcome-parent.v2"
    with pytest.raises(ValidationError):
        ScopedDeliveryAtomicConfirmedV3.model_validate(wire)

    wire = confirmed.model_dump(mode="json")
    wire["reduction"]["schema_id"] = "chiplog.effects.semantic-reduction.v99"
    with pytest.raises(ValidationError):
        ScopedDeliveryAtomicConfirmedV3.model_validate(wire)
