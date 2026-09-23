"""Effects-owned VISION dispatch algebra; this module grants no raw I/O authority.

Inputs representing current state/evidence must be independently reproduced by
the broker at the registered commit boundary. Constructing these values is not
authentication, a current-head check, a lease proof, or a send permit.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

from .contracts import (
    AttemptState,
    AuthorityBinding,
    DispatchSemanticBinding,
    ExactHead,
    ExternalActionIntent,
    SafeRetransmission,
    TransmissionAttempt,
)

TERMINAL_STATES: frozenset[AttemptState] = frozenset(
    {
        "CONFIRMED",
        "FAILED_NO_EFFECT",
        "PARTIAL_CONFIRMED",
        "CANCELLED_BEFORE_SEND",
        "SUPERSEDED_BEFORE_SEND",
    }
)
PRE_SEND_STATES: frozenset[AttemptState] = frozenset(
    {"INTENT_RECORDED", "HELD_BEFORE_SEND", "DISPATCH_AUTHORIZED"}
)
RETRANSMISSION_STATES: frozenset[AttemptState] = frozenset(
    {"SEND_COMMITTED", "SENT", "OUTCOME_UNKNOWN"}
)
_TRANSITIONS: dict[AttemptState, frozenset[AttemptState]] = {
    "INTENT_RECORDED": frozenset(
        {
            "HELD_BEFORE_SEND",
            "CANCELLED_BEFORE_SEND",
            "SUPERSEDED_BEFORE_SEND",
            "DISPATCH_AUTHORIZED",
        }
    ),
    "HELD_BEFORE_SEND": frozenset(
        {"DISPATCH_AUTHORIZED", "CANCELLED_BEFORE_SEND", "SUPERSEDED_BEFORE_SEND"}
    ),
    "DISPATCH_AUTHORIZED": frozenset(
        {"HELD_BEFORE_SEND", "CANCELLED_BEFORE_SEND", "SUPERSEDED_BEFORE_SEND", "SEND_COMMITTED"}
    ),
    "SEND_COMMITTED": frozenset(
        {"SENT", "CONFIRMED", "FAILED_NO_EFFECT", "OUTCOME_UNKNOWN", "PARTIAL"}
    ),
    "SENT": frozenset({"CONFIRMED", "FAILED_NO_EFFECT", "OUTCOME_UNKNOWN", "PARTIAL"}),
    "OUTCOME_UNKNOWN": frozenset({"CONFIRMED", "FAILED_NO_EFFECT", "PARTIAL_CONFIRMED"}),
    "PARTIAL": frozenset({"CONFIRMED", "FAILED_NO_EFFECT", "PARTIAL_CONFIRMED"}),
    "CONFIRMED": frozenset(),
    "FAILED_NO_EFFECT": frozenset(),
    "PARTIAL_CONFIRMED": frozenset(),
    "CANCELLED_BEFORE_SEND": frozenset(),
    "SUPERSEDED_BEFORE_SEND": frozenset(),
}


class EffectRuleViolation(ValueError):
    def __init__(self, code: str, reason: str) -> None:
        self.code = code
        super().__init__(reason)


def require_semantics(
    actual: DispatchSemanticBinding,
    supported: DispatchSemanticBinding | None,
    *,
    durable: bool,
) -> None:
    if supported is None or actual != supported:
        raise EffectRuleViolation(
            "DISPATCH_VERSION_HOLD" if durable else "DISPATCH_VERSION_DENIED",
            "complete bound dispatch semantics unavailable or unequal",
        )


def require_current_authority(
    intent: ExternalActionIntent, current: AuthorityBinding, now_ns: int
) -> None:
    """Compare the complete independently reproduced value at the writer cut."""
    if now_ns < 0 or intent.authority != current:
        raise EffectRuleViolation("STALE", "complete intent/current authority binding differs")
    if current.valid_until_ns <= now_ns or any(
        row.valid_until_ns <= now_ns for row in current.reads
    ):
        raise EffectRuleViolation("STALE", "authority or an authority read expired at boundary")
    if hashlib.sha256(intent.payload).hexdigest() != intent.effect_fingerprint:
        raise EffectRuleViolation("DENIED", "payload differs from exact effect fingerprint")


def require_transition(
    prior: AttemptState,
    proposed: AttemptState,
    *,
    writer: Literal["SEND_ADAPTER", "AUTHENTICATED_EVIDENCE", "AUTHORIZED_RECONCILER"],
    new_child: bool = False,
) -> None:
    if writer not in {"SEND_ADAPTER", "AUTHENTICATED_EVIDENCE", "AUTHORIZED_RECONCILER"}:
        raise EffectRuleViolation("DENIED", "unknown semantic writer role")
    if prior not in _TRANSITIONS or proposed not in _TRANSITIONS:
        raise EffectRuleViolation("DENIED", "unknown dispatch state")
    if prior in TERMINAL_STATES:
        raise EffectRuleViolation("CONFLICT", "original attempt is terminal")
    if new_child and proposed == prior:
        if prior not in RETRANSMISSION_STATES or writer != "SEND_ADAPTER":
            raise EffectRuleViolation(
                "DENIED", "child allocation outside safe retransmission branch"
            )
        return
    if proposed not in _TRANSITIONS[prior]:
        raise EffectRuleViolation("DENIED", "transition absent from exact VISION registry")
    if prior in PRE_SEND_STATES:
        if writer != "SEND_ADAPTER":
            raise EffectRuleViolation("DENIED", "only send adapter owns pre-send transitions")
        if new_child != (proposed == "SEND_COMMITTED"):
            raise EffectRuleViolation("DENIED", "first child and SEND_COMMITTED must be atomic")
    elif writer == "SEND_ADAPTER" or new_child:
        raise EffectRuleViolation("DENIED", "outcome transition requires evidence-driven successor")


@dataclass(frozen=True)
class ChildMemberEvidence:
    """Already authenticated durable observation, consumed under its bound reducer."""

    evidence: ExactHead
    child: ExactHead
    member: ExactHead
    outcome: Literal["OCCURRED", "PERMANENTLY_INCAPABLE", "POSSIBLE", "ABSENCE_OBSERVED"]


def reduce_child_evidence(
    prior: AttemptState,
    children: tuple[TransmissionAttempt, ...],
    members: tuple[ExactHead, ...],
    observations: tuple[ChildMemberEvidence, ...],
) -> AttemptState:
    """Conservative complete-child/member reduction; absence never proves no effect."""
    if prior not in {"SEND_COMMITTED", "SENT", "OUTCOME_UNKNOWN", "PARTIAL"}:
        raise EffectRuleViolation(
            "DENIED", "evidence reducer requires a crossed nonterminal attempt"
        )
    if not children or not members:
        raise EffectRuleViolation("RECOVERY_HOLD", "empty child or inseparable effect manifest")
    child_refs = tuple(child.transmission for child in children)
    if len(set(child_refs)) != len(child_refs) or len(set(members)) != len(members):
        raise EffectRuleViolation("CONFLICT", "duplicate child or effect member")
    if tuple(child.ordinal for child in children) != tuple(range(len(children))):
        raise EffectRuleViolation(
            "RECOVERY_HOLD", "incomplete or reordered retained child ordinals"
        )
    first = children[0]
    if any(
        child.intent != first.intent
        or child.semantics != first.semantics
        or child.payload_fingerprint != first.payload_fingerprint
        or child.recipient != first.recipient
        or child.idempotency_fence_key != first.idempotency_fence_key
        for child in children
    ):
        raise EffectRuleViolation(
            "CONFLICT", "child intent/binding/target differs within logical attempt"
        )
    evidence_ids: dict[str, ExactHead] = {}
    observed: dict[tuple[ExactHead, ExactHead], set[str]] = {}
    for row in observations:
        if row.child not in child_refs or row.member not in members:
            raise EffectRuleViolation(
                "CONFLICT", "evidence substitutes unknown child or effect member"
            )
        previous = evidence_ids.setdefault(row.evidence.subject_id, row.evidence)
        if previous != row.evidence:
            raise EffectRuleViolation("CONFLICT", "rival evidence identity")
        observed.setdefault((row.child, row.member), set()).add(row.outcome)
    occurred = 0
    incapable = 0
    uncertain = False
    for member in members:
        child_states = [observed.get((child, member), set()) for child in child_refs]
        if any({"OCCURRED", "PERMANENTLY_INCAPABLE"} <= states for states in child_states):
            uncertain = True
            continue
        # Every child must have a positive terminal observation. A confirmed
        # child never hides another child that may still produce a duplicate.
        if any(
            not states.intersection({"OCCURRED", "PERMANENTLY_INCAPABLE"})
            for states in child_states
        ):
            uncertain = True
            if any("OCCURRED" in states for states in child_states):
                occurred += 1
            continue
        if any("OCCURRED" in states for states in child_states):
            occurred += 1
        else:
            incapable += 1
    if uncertain:
        if prior in {"OUTCOME_UNKNOWN", "PARTIAL"}:
            return prior
        return "PARTIAL" if occurred and len(members) > 1 else "OUTCOME_UNKNOWN"
    if occurred == len(members):
        return "CONFIRMED"
    if incapable == len(members):
        return "FAILED_NO_EFFECT"
    # VISION allows PARTIAL_CONFIRMED only from denying unknown/partial states.
    return "PARTIAL_CONFIRMED" if prior in {"OUTCOME_UNKNOWN", "PARTIAL"} else "PARTIAL"


def require_safe_retransmission(
    state: AttemptState,
    intent: ExternalActionIntent,
    children: tuple[TransmissionAttempt, ...],
    request: SafeRetransmission,
    *,
    independently_verified_proof: SafeRetransmission | None,
    current_authority: AuthorityBinding,
    supported_semantics: DispatchSemanticBinding | None,
    now_ns: int,
) -> None:
    require_transition(state, state, writer="SEND_ADAPTER", new_child=True)
    require_semantics(intent.semantics, supported_semantics, durable=True)
    require_current_authority(intent, current_authority, now_ns)
    if request != independently_verified_proof:
        raise EffectRuleViolation(
            "RECOVERY_HOLD", "no independently issued exact safe-retransmission proof"
        )
    if not children or tuple(child.ordinal for child in children) != tuple(range(len(children))):
        raise EffectRuleViolation("RECOVERY_HOLD", "complete retained ordinal lineage required")
    if request.ordinal != len(children) or request.prior_children != tuple(
        child.transmission for child in children
    ):
        raise EffectRuleViolation(
            "STALE", "safe proof omits, reorders, or substitutes a prior child"
        )
    if (
        request.covered_effect_fingerprint != intent.effect_fingerprint
        or not request.coverage_starts_ns <= now_ns < request.coverage_expires_ns
        or any(
            child.semantics != intent.semantics
            or child.intent.subject_id != intent.intent_id
            or child.intent.fingerprint != intent.fingerprint
            or child.payload_fingerprint != intent.effect_fingerprint
            or child.recipient != intent.authority.recipient
            or child.idempotency_fence_key != intent.idempotency_fence_key
            for child in children
        )
    ):
        raise EffectRuleViolation(
            "RECOVERY_HOLD", "proof does not cover current exact effect and interval"
        )
    if any(child.dispatch_time_ns > now_ns for child in children):
        raise EffectRuleViolation("RECOVERY_HOLD", "child dispatch lies beyond current proof cut")
    if request.proof_kind == "PROVIDER_IDEMPOTENCY_COVERS_ALL" and any(
        not request.coverage_starts_ns <= child.dispatch_time_ns < request.coverage_expires_ns
        for child in children
    ):
        raise EffectRuleViolation(
            "RECOVERY_HOLD", "provider coverage excludes an earlier transmission"
        )


def require_fresh_recovery_originals(
    intent: ExternalActionIntent,
    current_originals: tuple[ExactHead, ...],
) -> None:
    purpose = intent.purpose
    expected: tuple[ExactHead, ...]
    if purpose.kind == "COMPENSATION":
        expected = (purpose.original.ambiguity_reconciliation_head,)
    elif purpose.kind == "AUTHORIZE_DUPLICATE_RISK":
        expected = tuple(item.ambiguity_reconciliation_head for item in purpose.unresolved_attempts)
    else:
        raise EffectRuleViolation("DENIED", "ordinary intent cannot borrow recovery authorization")
    if not expected or expected != current_originals or len(set(expected)) != len(expected):
        raise EffectRuleViolation(
            "STALE", "original ambiguity resolved, changed, duplicated or omitted"
        )
