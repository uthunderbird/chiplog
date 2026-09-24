"""Fail-closed boundary for independently selected historical H1 sources.

The H1 issuance is evidence, never a substitute for the raw H0/R17/R16/native
and trust selections that originally authorized it.  This module deliberately
does not fall back to the current H1 reader or a live dispatch resource.

The runtime raw-source adapter has not yet been mounted.  Keeping the adapter
check here makes that absence explicit at the historical authority boundary
instead of silently accepting a schema-valid issuance.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from chiplog.platform._owner_publication_contracts import CompleteDeliveryBatchV2
from chiplog.platform.owner_publications import SelectedOwnerDecision

if TYPE_CHECKING:
    from chiplog.composition.h1_completion_issuance import H1CompletionIssuanceV1
    from chiplog.composition.r14_runtime import R14PlanningRuntime


@dataclass(frozen=True, slots=True)
class H1HistoricalSelection:
    """The exact selected owner decision, paired with its verified issuance."""

    decision: SelectedOwnerDecision
    issuance: H1CompletionIssuanceV1


@dataclass(frozen=True, slots=True)
class _HistoricalPorts:
    """Private registered ports required before historical verification exists."""

    gate: object
    custody: object
    loop_journal: object
    owner_journal: object
    trust_reader: object


def _require_historical_ports(runtime: object) -> _HistoricalPorts:
    """Require the registered read-only ports; never substitute current services."""
    gate_factory = getattr(runtime, "_authority_gate", None)
    if not callable(gate_factory):
        raise ValueError("H1 historical validation lacks its authority gate")
    gate = gate_factory()
    if not callable(getattr(gate, "hold", None)):
        raise ValueError("H1 historical validation has an unsupported authority gate")

    custody_factory = getattr(runtime, "_h1_historical_custody", None)
    if not callable(custody_factory):
        raise ValueError("H1 historical validation lacks registered historical custody")
    custody = custody_factory()
    if custody is None:
        raise ValueError("H1 historical validation has no registered historical custody")

    loop_factory = getattr(runtime, "_loop_decisions", None)
    owner_factory = getattr(runtime, "_owner_decisions", None)
    trust_factory = getattr(runtime, "_h1_historical_trust_reader", None)
    if not callable(loop_factory) or not callable(owner_factory) or not callable(trust_factory):
        raise ValueError("H1 historical validation lacks a registered raw selected-source reader")
    loop_journal = loop_factory()
    owner_journal = owner_factory()
    trust_reader = trust_factory()
    if (
        not callable(getattr(loop_journal, "entries", None))
        or not callable(getattr(owner_journal, "snapshot", None))
        or trust_reader is None
    ):
        raise ValueError("H1 historical validation has an unsupported raw selected-source reader")
    return _HistoricalPorts(gate, custody, loop_journal, owner_journal, trust_reader)


def _require_typed_inputs(batch: object, issuance: object) -> None:
    if type(batch) is not CompleteDeliveryBatchV2:
        raise TypeError("H1 historical sources require CompleteDeliveryBatchV2")
    # Delay this import: the issuance module statically imports this seam.
    from chiplog.composition.h1_completion_issuance import H1CompletionIssuanceV1

    if type(issuance) is not H1CompletionIssuanceV1:
        raise TypeError("H1 historical sources require H1CompletionIssuanceV1")


def verify_h1_historical_sources(
    batch: CompleteDeliveryBatchV2,
    issuance: H1CompletionIssuanceV1,
    runtime: R14PlanningRuntime,
) -> None:
    """Verify H1 source selection, or reject until its raw adapter is mounted.

    A positive result requires an adapter that authenticates and joins the raw
    H0 loop, R17 ingress, R16 signed custody, native selected publications and
    physical trust materialization under the gate.  No such adapter currently
    exists in R14, so accepting the partially exposed semantic snapshots would
    be a false historical proof.
    """
    _require_typed_inputs(batch, issuance)
    _require_historical_ports(runtime)
    raise ValueError("H1 historical raw selected-source verification is not mounted")


def bind_selected_h1_completion(
    batch: CompleteDeliveryBatchV2, runtime: R14PlanningRuntime
) -> H1HistoricalSelection:
    """Bind an H1 batch to its selected decision, or fail closed.

    Selection cannot be projected from an issuance because the latter contains
    no decision identity.  Until the raw verifier is mounted this function also
    rejects, rather than returning an unauthenticated snapshot decision.
    """
    if type(batch) is not CompleteDeliveryBatchV2:
        raise TypeError("H1 historical sources require CompleteDeliveryBatchV2")
    _require_historical_ports(runtime)
    raise ValueError("H1 historical selected completion binding is not mounted")
