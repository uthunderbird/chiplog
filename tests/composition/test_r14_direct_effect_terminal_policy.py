"""The v1 R14 direct mapping is exact, versioned, and inert."""

from typing import cast

import pytest

from chiplog.capabilities.effects.dispatch_outcomes import prepare_outcome
from chiplog.composition.r14_direct_effect_terminal_policy import (
    CLASSIFIER_ID,
    CLASSIFIER_VERSION,
    MAPPING_ID,
    MAPPING_VERSION,
    classify_direct_effect_terminal_v1,
)
from chiplog.composition.r14_original_outcome_translation import (
    RecoveredEffectStatusV1,
    translate_selected_outcome_v1,
)
from tests.capabilities.effects.support.dispatch_outcomes import original_send
from tests.capabilities.effects.test_dispatch_outcomes import request


def closed_status(*, no_effect: bool) -> RecoveredEffectStatusV1:
    send = original_send()
    unknown = prepare_outcome(request(send, send))
    evidence = prepare_outcome(
        request(
            unknown,
            send,
            negative=("subject",) if no_effect else (),
            positive=() if no_effect else ("subject",),
            name="complete-evidence",
        )
    )
    return translate_selected_outcome_v1(prepare_outcome(request(evidence, send, close=True)))


@pytest.mark.parametrize(
    ("no_effect", "semantic_class", "disposition"),
    (
        (False, "R14_EFFECT_CONFIRMED", "SUCCEEDED"),
        (True, "R14_EFFECT_FAILED_NO_EFFECT", "FAILED_DEFINITE"),
    ),
)
def test_v1_maps_only_exhaustive_closed_outcomes_and_retains_exact_lineage(
    no_effect: bool, semantic_class: str, disposition: str
) -> None:
    status = closed_status(no_effect=no_effect)

    mapped = classify_direct_effect_terminal_v1(status)

    assert mapped.mapping_id == MAPPING_ID == "R14_DIRECT_EFFECT_TERMINAL_V1"
    assert mapped.mapping_version == MAPPING_VERSION == "r14-direct-effect-terminal.v1"
    assert mapped.classifier_id == CLASSIFIER_ID
    assert mapped.classifier_version == CLASSIFIER_VERSION
    assert mapped.semantic_binding == status.semantic_binding
    assert mapped.semantic_class == semantic_class
    assert mapped.disposition == disposition
    assert mapped.intent == status.intent
    assert mapped.terminal == status.terminal
    assert mapped.ordered_evidence == status.ordered_evidence
    assert mapped.model_validate_json(mapped.canonical_bytes()) == mapped


def test_v1_rejects_unknown_semantic_class_and_unregistered_classifier() -> None:
    status = closed_status(no_effect=False)

    with pytest.raises(ValueError):
        classify_direct_effect_terminal_v1(
            status.model_copy(update={"semantic_class": "PARTIAL_CONFIRMED"})
        )
    with pytest.raises(ValueError):
        classify_direct_effect_terminal_v1(
            status.model_copy(update={"policy_version": "future-version"})
        )


def test_v1_rejects_non_exact_status_and_empty_evidence_lineage() -> None:
    status = closed_status(no_effect=False)

    with pytest.raises(TypeError):
        classify_direct_effect_terminal_v1(cast(RecoveredEffectStatusV1, status.model_dump()))
    with pytest.raises(ValueError):
        classify_direct_effect_terminal_v1(status.model_copy(update={"ordered_evidence": ()}))
