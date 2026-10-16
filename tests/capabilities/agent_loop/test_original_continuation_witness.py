"""Producer-to-continuation projection after cut validation, not source authority."""

from types import SimpleNamespace

import pytest
from tests.capabilities.agent_loop.test_original_call_resolution_preparation import _source
from tests.capabilities.agent_loop.test_original_semantic_reduction_preparation import request
from tests.capabilities.agent_loop.test_recovery_accounting import recovery_call

from chiplog.capabilities.agent_loop.execution_recovery_observations import OriginalRecoveredClosure
from chiplog.capabilities.agent_loop.execution_recovery_preparation import _continuation_closures
from chiplog.capabilities.agent_loop.original_recovery_contracts import (
    OriginalObligationClosureRecord,
    OriginalResolutionBasis,
    PreparedLoopSemanticReduction,
)
from chiplog.capabilities.agent_loop.original_semantic_reduction_preparation import (
    prepare_loop_semantic_reduction,
)
from chiplog.capabilities.agent_loop.recovery_domain import (
    RecoveryHold,
    RecoveryIntegrityFault,
    sealed_call_accounting,
)
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import EvidenceReduction


def _original_cut_accounting():
    source_request = request()
    prepared = prepare_loop_semantic_reduction(source_request)
    assert isinstance(prepared, PreparedLoopSemanticReduction)
    record = prepared.record
    anchors = {source.schema_id: source for source in source_request.complete_anchor_sources}
    closure = OriginalObligationClosureRecord.model_validate_json(
        anchors["chiplog.loop.original-obligation-closure.v1"].canonical_record_bytes
    )
    basis = OriginalResolutionBasis.model_validate_json(
        anchors["chiplog.loop.original-resolution-basis.v1"].canonical_record_bytes
    )
    assert record.anchor.kind == "RESOLVED"
    assert record.disposition.kind == "CONSUMABLE"
    assert record.anchor.accepted_witness == closure.accepted_witness
    assert closure.accepted_witness not in record.complete_ordered_evidence
    assert record.complete_ordered_evidence == tuple(
        item.physical_record for item in source_request.complete_ordered_evidence
    )
    reduced_source = _source(
        record,
        record_id=record.schema_id + ":" + record.digest(),
        owner="agent_loop",
        schema=record.schema_id,
    )
    summary = OriginalRecoveredClosure(
        original=closure.original,
        terminal_disposition=basis.original_terminal.revision,
        recovered_outcome=record.anchor.recovered_outcome.revision,
        accepted_evidence=closure.accepted_witness.revision,
        closure=record.anchor.closure.revision,
        resolver_batch=record.anchor.resolver_batch.revision,
        reduction_id=record.reduction_id,
        accepted_semantic_class=record.disposition.semantic_class,
    )
    reduction = EvidenceReduction(
        stream_id=record.stream.stream_id,
        reduction_id=record.reduction_id,
        reducer_id=record.reducer_id,
        reducer_version=record.reducer_version,
        current_reduction=reduced_source.subject.revision,
        ordered_consumed_evidence=tuple(item.revision for item in record.complete_ordered_evidence),
        accepted_witness=record.anchor.accepted_witness.revision,
        accepted_outcome=record.anchor.recovered_outcome.revision,
        obligation_closure=record.anchor.closure.revision,
        resolver_batch=record.anchor.resolver_batch.revision,
        semantic_class=record.disposition.semantic_class,
        consumability="CONSUMABLE",
    )
    call = recovery_call(recovered=True)
    call = call.model_copy(
        update={
            "original_call_id": closure.original.original_call_id,
            "response_id": "response-original",
            "disposition": call.disposition.model_copy(
                update={
                    "terminal": basis.original_terminal.revision,
                    "obligation": closure.original,
                    "recovered_outcome": call.disposition.recovered_outcome.model_copy(
                        update={
                            "original_call_id": closure.original.original_call_id,
                            "recovery_obligation_id": closure.original.obligation_id,
                            "outcome": record.anchor.recovered_outcome.revision,
                        }
                    ),
                }
            ),
        }
    )
    accounting = sealed_call_accounting(
        call.response_id, "f" * 64, (call.original_call_id,), (call,)
    )
    cut = SimpleNamespace(
        original_closures=(summary,),
        current_reductions=(reduction,),
        complete_sources=(
            *source_request.complete_anchor_sources,
            *source_request.complete_ordered_evidence,
            source_request.reducer_policy,
            reduced_source,
            source_request.authority.authenticated_invocation,
        ),
    )
    from chiplog.capabilities.agent_loop.original_recovery_contracts import (
        OriginalResolverInvocationV1,
    )

    invocation = OriginalResolverInvocationV1.model_validate_json(
        source_request.authority.authenticated_invocation.canonical_record_bytes
    )
    cut.complete_sources += (
        invocation.accepted_terminal_source,
        invocation.accepted_opening_source,
    )
    return cut, accounting


