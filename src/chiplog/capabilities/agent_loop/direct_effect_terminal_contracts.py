"""Pure R16-to-R14 direct terminal classification owned by the loop boundary."""

from typing import Literal

from pydantic import Field

from chiplog.capabilities.effects.contracts import DispatchSemanticBinding, ExactHead
from chiplog.capabilities.effects.dispatch_outcome_contracts import DispatchOutcomeRecordV2
from chiplog.capabilities.effects.dispatch_outcomes import evidence_head
from chiplog.capabilities.effects.dispatch_v2 import reference

from .execution_recovery_observations import ExecutionRecoveryDTO

CLASSIFIER_ID = "R14_R16_V2_OUTCOME_TRANSLATION"
CLASSIFIER_VERSION = "r14-r16-outcome-translation.v1"
MAPPING_ID = "R14_DIRECT_EFFECT_TERMINAL_V1"
MAPPING_VERSION = "r14-direct-effect-terminal.v1"
RESULT_SCHEMA = "chiplog.loop.recovered-effect-status.v1"


class RecoveredEffectStatusV1(ExecutionRecoveryDTO):
    """Inert status fact tied to the exact R16 intent, outcome and binding."""

    schema_id: Literal["chiplog.loop.recovered-effect-status.v1"] = (
        "chiplog.loop.recovered-effect-status.v1"
    )
    policy_id: Literal["R14_R16_V2_OUTCOME_TRANSLATION"] = "R14_R16_V2_OUTCOME_TRANSLATION"
    policy_version: Literal["r14-r16-outcome-translation.v1"] = "r14-r16-outcome-translation.v1"
    semantic_binding: DispatchSemanticBinding
    semantic_class: Literal["R14_EFFECT_CONFIRMED", "R14_EFFECT_FAILED_NO_EFFECT"]
    intent: ExactHead
    terminal: ExactHead
    ordered_evidence: tuple[ExactHead, ...] = Field(min_length=1)


class DirectEffectTerminalClassificationV1(ExecutionRecoveryDTO):
    """Inert R14 disposition classification bound to the complete R16 lineage."""

    schema_id: Literal["chiplog.loop.direct-effect-terminal-classification.v1"] = (
        "chiplog.loop.direct-effect-terminal-classification.v1"
    )
    mapping_id: Literal["R14_DIRECT_EFFECT_TERMINAL_V1"] = "R14_DIRECT_EFFECT_TERMINAL_V1"
    mapping_version: Literal["r14-direct-effect-terminal.v1"] = "r14-direct-effect-terminal.v1"
    classifier_id: Literal["R14_R16_V2_OUTCOME_TRANSLATION"] = "R14_R16_V2_OUTCOME_TRANSLATION"
    classifier_version: Literal["r14-r16-outcome-translation.v1"] = "r14-r16-outcome-translation.v1"
    semantic_binding: DispatchSemanticBinding
    semantic_class: Literal["R14_EFFECT_CONFIRMED", "R14_EFFECT_FAILED_NO_EFFECT"]
    disposition: Literal["SUCCEEDED", "FAILED_DEFINITE"]
    intent: ExactHead
    terminal: ExactHead
    ordered_evidence: tuple[ExactHead, ...]


def translate_selected_outcome_v1(
    outcome: DispatchOutcomeRecordV2,
) -> RecoveredEffectStatusV1:
    """Translate only exhaustive, non-conflicting terminal child evidence."""
    command, snapshot = outcome.command, outcome.snapshot
    children = tuple(item.subject_id for item in snapshot.intent.mandate.bundle_members)
    transmissions = tuple(item.transmission for item in snapshot.transmissions)
    evidence_ids = tuple(item.evidence_id for item in snapshot.evidence)
    intent_ref = reference(snapshot.intent.intent_id, snapshot.intent.canonical_bytes())
    if (
        command.operation != "RESOLVE_OBLIGATION"
        or command.evidence is not None
        or command.resolver is None
        or command.intent != intent_ref
        or snapshot.obligation.original_intent != intent_ref
        or command.original_send != snapshot.obligation.original_send
        or not children
        or len(children) != len(set(children))
        or len(evidence_ids) != len(set(evidence_ids))
        or tuple(command.complete_children) != transmissions
        or tuple(command.prior_evidence) != tuple(snapshot.evidence_heads)
        or tuple(snapshot.evidence_heads)
        != tuple(evidence_head(item) for item in snapshot.evidence)
        or tuple(snapshot.obligation.original_children) != tuple(command.complete_children)
        or snapshot.obligation.state != "CLOSED"
        or tuple(snapshot.obligation.closure_evidence) != tuple(snapshot.evidence_heads)
        or snapshot.conflicting_evidence
    ):
        raise ValueError("R16 outcome is not a complete, closed resolver snapshot")

    occurred: list[str] = []
    incapable: list[str] = []
    for item in snapshot.evidence:
        positive = tuple(item.occurred_members)
        negative = tuple(item.permanently_incapable_members)
        if (
            item.transmission not in transmissions
            or len(positive) != len(set(positive))
            or len(negative) != len(set(negative))
            or set(positive) & set(negative)
            or not (set(positive) | set(negative)) <= set(children)
        ):
            raise ValueError("R16 evidence has malformed or foreign child coverage")
        occurred.extend(positive)
        incapable.extend(negative)
    if (
        len(occurred) != len(set(occurred))
        or len(incapable) != len(set(incapable))
        or set(occurred) & set(incapable)
        or set(occurred) | set(incapable) != set(children)
    ):
        raise ValueError("R16 evidence does not partition the complete original child set")

    semantic_class: Literal["R14_EFFECT_CONFIRMED", "R14_EFFECT_FAILED_NO_EFFECT"]
    if snapshot.state == "CONFIRMED" and not incapable:
        semantic_class = "R14_EFFECT_CONFIRMED"
    elif snapshot.state == "FAILED_NO_EFFECT" and not occurred:
        semantic_class = "R14_EFFECT_FAILED_NO_EFFECT"
    else:
        raise ValueError("R16 outcome class is not supported by the R14 translation policy")

    return RecoveredEffectStatusV1(
        semantic_binding=snapshot.intent.mandate.semantics,
        semantic_class=semantic_class,
        intent=command.intent,
        terminal=outcome.record,
        ordered_evidence=tuple(snapshot.evidence_heads),
    )


def classify_direct_effect_terminal_v1(
    status: RecoveredEffectStatusV1,
) -> DirectEffectTerminalClassificationV1:
    """Map only exact closed R16 semantic classes; grants no writer authority."""
    if type(status) is not RecoveredEffectStatusV1:
        raise TypeError("direct R14 mapping requires the exact closed R16 status DTO")
    if (
        status.policy_id != CLASSIFIER_ID
        or status.policy_version != CLASSIFIER_VERSION
        or not status.ordered_evidence
    ):
        raise ValueError("R16 status is missing the registered classifier lineage")
    if status.semantic_class == "R14_EFFECT_CONFIRMED":
        disposition: Literal["SUCCEEDED", "FAILED_DEFINITE"] = "SUCCEEDED"
    elif status.semantic_class == "R14_EFFECT_FAILED_NO_EFFECT":
        disposition = "FAILED_DEFINITE"
    else:
        raise ValueError("R16 status class is outside direct R14 mapping v1")
    return DirectEffectTerminalClassificationV1(
        semantic_class=status.semantic_class,
        disposition=disposition,
        semantic_binding=status.semantic_binding,
        intent=status.intent,
        terminal=status.terminal,
        ordered_evidence=status.ordered_evidence,
    )
