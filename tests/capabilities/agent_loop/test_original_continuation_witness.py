"""Semantic reduction retains the accepted witness after compatible evidence."""

from tests.support.capabilities.agent_loop.original_continuation_witness import (
    _original_cut_accounting,
)
from tests.support.capabilities.agent_loop.original_semantic_reduction_preparation import request

from chiplog.capabilities.agent_loop.original_recovery_contracts import (
    PreparedLoopSemanticReduction,
)
from chiplog.capabilities.agent_loop.original_semantic_reduction_preparation import (
    prepare_loop_semantic_reduction,
)


def test_compatible_append_preserves_immutable_accepted_witness() -> None:
    from tests.capabilities.effects.support.dispatch_outcomes import original_send
    from tests.support.capabilities.agent_loop.original_call_resolution_preparation import _head
    from tests.support.capabilities.effects.dispatch_outcomes import request as outcome_request

    from chiplog.capabilities.agent_loop.execution_recovery_observations import RecoverySourceRecord
    from chiplog.capabilities.agent_loop.original_recovery_contracts import (
        LoopSemanticReductionRecord,
        SelectedLoopSemanticReduction,
    )
    from chiplog.capabilities.effects.dispatch_outcome_contracts import DispatchOutcomeRecordV2
    from chiplog.capabilities.effects.dispatch_outcomes import evidence_head, prepare_outcome

    cut, _accounting = _original_cut_accounting()
    source_request = request()
    previous_source = next(
        row for row in cut.complete_sources if row.schema_id == "chiplog.loop.semantic-reduction.v1"
    )
    previous = LoopSemanticReductionRecord.model_validate_json(
        previous_source.canonical_record_bytes
    )
    accepted_source = source_request.complete_anchor_sources[-1]
    accepted = DispatchOutcomeRecordV2.model_validate_json(accepted_source.canonical_record_bytes)
    late = prepare_outcome(outcome_request(accepted, original_send(), name="late-neutral"))
    evidence = late.command.evidence
    assert evidence is not None
    child = evidence_head(evidence)
    carrier = _head(late.record.subject_id, late.record.head, late.record.fingerprint)
    evidence_source = RecoverySourceRecord(
        owner="effects",
        subject=_head(child.subject_id, child.head, child.fingerprint),
        schema_id=late.schema_id,
        canonical_record_bytes=late.canonical_bytes(),
        selected_decision=_head("selected:late-neutral", "decision", "a" * 64),
        physical_record=carrier,
    )
    current_witness_source = evidence_source.model_copy(update={"subject": carrier})
    refined = prepare_loop_semantic_reduction(
        source_request.model_copy(
            update={
                "expected": SelectedLoopSemanticReduction(record=previous, source=previous_source),
                "complete_ordered_evidence": (
                    *source_request.complete_ordered_evidence,
                    evidence_source,
                ),
                "complete_anchor_sources": (
                    *source_request.complete_anchor_sources[:-1],
                    current_witness_source,
                ),
            }
        )
    )
    assert isinstance(refined, PreparedLoopSemanticReduction)
    record = refined.record
    assert record.disposition.kind == "CONSUMABLE"
    assert record.anchor.accepted_witness == previous.anchor.accepted_witness
    assert record.anchor.accepted_witness != current_witness_source.subject
    assert record.complete_ordered_evidence == (*previous.complete_ordered_evidence, carrier)