def test_original_resolver_witness_is_independent_of_consumed_append_evidence():
    cut, accounting = _original_cut_accounting()
    assert _continuation_closures(cut, accounting) == (
        cut.original_closures,
        cut.current_reductions,
    )


def test_native_body_aliases_keep_physical_carriers_separate():
    import hashlib

    from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
    from chiplog.capabilities.agent_loop.recovery_contracts import Present
    from chiplog.capabilities.effects.dispatch_outcome_contracts import DispatchOutcomeRecordV2

    cut, accounting = _original_cut_accounting()
    sources = []
    for source in cut.complete_sources:
        if source.owner != "effects":
            sources.append(source)
            continue
        record = DispatchOutcomeRecordV2.model_validate_json(source.canonical_record_bytes)
        digest = hashlib.sha256(source.canonical_record_bytes).hexdigest()
        physical = CallSubjectHead(
            subject_id=source.schema_id + ":" + digest,
            revision=Present(head="record:" + digest, fingerprint=digest),
        )
        body = CallSubjectHead(
            subject_id=record.record.subject_id,
            revision=Present(head=record.record.head, fingerprint=record.record.fingerprint),
        )
        sources.append(source.model_copy(update={"subject": body, "physical_record": physical}))
        if source.subject != body:
            sources.append(source.model_copy(update={"physical_record": physical}))
    cut.complete_sources = tuple(sources)
    assert _continuation_closures(cut, accounting) == (
        cut.original_closures,
        cut.current_reductions,
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "duplicate",
        "subject",
        "physical",
        "schema",
        "body",
        "order",
        "duplicate_evidence",
        "class",
        "projection",
        "extra_closure",
        "extra_reduction",
    ],
)
def test_original_join_rejects_inexact_sources_and_projections(mutation):
    cut, accounting = _original_cut_accounting()
    sources = list(cut.complete_sources)
    index = next(
        i
        for i, source in enumerate(sources)
        if source.schema_id == "chiplog.loop.original-obligation-closure.v1"
    )
    source = sources[index]
    if mutation == "missing":
        sources.pop(index)
    elif mutation == "duplicate":
        sources.append(source)
    elif mutation == "subject":
        sources[index] = source.model_copy(
            update={"subject": source.subject.model_copy(update={"subject_id": "another-subject"})}
        )
    elif mutation == "physical":
        sources[index] = source.model_copy(
            update={
                "physical_record": source.physical_record.model_copy(
                    update={"subject_id": "another-carrier"}
                )
            }
        )
    elif mutation == "schema":
        sources[index] = source.model_copy(update={"schema_id": "another-schema"})
    elif mutation == "body":
        sources[index] = source.model_copy(update={"canonical_record_bytes": b"{}"})
    elif mutation in ("order", "duplicate_evidence", "class", "projection"):
        reduction = cut.current_reductions[0]
        update = {
            "order": {
                "ordered_consumed_evidence": tuple(reversed(reduction.ordered_consumed_evidence))
            },
            "duplicate_evidence": {
                "ordered_consumed_evidence": reduction.ordered_consumed_evidence * 2
            },
            "class": {"semantic_class": "FOREIGN_CLASS"},
            "projection": {"reducer_version": "foreign"},
        }[mutation]
        cut.current_reductions = (reduction.model_copy(update=update),)
    elif mutation == "extra_closure":
        cut.original_closures *= 2
    elif mutation == "extra_reduction":
        cut.current_reductions *= 2
    cut.complete_sources = tuple(sources)
    with pytest.raises((RecoveryHold, RecoveryIntegrityFault, ValueError)):
        _continuation_closures(cut, accounting)


