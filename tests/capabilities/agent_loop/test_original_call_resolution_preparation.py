"""Owner preparation retains one exact selected R16 witness without write authority."""

import pytest
from tests.support.capabilities.agent_loop.original_call_resolution_preparation import (
    _head,
    _sha,
    request,
)

from chiplog.capabilities.agent_loop.original_call_resolution_preparation import (
    prepare_original_call_resolution,
)
from chiplog.composition.r14_original_outcome_translation import RESULT_SCHEMA


def test_preparation_binds_selected_witness_and_exact_evidence() -> None:
    result = prepare_original_call_resolution(request())

    assert result.kind == "PREPARED_ORIGINAL_CALL_RESOLUTION_V1"
    assert result.basis.selected_witness.subject_id.startswith("effect-record/")
    expected_evidence = tuple(
        source.physical_record for source in request().cut.complete_ordered_evidence
    )
    assert result.basis.complete_evidence_manifest == expected_evidence
    assert result.recovered_outcome.result_schema == RESULT_SCHEMA
    assert result.batch.source_cut_fingerprint == request().cut.digest()
    assert result.batch.closure.revision.fingerprint == result.closure.digest()


@pytest.mark.parametrize(
    "mutation", ("missing_witness", "foreign_intent", "incomplete_evidence", "effects_reduction")
)
def test_preparation_holds_for_unproven_or_unsupported_cut(mutation: str) -> None:
    value = request()
    cut = value.cut
    if mutation == "missing_witness":
        cut = cut.model_copy(update={"selected_witness": cut.complete_ordered_evidence[0]})
    elif mutation == "foreign_intent":
        cut = cut.model_copy(
            update={
                "selected_witness": cut.selected_witness.model_copy(
                    update={
                        "canonical_record_bytes": b"{}",
                        "physical_record": _head("foreign", "record:" + _sha(b"{}"), _sha(b"{}")),
                        "subject": _head("foreign", "record:" + _sha(b"{}"), _sha(b"{}")),
                    }
                )
            }
        )
    elif mutation == "incomplete_evidence":
        cut = cut.model_copy(update={"complete_ordered_evidence": ()})
    else:
        cut = cut.model_copy(
            update={"complete_selected_effects_reductions": (cut.selected_witness,)}
        )
    rejected = prepare_original_call_resolution(value.model_copy(update={"cut": cut}))
    assert rejected.kind == "CALL_PREPARATION_REJECTED_V1"
