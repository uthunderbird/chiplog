"""R14's versioned translation accepts only exhaustive closed R16 evidence."""

import pytest
from tests.capabilities.effects.support.dispatch_outcomes import original_send
from tests.support.capabilities.effects.dispatch_outcomes import request

from chiplog.capabilities.effects.dispatch_outcome_contracts import (
    DispatchOutcomeRecordV2,
)
from chiplog.capabilities.effects.dispatch_outcomes import prepare_outcome
from chiplog.capabilities.effects.dispatch_v2 import reference
from chiplog.composition.r14_original_outcome_translation import (
    translate_selected_outcome_v1,
)


def closed_outcome(*, negative: bool = False) -> DispatchOutcomeRecordV2:
    send = original_send()
    unknown = prepare_outcome(request(send, send))
    evidence = prepare_outcome(
        request(
            unknown,
            send,
            negative=("subject",) if negative else (),
            positive=() if negative else ("subject",),
            name="complete-evidence",
        )
    )
    return prepare_outcome(request(evidence, send, close=True))


@pytest.mark.parametrize(
    ("negative", "expected"),
    ((False, "R14_EFFECT_CONFIRMED"), (True, "R14_EFFECT_FAILED_NO_EFFECT")),
)
def test_translation_returns_inert_typed_status_with_exact_lineage(
    negative: bool, expected: str
) -> None:
    outcome = closed_outcome(negative=negative)

    translated = translate_selected_outcome_v1(outcome)

    assert translated.semantic_class == expected
    assert translated.intent == outcome.command.intent
    assert translated.terminal == outcome.record
    assert translated.ordered_evidence == outcome.snapshot.evidence_heads
    assert translated.model_validate_json(translated.canonical_bytes()) == translated


@pytest.mark.parametrize("mutation", ("open", "conflict", "partial", "foreign"))
def test_translation_holds_for_nonterminal_conflicting_or_incomplete_history(
    mutation: str,
) -> None:
    outcome = closed_outcome()
    snapshot = outcome.snapshot
    if mutation == "open":
        changed = snapshot.model_copy(
            update={"obligation": snapshot.obligation.model_copy(update={"state": "OPEN"})}
        )
    elif mutation == "conflict":
        changed = snapshot.model_copy(update={"conflicting_evidence": True})
    elif mutation == "partial":
        changed = snapshot.model_copy(update={"state": "PARTIAL_CONFIRMED"})
    else:
        child = outcome.command.complete_children[0].model_copy(
            update={"subject_id": "foreign-child"}
        )
        changed = snapshot.model_copy(
            update={
                "evidence": (
                    snapshot.evidence[0].model_copy(
                        update={"occurred_members": ("foreign-child",)}
                    ),
                ),
                "obligation": snapshot.obligation.model_copy(
                    update={"original_children": (child,)}
                ),
            }
        )
    malformed = outcome.model_copy(update={"snapshot": changed})

    with pytest.raises(ValueError):
        translate_selected_outcome_v1(malformed)


def test_translation_holds_when_command_labels_another_intent() -> None:
    outcome = closed_outcome()
    substituted = reference("other-intent", b"other-intent")
    malformed = outcome.model_copy(
        update={"command": outcome.command.model_copy(update={"intent": substituted})}
    )

    with pytest.raises(ValueError):
        translate_selected_outcome_v1(malformed)


def test_translation_holds_when_evidence_identity_is_repeated() -> None:
    outcome = closed_outcome()
    evidence = outcome.snapshot.evidence[0]
    duplicate_snapshot = outcome.snapshot.model_copy(
        update={
            "evidence": (evidence, evidence),
            "evidence_heads": (outcome.snapshot.evidence_heads[0],) * 2,
            "obligation": outcome.snapshot.obligation.model_copy(
                update={
                    "closure_evidence": (outcome.snapshot.evidence_heads[0],) * 2,
                }
            ),
        }
    )
    malformed = outcome.model_copy(
        update={
            "command": outcome.command.model_copy(
                update={"prior_evidence": (outcome.snapshot.evidence_heads[0],) * 2}
            ),
            "snapshot": duplicate_snapshot,
        }
    )

    with pytest.raises(ValueError):
        translate_selected_outcome_v1(malformed)
