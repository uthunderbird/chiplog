"""Exact wire join for the initial terminal result of a scoped V3 delivery.

This is a path-scoped structural check.  It preserves canonical selected effects
records and proves that a terminal parent, its closed obligation revision, and
its consumable semantic reduction describe one selected atomic batch.  It does
not authenticate the selection against a journal or SQL materialization.
"""

from __future__ import annotations

import hashlib
from typing import Annotated, Final, Literal

from pydantic import Field, TypeAdapter, model_validator

from chiplog.capabilities.effects.contracts import ExactHead
from chiplog.capabilities.effects.dispatch_authority_contracts import DispatchObservationDTO
from chiplog.capabilities.effects.fences import Present
from chiplog.capabilities.effects.lifecycle_transition_contracts import (
    EffectsObligationRevision,
    EffectsReductionConsumable,
    EffectsSemanticReductionRecord,
    SelectedEffectsSource,
)
from chiplog.capabilities.effects.scoped_dispatch_contracts import AllChildOutcomeParent

ALL_CHILD_OUTCOME_PARENT_SCHEMA: Final[Literal["chiplog.effects.all-child-outcome-parent.v1"]] = (
    "chiplog.effects.all-child-outcome-parent.v1"
)
ALL_CHILD_OBLIGATION_SCHEMA: Final[Literal["chiplog.effects.all-child-obligation.v1"]] = (
    "chiplog.effects.all-child-obligation.v1"
)
SEMANTIC_REDUCTION_SCHEMA: Final[Literal["chiplog.effects.semantic-reduction.v1"]] = (
    "chiplog.effects.semantic-reduction.v1"
)


class AtomicOutcomeSourceIntegrityError(ValueError):
    """A retained selected source cannot be decoded as its declared record."""


class _SelectedPhysicalEffectsSource(SelectedEffectsSource):
    """Effects source whose bytes are exactly one retained physical record."""

    owner: Literal["effects"] = "effects"

    @model_validator(mode="after")
    def _bind_physical_bytes(self) -> _SelectedPhysicalEffectsSource:
        if (
            self.owner != "effects"
            or self.subject != self.physical_record
            or hashlib.sha256(self.canonical_record_bytes).hexdigest()
            != self.physical_record.fingerprint
        ):
            raise ValueError("selected effects source does not preserve physical record bytes")
        return self


class SelectedAllChildOutcomeParentSource(_SelectedPhysicalEffectsSource):
    """Selected physical initial parent revision, never a later refinement."""

    schema_id: Literal["chiplog.effects.all-child-outcome-parent.v1"] = (
        ALL_CHILD_OUTCOME_PARENT_SCHEMA
    )
    record: AllChildOutcomeParent

    @model_validator(mode="after")
    def _decode_parent_bytes(self) -> SelectedAllChildOutcomeParentSource:
        try:
            decoded = AllChildOutcomeParent.model_validate_json(self.canonical_record_bytes)
        except ValueError as error:
            raise AtomicOutcomeSourceIntegrityError("invalid all-child parent bytes") from error
        if decoded != self.record or self.canonical_record_bytes != self.record.canonical_bytes():
            raise ValueError("selected all-child parent bytes are not canonical")
        return self


class SelectedEffectsObligationRevisionSource(_SelectedPhysicalEffectsSource):
    """Selected physical all-child obligation revision."""

    schema_id: Literal["chiplog.effects.all-child-obligation.v1"] = ALL_CHILD_OBLIGATION_SCHEMA
    record: EffectsObligationRevision

    @model_validator(mode="after")
    def _decode_obligation_bytes(self) -> SelectedEffectsObligationRevisionSource:
        try:
            decoded = EffectsObligationRevision.model_validate_json(self.canonical_record_bytes)
        except ValueError as error:
            raise AtomicOutcomeSourceIntegrityError("invalid all-child obligation bytes") from error
        if decoded != self.record or self.canonical_record_bytes != self.record.canonical_bytes():
            raise ValueError("selected all-child obligation bytes are not canonical")
        return self


class SelectedEffectsSemanticReductionSource(_SelectedPhysicalEffectsSource):
    """Selected physical semantic-reduction record."""

    schema_id: Literal["chiplog.effects.semantic-reduction.v1"] = SEMANTIC_REDUCTION_SCHEMA
    record: EffectsSemanticReductionRecord

    @model_validator(mode="after")
    def _decode_reduction_bytes(self) -> SelectedEffectsSemanticReductionSource:
        try:
            decoded = EffectsSemanticReductionRecord.model_validate_json(
                self.canonical_record_bytes
            )
        except ValueError as error:
            raise AtomicOutcomeSourceIntegrityError("invalid semantic reduction bytes") from error
        if decoded != self.record or self.canonical_record_bytes != self.record.canonical_bytes():
            raise ValueError("selected semantic reduction bytes are not canonical")
        return self


