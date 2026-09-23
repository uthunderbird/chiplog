"""Owner-local recovery joins over immutable authoritative observations.

The broker must independently enumerate and authenticate these inputs again at
the publication cut. A returned join is a candidate, never a commit credential.
"""

from dataclasses import dataclass

from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import (
    CallRecoveryFrontier,
    EvidenceReduction,
    ReadOnlyAcceptedCall,
    ReadOnlyAttemptMember,
    TerminalCallFrontier,
)


class RecoveryIntegrityFault(ValueError):
    """Immutable subject, branch or lineage facts contradict their contract."""


class RecoveryHold(ValueError):
    """Complete accountable or consumable evidence is not currently available."""


@dataclass(frozen=True)
class SealedAccounting:
    response_id: str
    sealed_manifest_fingerprint: str
    calls: tuple[TerminalCallFrontier, ...]


@dataclass(frozen=True)
class RecoveredClosure:
    original_call_id: str
    obligation_id: str
    original_obligation_head: str
    terminal_disposition: Present
    outcome: Present
    accepted_evidence: Present
    closure: Present
    resolver_batch: Present
    resolver_id: str
    resolver_version: str
    reducer_id: str
    reducer_version: str
    reduction_id: str
    accepted_semantic_class: str


@dataclass(frozen=True)
class ContinuationReady:
    accounting: SealedAccounting
    recovered: tuple[tuple[RecoveredClosure, EvidenceReduction], ...]


def _readonly_lineage(acceptance: ReadOnlyAcceptedCall, call_id: str) -> None:
    lineage = acceptance.lineage
    members = acceptance.complete_ordered_attempts
    if (
        acceptance.lineage_id != lineage.lineage_id
        or lineage.original_call_id != call_id
        or acceptance.attempts_consumed != len(members)
        or len(members) > lineage.max_attempts
        or acceptance.ordinal != len(members) - 1
        or len({item.attempt_id for item in members}) != len(members)
    ):
        raise RecoveryIntegrityFault("read-only terminal lost original bounded lineage")
    previous: ReadOnlyAttemptMember | None = None
    for ordinal, member in enumerate(members):
        if member.ordinal != ordinal:
            raise RecoveryIntegrityFault("read-only attempt order or cardinality mismatch")
        if previous is None:
            if member.predecessor.kind != "ABSENT":
                raise RecoveryIntegrityFault("read-only genesis has a predecessor")
        elif member.predecessor != previous.accepted:
            raise RecoveryIntegrityFault("read-only predecessor is not exact prior accepted head")
        if member.outcome.kind != "PRESENT" or member.result_or_obligation.kind != "PRESENT":
            raise RecoveryHold("read-only terminal has an unaccounted attempt")
        previous = member
    if acceptance.accepted != members[-1].accepted:
        raise RecoveryIntegrityFault("read-only terminal selected a different accepted attempt")


def sealed_call_accounting(
    response_id: str,
    sealed_manifest_fingerprint: str,
    ordered_sealed_call_ids: tuple[str, ...],
    current_frontiers: tuple[CallRecoveryFrontier, ...],
) -> SealedAccounting:
    """Weak exhaustive join: an exact open recovery obligation is accountable."""
    if len(set(ordered_sealed_call_ids)) != len(ordered_sealed_call_ids):
        raise RecoveryIntegrityFault("sealed manifest contains duplicate logical call")
    if tuple(item.original_call_id for item in current_frontiers) != ordered_sealed_call_ids:
        raise RecoveryHold("frontier membership does not equal complete ordered sealed manifest")
    terminals: list[TerminalCallFrontier] = []
    for call in current_frontiers:
        if call.response_id != response_id:
            raise RecoveryIntegrityFault("call belongs to a different sealed response")
        if call.kind != "TERMINAL":
            raise RecoveryHold("pending call is not terminal-accountable")
        acceptance, disposition = call.acceptance, call.disposition
        if disposition.kind == "CANCELLED_BEFORE_ACCEPT":
            if (
                acceptance.kind != "INITIALIZED"
                or disposition.initialized_predecessor != acceptance.initialized
            ):
                raise RecoveryIntegrityFault("accepted call cannot be cancelled before acceptance")
        elif acceptance.kind == "INITIALIZED":
            raise RecoveryIntegrityFault("result has no registered acceptance branch")
        if acceptance.kind == "CONSEQUENTIAL_ACCEPTED":
            required = [acceptance.accepted, acceptance.execution_intent]
            if acceptance.external_effect_intent.kind == "PRESENT":
                required.append(acceptance.external_effect_intent)
            # This exact owner-local batch has one accepted head and its intents;
            # initialized is the predecessor, not a newly published batch member.
            if tuple(required) != acceptance.complete_acceptance_manifest:
                raise RecoveryIntegrityFault("acceptance batch missing, extra or reordered members")
        elif acceptance.kind == "READ_ONLY_ACCEPTED":
            _readonly_lineage(acceptance, call.original_call_id)
            if disposition.kind != "RECOVERY_REQUIRED":
                if disposition.kind == "CANCELLED_BEFORE_ACCEPT":
                    raise RecoveryIntegrityFault("read-only accepted call has pre-accept result")
                if disposition.result != acceptance.call_level_outcome:
                    raise RecoveryIntegrityFault(
                        "attempt result substituted for call-level outcome"
                    )
        if disposition.kind == "RECOVERY_REQUIRED":
            obligation, outcome = disposition.obligation, disposition.recovered_outcome
            if (
                obligation.original_call_id != call.original_call_id
                or outcome.original_call_id != call.original_call_id
                or outcome.recovery_obligation_id != obligation.obligation_id
            ):
                raise RecoveryIntegrityFault("recovery obligation/outcome identity mismatch")
        terminals.append(call)
    return SealedAccounting(response_id, sealed_manifest_fingerprint, tuple(terminals))