@pytest.mark.parametrize("mutation", ["prefix", "anchor_subject", "anchor_class", "stream"])
def test_original_join_rejects_self_consistent_reduction_projection(mutation):
    from chiplog.capabilities.agent_loop.original_recovery_contracts import (
        LoopSemanticReductionRecord,
    )

    cut, accounting = _original_cut_accounting()
    sources = list(cut.complete_sources)
    index = next(
        i for i, row in enumerate(sources) if row.schema_id == "chiplog.loop.semantic-reduction.v1"
    )
    record = LoopSemanticReductionRecord.model_validate_json(sources[index].canonical_record_bytes)
    changes = {
        "prefix": {"complete_ordered_evidence": tuple(reversed(record.complete_ordered_evidence))},
        "anchor_subject": {
            "anchor": record.anchor.model_copy(
                update={
                    "accepted_witness": record.anchor.accepted_witness.model_copy(
                        update={"subject_id": "foreign"}
                    )
                }
            )
        },
        "anchor_class": {
            "anchor": record.anchor.model_copy(update={"accepted_semantic_class": "FOREIGN"})
        },
        "stream": {"stream": record.stream.model_copy(update={"stream_id": "foreign"})},
    }[mutation]
    record = record.model_copy(update=changes)
    replacement = _source(
        record,
        record_id=record.schema_id + ":" + record.digest(),
        owner="agent_loop",
        schema=record.schema_id,
    )
    sources[index] = replacement
    cut.complete_sources = tuple(sources)
    reduction = cut.current_reductions[0]
    cut.current_reductions = (
        reduction.model_copy(
            update={
                "current_reduction": replacement.subject.revision,
                "ordered_consumed_evidence": tuple(
                    item.revision for item in record.complete_ordered_evidence
                ),
                "accepted_witness": record.anchor.accepted_witness.revision,
                "stream_id": record.stream.stream_id,
            }
        ),
    )
    with pytest.raises((RecoveryHold, RecoveryIntegrityFault, ValueError)):
        _continuation_closures(cut, accounting)


def test_original_join_rejects_terminal_for_another_intent():
    from chiplog.capabilities.agent_loop.execution_recovery_contracts import (
        AcceptedOpenTerminalDispositionV1,
    )

    cut, accounting = _original_cut_accounting()
    sources = list(cut.complete_sources)
    index = next(
        i
        for i, row in enumerate(sources)
        if row.schema_id == "chiplog.execution.accepted-open-terminal-disposition.v1"
    )
    row = sources[index]
    terminal = AcceptedOpenTerminalDispositionV1.model_validate_json(row.canonical_record_bytes)
    terminal = terminal.model_copy(
        update={
            "external_effect_intent": terminal.external_effect_intent.model_copy(
                update={"subject_id": "another-intent"}
            )
        }
    )
    # Keep the immutable reference: substituted bytes cannot pass that reference.
    sources[index] = row.model_copy(update={"canonical_record_bytes": terminal.canonical_bytes()})
    cut.complete_sources = tuple(sources)
    with pytest.raises((RecoveryHold, RecoveryIntegrityFault, ValueError)):
        _continuation_closures(cut, accounting)


def test_compatible_append_preserves_immutable_accepted_witness():
    from tests.capabilities.agent_loop.test_original_call_resolution_preparation import _head
    from tests.capabilities.effects.support.dispatch_outcomes import original_send
    from tests.capabilities.effects.test_dispatch_outcomes import request as outcome_request

    from chiplog.capabilities.agent_loop.execution_recovery_observations import RecoverySourceRecord
    from chiplog.capabilities.agent_loop.original_recovery_contracts import (
        LoopSemanticReductionRecord,
        SelectedLoopSemanticReduction,
    )
    from chiplog.capabilities.effects.dispatch_outcome_contracts import DispatchOutcomeRecordV2
    from chiplog.capabilities.effects.dispatch_outcomes import evidence_head, prepare_outcome

    cut, accounting = _original_cut_accounting()
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
    refined_source = _source(
        record,
        record_id=record.schema_id + ":" + record.digest(),
        owner="agent_loop",
        schema=record.schema_id,
    )
    cut.complete_sources = (
        *(row for row in cut.complete_sources if row != previous_source),
        refined_source,
        evidence_source,
    )
    cut.current_reductions = (
        cut.current_reductions[0].model_copy(
            update={
                "current_reduction": refined_source.subject.revision,
                "ordered_consumed_evidence": tuple(
                    item.revision for item in record.complete_ordered_evidence
                ),
            }
        ),
    )
    closures, reductions = _continuation_closures(cut, accounting)
    assert closures == cut.original_closures
    assert reductions == cut.current_reductions
    assert closures[0].accepted_evidence == previous.anchor.accepted_witness.revision
