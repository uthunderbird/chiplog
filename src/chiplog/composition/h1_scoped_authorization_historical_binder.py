"""Read-only historical binding for a selected scoped-authorization V3 pair.

The V3 pair is structurally closed by ``scoped_authorization_history``.  This
module adds the missing selected-prefix boundary.  It deliberately does not
turn the retained inventory into authority: every inventory role still needs
an independently retained historical reader before this binder can succeed.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import cast

from chiplog.capabilities.effects.dispatch_v2 import canonical, reference
from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
from chiplog.composition.h1_j7_historical_scope import verify_h1_j7_historical_scope
from chiplog.composition.h1_scoped_authorization_issuance_v3 import ScopedAuthorizationIssuanceV3
from chiplog.composition.r16_dispatch_history import decode_selected_scoped_authorization_batch_v3
from chiplog.platform._owner_publication_contracts import CompleteDeliveryBatchV2
from chiplog.platform.authority_gate import AuthorityGate
from chiplog.platform.owner_decision_journal import OwnerJournalSnapshot
from chiplog.platform.owner_publications import SelectedOwnerDecision


class ScopedAuthorizationHistoricalBindError(ValueError):
    """The selected pair or its authenticated predecessor is inconsistent."""


class ScopedAuthorizationHistoricalBindHold(ScopedAuthorizationHistoricalBindError):
    """A required historical authority source has no installed reader."""


@dataclass(frozen=True, slots=True)
class ScopedAuthorizationHistoricalBinding:
    """Non-authorizing evidence that the exact selected pair reached its cut."""

    decision: SelectedOwnerDecision
    issuance: ScopedAuthorizationIssuanceV3
    predecessor_head: str | None


class H1ScopedAuthorizationHistoricalBinder:
    """Installed-only reader for selected V3 authorization history.

    The object holds no preparation, worker, or current-authority capability.
    It is installed under the runtime's common authority gate and only reads
    the owner journal and historical trust reader while that gate is held.
    """

    def __init__(self, runtime: CommonCliExecutionRuntime) -> None:
        if type(runtime) is not CommonCliExecutionRuntime:
            raise TypeError("scoped authorization historical binder requires installed runtime")
        gate = runtime._authority_gate()
        if type(gate) is not AuthorityGate:
            raise TypeError("scoped authorization historical binder requires AuthorityGate")
        self._runtime = runtime
        self._gate = gate

    def bind(self, decision: SelectedOwnerDecision) -> ScopedAuthorizationHistoricalBinding:
        if type(decision) is not SelectedOwnerDecision:
            raise TypeError("scoped authorization historical binding requires selected decision")
        self._gate.require_held()
        decision = self._authenticated_selected_decision(decision)
        issuance = decode_selected_scoped_authorization_batch_v3(decision)
        if issuance is None:
            raise ScopedAuthorizationHistoricalBindError(
                "selected decision is not scoped authorization V3"
            )
        predecessor_head, predecessor = self._authenticated_predecessor(decision, issuance)
        predecessor_version = self._verify_h1_intent_and_j7_scope(issuance, predecessor)
        self._require_independent_inventory_sources(issuance, predecessor_version)
        return ScopedAuthorizationHistoricalBinding(decision, issuance, predecessor_head)

    def _authenticated_selected_decision(
        self, supplied: SelectedOwnerDecision
    ) -> SelectedOwnerDecision:
        """Reacquire the exact selected decision before decoding retained evidence."""
        owner_journal = self._runtime._owner_decisions()
        snapshot = getattr(owner_journal, "snapshot", None)
        if not callable(snapshot):
            raise ScopedAuthorizationHistoricalBindError(
                "selected authorization lacks authenticated owner selection"
            )
        try:
            current = snapshot()
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            raise ScopedAuthorizationHistoricalBindError(
                "selected authorization owner selection is unreadable"
            ) from error
        matches = [
            decision
            for decision in current.decisions
            if decision.decision_id == supplied.decision_id
        ]
        if len(matches) != 1 or matches[0] != supplied:
            raise ScopedAuthorizationHistoricalBindError(
                "selected authorization decision differs from authenticated selection"
            )
        return cast(SelectedOwnerDecision, matches[0])

    def _authenticated_predecessor(
        self, decision: SelectedOwnerDecision, issuance: ScopedAuthorizationIssuanceV3
    ) -> tuple[str | None, OwnerJournalSnapshot]:
        owner_journal = self._runtime._owner_decisions()
        raw = getattr(owner_journal, "_raw", None)
        entries = getattr(raw, "entries", None)
        snapshot_at = getattr(owner_journal, "snapshot_at", None)
        if not callable(entries) or not callable(snapshot_at):
            raise ScopedAuthorizationHistoricalBindError(
                "selected authorization lacks raw owner journal"
            )
        try:
            physical = tuple(entries())
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            raise ScopedAuthorizationHistoricalBindError(
                "selected authorization raw journal is unreadable"
            ) from error
        matching = [
            (index, entry)
            for index, entry in enumerate(physical)
            if entry[0] == decision.decision_id
        ]
        if len(matching) != 1:
            raise ScopedAuthorizationHistoricalBindError(
                "selected authorization physical decision is absent or ambiguous"
            )
        index, entry = matching[0]
        raw_id, raw_predecessor, raw_bytes = entry
        if (
            raw_id != decision.decision_id
            or decision.decision_head != raw_id
            or hashlib.sha256(raw_bytes).hexdigest() != decision.decision_fingerprint
        ):
            raise ScopedAuthorizationHistoricalBindError(
                "selected authorization physical decision differs"
            )
        # The journal predecessor link is the only raw locator for this cut.
        # No marker grammar has been versioned for this V3 authorization, so
        # any entry between that cut and this decision is rejected by requiring
        # the physical predecessor to be immediately adjacent.
        expected_predecessor = physical[index - 1][0] if index else None
        if raw_predecessor != expected_predecessor:
            raise ScopedAuthorizationHistoricalBindError(
                "selected authorization raw predecessor differs"
            )
        expected_ref = reference("owner-journal", canonical(raw_predecessor))
        retained_ref = issuance.original_cut.current.observation.cut.selected_journal_head
        if retained_ref != expected_ref:
            raise ScopedAuthorizationHistoricalBindError(
                "selected authorization retained journal predecessor differs"
            )
        try:
            predecessor = snapshot_at(raw_predecessor)
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            raise ScopedAuthorizationHistoricalBindError(
                "selected authorization predecessor snapshot is invalid"
            ) from error
        if (
            predecessor.head != raw_predecessor
            or predecessor.tenant_id != issuance.identity.tenant_id
            or any(item.decision_id == decision.decision_id for item in predecessor.decisions)
        ):
            raise ScopedAuthorizationHistoricalBindError(
                "selected authorization predecessor snapshot differs"
            )
        return raw_predecessor, predecessor

    def _verify_h1_intent_and_j7_scope(
        self, issuance: ScopedAuthorizationIssuanceV3, predecessor: OwnerJournalSnapshot
    ) -> str:
        """Authenticate retained H1 intent via its existing selected binder.

        The original selected H1 completion's full bytes are verified by that
        binder.  J7 is independently replayed from its retained trust prefix.
        """
        from chiplog.composition.h1_historical_selected_sources import bind_selected_h1_completion

        try:
            from chiplog.capabilities.effects.scoped_intent_record_contracts import (
                make_scoped_intent_member,
            )
            from chiplog.composition.h1_completion_issuance import (
                H1CompletionIssuanceV3,
                H1CompletionIssuanceV4,
                decode_h1_completion_issuance,
            )

            observed = issuance.original_cut.observed.original_intent
            original_matches: list[SelectedOwnerDecision] = []
            for candidate in predecessor.decisions:
                batch = candidate.prepared.request
                if type(batch) is not CompleteDeliveryBatchV2:
                    continue
                decoded = decode_h1_completion_issuance(batch)
                if type(decoded) in (H1CompletionIssuanceV3, H1CompletionIssuanceV4):
                    scoped_issuance = cast(H1CompletionIssuanceV3 | H1CompletionIssuanceV4, decoded)
                    member = make_scoped_intent_member(
                        scoped_issuance.effects_result.intent_result.intent
                    )
                else:
                    continue
                observed_member = make_scoped_intent_member(observed)
                if member == observed_member:
                    original_matches.append(candidate)
            if len(original_matches) != 1:
                raise ValueError("original selected H1 intent is absent or ambiguous")
            original = original_matches[0]
            original_batch = original.prepared.request
            if type(original_batch) is not CompleteDeliveryBatchV2:
                raise ValueError("original selected H1 batch differs")
            bound = bind_selected_h1_completion(original_batch, self._runtime)
            if bound.decision != original:
                raise ValueError("original selected H1 decision differs")
            original_issuance = bound.issuance
            if type(original_issuance) is H1CompletionIssuanceV3:
                original_member = make_scoped_intent_member(
                    original_issuance.effects_result.intent_result.intent
                )
                predecessor_version = "V3"
            elif type(original_issuance) is H1CompletionIssuanceV4:
                original_member = make_scoped_intent_member(
                    original_issuance.effects_result.intent_result.intent
                )
                predecessor_version = "V4"
            else:
                raise ValueError("original selected H1 issuance differs")
            if original_member != make_scoped_intent_member(observed):
                raise ValueError("original selected H1 intent bytes differ")
            verify_h1_j7_historical_scope(
                issuance.h1_current_scope_exchange,
                original_issuance.effects_call.request.selected_scope.scope,
                trust_reader=self._runtime._h1_historical_trust_reader(),
                authority_gate=self._gate,
            )
            return predecessor_version
        except (TypeError, ValueError, RuntimeError) as error:
            raise ScopedAuthorizationHistoricalBindError(
                "selected authorization original H1 intent or J7 historical scope differs"
            ) from error

    @staticmethod
    def _require_independent_inventory_sources(
        issuance: ScopedAuthorizationIssuanceV3, predecessor_version: str
    ) -> None:
        """Fail closed until every retained source role has a prefix reader.

        V3 retains values for all eleven roles, but this runtime has no
        versioned independent original-prefix readers for those values.  A
        value retained inside the authorization request is not such a reader.
        """
        roles: tuple[str, ...]
        if predecessor_version == "V4":
            # H1 V4's selected producer and historical sources authenticate the
            # first four source roles. These seven current inventory roles
            # remain unmounted and therefore keep authorization on hold.
            roles = (
                "endpoint",
                "credential_lifecycle",
                "deployment_entitlement",
                "clock",
                "effects_history",
                "normative_conflict_generation",
                "runtime_and_fence",
            )
        elif predecessor_version == "V3":
            roles = tuple(type(issuance.original_cut.current.observation.sources).model_fields)
        else:
            raise ScopedAuthorizationHistoricalBindError(
                "selected authorization predecessor issuance version is unsupported"
            )
        raise ScopedAuthorizationHistoricalBindHold(
            "selected authorization lacks independent historical readers for inventory roles: "
            + ", ".join(roles)
        )


def bind_selected_scoped_authorization_v3(
    decision: SelectedOwnerDecision, runtime: object
) -> ScopedAuthorizationHistoricalBinding:
    """Use only the binder installed on this exact common runtime."""
    if type(runtime) is not CommonCliExecutionRuntime:
        raise ScopedAuthorizationHistoricalBindHold(
            "selected scoped authorization historical binder is unavailable"
        )
    binder = getattr(runtime, "_h1_scoped_authorization_historical_binder", None)
    if type(binder) is not H1ScopedAuthorizationHistoricalBinder or binder._runtime is not runtime:
        raise ScopedAuthorizationHistoricalBindHold(
            "selected scoped authorization historical binder is unavailable"
        )
    return binder.bind(decision)


__all__ = [
    "H1ScopedAuthorizationHistoricalBinder",
    "ScopedAuthorizationHistoricalBindError",
    "ScopedAuthorizationHistoricalBindHold",
    "ScopedAuthorizationHistoricalBinding",
    "bind_selected_scoped_authorization_v3",
]