class _TerminalAtomicClosureOutcome(DispatchObservationDTO):
    """Shared exact join for a first terminal closure result."""

    original_intent: ExactHead
    original_send: ExactHead
    parent: SelectedAllChildOutcomeParentSource
    obligation: SelectedEffectsObligationRevisionSource
    reduction: SelectedEffectsSemanticReductionSource

    @model_validator(mode="after")
    def _bind_atomic_terminal_closure(self) -> _TerminalAtomicClosureOutcome:
        parent = self.parent.record
        obligation = self.obligation.record
        reduction = self.reduction.record
        if (
            self.parent.selected_decision != self.obligation.selected_decision
            or self.parent.selected_decision != self.reduction.selected_decision
            or parent.original_intent != self.original_intent
            or obligation.original_intent != self.original_intent
            or reduction.original_intent != self.original_intent
            or obligation.original_send != self.original_send
            or parent.complete_ordered_children != obligation.complete_ordered_children
            or parent.complete_ordered_children != reduction.complete_ordered_children
            or not parent.complete_ordered_evidence
            or parent.complete_ordered_evidence != obligation.closure_evidence
            or parent.complete_ordered_evidence != reduction.complete_ordered_evidence
            or reduction.current_parent != self.parent.subject
            or not isinstance(parent.original_obligation, ExactHead)
            or parent.original_obligation != obligation.obligation
            or reduction.original_obligation != obligation.obligation
            or obligation.state != "CLOSED"
            or not isinstance(reduction.original_terminal_closure, Present)
            or reduction.original_terminal_closure.head != self.obligation.physical_record.head
            or reduction.original_terminal_closure.fingerprint
            != self.obligation.physical_record.fingerprint
            or not isinstance(reduction.disposition, EffectsReductionConsumable)
        ):
            raise ValueError("terminal outcome sources do not form one exact atomic closure")
        return self


class ScopedDeliveryAtomicConfirmedV3(_TerminalAtomicClosureOutcome):
    """Evidence for a selected CONFIRMED initial terminal closure."""

    kind: Literal["CONFIRMED"] = "CONFIRMED"

    @model_validator(mode="after")
    def _require_confirmed_parent(self) -> ScopedDeliveryAtomicConfirmedV3:
        if self.parent.record.state != "CONFIRMED":
            raise ValueError("CONFIRMED evidence requires a CONFIRMED parent")
        return self


class ScopedDeliveryAtomicNoEffectV3(_TerminalAtomicClosureOutcome):
    """Evidence for a selected FAILED_NO_EFFECT initial terminal closure."""

    kind: Literal["NO_EFFECT"] = "NO_EFFECT"

    @model_validator(mode="after")
    def _require_no_effect_parent(self) -> ScopedDeliveryAtomicNoEffectV3:
        if self.parent.record.state != "FAILED_NO_EFFECT":
            raise ValueError("NO_EFFECT evidence requires a FAILED_NO_EFFECT parent")
        return self


class ScopedDeliveryAtomicUnknownV3(DispatchObservationDTO):
    """No terminal claim; any available sources remain merely historical evidence."""

    kind: Literal["UNKNOWN"] = "UNKNOWN"
    parent: SelectedAllChildOutcomeParentSource | None = None
    obligation: SelectedEffectsObligationRevisionSource | None = None
    reduction: SelectedEffectsSemanticReductionSource | None = None


type ScopedDeliveryAtomicOutcomeV3 = Annotated[
    ScopedDeliveryAtomicConfirmedV3
    | ScopedDeliveryAtomicNoEffectV3
    | ScopedDeliveryAtomicUnknownV3,
    Field(discriminator="kind"),
]
SCOPED_DELIVERY_ATOMIC_OUTCOME_V3: TypeAdapter[ScopedDeliveryAtomicOutcomeV3] = TypeAdapter(
    ScopedDeliveryAtomicOutcomeV3
)

# Short aliases make the three source roles easy to use at the composition seam.
SelectedAtomicOutcomeParentSource = SelectedAllChildOutcomeParentSource
SelectedAtomicObligationSource = SelectedEffectsObligationRevisionSource
SelectedAtomicReductionSource = SelectedEffectsSemanticReductionSource
