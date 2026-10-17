"""Producer-to-continuation projection after cut validation, not source authority."""

import pytest
from tests.support.capabilities.agent_loop.original_call_resolution_preparation import _source
from tests.support.capabilities.agent_loop.original_continuation_witness import (
    _original_cut_accounting,
)
from tests.support.capabilities.agent_loop.original_semantic_reduction_preparation import request

from chiplog.capabilities.agent_loop.execution_recovery_preparation import _continuation_closures
from chiplog.capabilities.agent_loop.original_recovery_contracts import (
    PreparedLoopSemanticReduction,
)
from chiplog.capabilities.agent_loop.original_semantic_reduction_preparation import (
    prepare_loop_semantic_reduction,
)
from chiplog.capabilities.agent_loop.recovery_domain import (
    RecoveryHold,
    RecoveryIntegrityFault,
)


def test_original_resolver_witness_is_independent_of_consumed_append_evidence() -> None:
    cut, accounting = _original_cut_accounting()
    assert _continuation_closures(cut, accounting) == (
        cut.original_closures,
        cut.current_reductions,
    )


def _native_cut_accounting():
    from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
    from chiplog.capabilities.agent_loop.recovery_contracts import Present
    from chiplog.capabilities.effects.dispatch_outcome_contracts import DispatchOutcomeRecordV2
    from chiplog.composition.r16_dispatch_outcomes import _row

    cut, accounting = _original_cut_accounting()
    sources = []
    for source in cut.complete_sources:
        if source.owner != "effects":
            sources.append(source)
            continue
        record = DispatchOutcomeRecordV2.model_validate_json(source.canonical_record_bytes)
        physical_row = _row(record)
        assert physical_row.canonical_bytes == source.canonical_record_bytes
        physical = CallSubjectHead(
            subject_id=physical_row.record_id,
            revision=Present(
                head="record:" + physical_row.fingerprint,
                fingerprint=physical_row.fingerprint,
            ),
        )
        assert physical.revision.fingerprint != record.record.fingerprint
        body = CallSubjectHead(
            subject_id=record.record.subject_id,
            revision=Present(head=record.record.head, fingerprint=record.record.fingerprint),
        )
        sources.append(source.model_copy(update={"subject": body, "physical_record": physical}))
        if source.subject != body:
            sources.append(source.model_copy(update={"physical_record": physical}))
    cut.complete_sources = tuple(sources)
    return cut, accounting


def test_native_body_aliases_keep_physical_carriers_separate() -> None:
    cut, accounting = _native_cut_accounting()
    assert _continuation_closures(cut, accounting) == (
        cut.original_closures,
        cut.current_reductions,
    )


@pytest.mark.parametrize("mutation", ("schema-digest-id", "foreign-id", "fingerprint"))
def test_native_effect_carrier_must_match_actual_writer_projection(mutation: str) -> None:
    cut, accounting = _native_cut_accounting()
    sources = list(cut.complete_sources)
    index = next(i for i, row in enumerate(sources) if row.owner == "effects")
    source = sources[index]
    physical = source.physical_record
    if mutation == "fingerprint":
        physical = physical.model_copy(
            update={"revision": physical.revision.model_copy(update={"fingerprint": "0" * 64})}
        )
    else:
        physical = physical.model_copy(
            update={
                "subject_id": (
                    source.schema_id + ":" + physical.revision.fingerprint
                    if mutation == "schema-digest-id"
                    else "foreign-carrier"
                )
            }
        )
    sources[index] = source.model_copy(update={"physical_record": physical})
    cut.complete_sources = tuple(sources)
    with pytest.raises(RecoveryIntegrityFault, match="original effects carrier differs"):
        _continuation_closures(cut, accounting)


def _cut_with_opening_terminal_aliases():
    cut, accounting = _native_cut_accounting()
    aliases = tuple(
        source.model_copy(update={"subject": source.physical_record})
        for source in cut.complete_sources
        if source.schema_id
        in (
            "chiplog.execution.accepted-open-terminal-disposition.v1",
            "chiplog.execution.accepted-open-recovery-opening.v1",
        )
    )
    assert len(aliases) == 2
    assert all(alias not in cut.complete_sources for alias in aliases)
    cut.complete_sources += aliases
    return cut, accounting


def test_native_opening_terminal_physical_aliases_are_not_duplicate_logical_sources() -> None:
    cut, accounting = _cut_with_opening_terminal_aliases()
    assert _continuation_closures(cut, accounting) == (
        cut.original_closures,
        cut.current_reductions,
    )


@pytest.mark.parametrize("family", ("terminal-disposition", "recovery-opening"))
@pytest.mark.parametrize("mutation", ("duplicate", "subject", "body"))
def test_native_logical_opening_terminal_source_remains_exact(family: str, mutation: str) -> None:
    cut, accounting = _cut_with_opening_terminal_aliases()
    sources = list(cut.complete_sources)
    index = next(
        i
        for i, row in enumerate(sources)
        if family in row.schema_id and row.subject != row.physical_record
    )
    source = sources[index]
    if mutation == "duplicate":
        sources.append(source)
    elif mutation == "subject":
        sources[index] = source.model_copy(
            update={"subject": source.subject.model_copy(update={"subject_id": "foreign"})}
        )
    else:
        sources[index] = source.model_copy(update={"canonical_record_bytes": b"{}"})
    cut.complete_sources = tuple(sources)
    message = (
        "invalid recovery source bytes"
        if mutation == "body"
        else "original immutable terminal or opening source missing or duplicate"
    )
    with pytest.raises((RecoveryHold, RecoveryIntegrityFault), match=message):
        _continuation_closures(cut, accounting)


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
def test_original_join_rejects_inexact_sources_and_projections(mutation: str) -> None:
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
def test_original_join_rejects_self_consistent_reduction_projection(mutation: str) -> None:
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


def test_original_join_rejects_terminal_for_another_intent() -> None:
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