def model_continuation_ready(
    accounting: SealedAccounting,
    closures: tuple[RecoveredClosure, ...],
    current_reductions: tuple[EvidenceReduction, ...],
) -> ContinuationReady:
    """Strong join binds immutable closure/witness and the current reduction.

    Raw evidence append position is intentionally not a continuation credential.
    The writer must provide the exact current reduction, not an older consumable one.
    """
    closure_map = {item.original_call_id: item for item in closures}
    reduction_map = {item.reduction_id: item for item in current_reductions}
    if len(closure_map) != len(closures) or len(reduction_map) != len(current_reductions):
        raise RecoveryIntegrityFault("duplicate recovery closure or semantic reduction")
    required_calls = {
        call.original_call_id
        for call in accounting.calls
        if call.disposition.kind == "RECOVERY_REQUIRED"
    }
    if set(closure_map) != required_calls:
        raise RecoveryHold("recovered closure membership is incomplete or contains extra subjects")
    joined: list[tuple[RecoveredClosure, EvidenceReduction]] = []
    for call in accounting.calls:
        disposition = call.disposition
        if disposition.kind != "RECOVERY_REQUIRED":
            continue
        if disposition.recovered_outcome.outcome.kind != "PRESENT":
            raise RecoveryHold("terminal recovery obligation has no immutable recovered outcome")
        closure = closure_map[call.original_call_id]
        obligation = disposition.obligation
        reduction = reduction_map.get(closure.reduction_id)
        if reduction is None or reduction.consumability != "CONSUMABLE":
            raise RecoveryHold("current semantic reduction absent or non-consumable")
        if (
            closure.obligation_id != obligation.obligation_id
            or closure.original_obligation_head != obligation.obligation_head
            or closure.terminal_disposition != disposition.terminal
            or closure.outcome != disposition.recovered_outcome.outcome
            or (closure.resolver_id, closure.resolver_version)
            != (obligation.resolver_id, obligation.resolver_version)
            or (closure.reducer_id, closure.reducer_version)
            != (obligation.reducer_id, obligation.reducer_version)
            or (reduction.reducer_id, reduction.reducer_version)
            != (closure.reducer_id, closure.reducer_version)
            or reduction.stream_id != obligation.evidence_stream_id
            or reduction.accepted_witness != closure.accepted_evidence
            or reduction.accepted_outcome != closure.outcome
            or reduction.obligation_closure != closure.closure
            or reduction.resolver_batch != closure.resolver_batch
            or reduction.semantic_class != closure.accepted_semantic_class
            or reduction.accepted_witness not in reduction.ordered_consumed_evidence
            or len({item.head for item in reduction.ordered_consumed_evidence})
            != len(reduction.ordered_consumed_evidence)
        ):
            raise RecoveryHold("recovered pair, closure, witness or reducer binding differs")
        joined.append((closure, reduction))
    if set(reduction_map) != {closure.reduction_id for closure, _ in joined}:
        raise RecoveryHold("extra current semantic reduction")
    return ContinuationReady(accounting, tuple(joined))
