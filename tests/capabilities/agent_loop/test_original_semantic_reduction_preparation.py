"""The semantic reduction owner proposes exact current evidence and resolver anchors."""

from __future__ import annotations

from tests.capabilities.agent_loop.test_original_call_resolution_preparation import (
    _head,
    _source,
)
from tests.capabilities.agent_loop.test_original_call_resolution_preparation import (
    request as resolution_request,
)

from chiplog.capabilities.agent_loop.original_call_resolution_preparation import (
    prepare_original_call_resolution,
)
from chiplog.capabilities.agent_loop.original_recovery_contracts import (
    LoopRecoveryStream,
    PreparedLoopSemanticReduction,
    PrepareLoopSemanticReduction,
    SelectedLoopSemanticReduction,
)
from chiplog.capabilities.agent_loop.original_semantic_reduction_preparation import (
    prepare_loop_semantic_reduction,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Absent


def request() -> PrepareLoopSemanticReduction:
    source_request = resolution_request()
    prepared = prepare_original_call_resolution(source_request)
    assert prepared.kind == "PREPARED_ORIGINAL_CALL_RESOLUTION_V1"
    cut = source_request.cut
    records = (
        (
            prepared.basis,
            "chiplog.loop.original-resolution-basis.v1",
            prepared.basis.basis_id,
        ),
        (
            prepared.closure,
            prepared.closure.schema_id,
            prepared.closure.closure_id,
        ),
        (
            prepared.recovered_outcome,
            prepared.recovered_outcome.schema_id,
            prepared.recovered_outcome.outcome_id,
        ),
        (
            prepared.batch,
            "chiplog.loop.original-resolver-batch.v1",
            prepared.batch.batch_id,
        ),
    )
    anchors = (
        *(
            _source(record, record_id=record_id, owner="agent_loop", schema=schema)
            for record, schema, record_id in records
        ),
        cut.selected_witness,
    )
    binding = cut.original
    return PrepareLoopSemanticReduction(
        command_id="reduce-original-semantic-evidence",
        tenant_id=cut.tenant_id,
        database_id=cut.database_id,
        tenant_commit_sequence=cut.tenant_commit_sequence + 1,
        materialization_commitment="a" * 64,
        stream=LoopRecoveryStream(
            stream_id=binding.evidence_stream_id,
            registry=cut.obligation_stream.registry,
            subject=cut.current_obligation.subject,
            schema_id="chiplog.loop.semantic-reduction.v1",
        ),
        expected=Absent(),
        original=binding,
        complete_ordered_evidence=cut.complete_ordered_evidence,
        complete_anchor_sources=anchors,
        reducer_policy=cut.reducer_policy,
        authority=cut.authority,
    )


def test_preparation_builds_consumable_resolved_reduction() -> None:
    source = request()
    prepared = prepare_loop_semantic_reduction(source)

    assert isinstance(prepared, PreparedLoopSemanticReduction)
    assert prepared.record.predecessor == Absent()
    assert prepared.record.reduction_id == "r14-original-semantic-reduction:obligation-original"
    assert prepared.record.disposition.kind == "CONSUMABLE"
    assert prepared.record.anchor.kind == "RESOLVED"
    assert prepared.source_request_fingerprint == source.digest()


def test_preparation_holds_on_missing_evidence() -> None:
    source = request()
    rejected = prepare_loop_semantic_reduction(
        source.model_copy(update={"complete_ordered_evidence": ()})
    )

    assert rejected.kind == "CALL_PREPARATION_REJECTED_V1"
    assert rejected.code == "HOLD"


def test_preparation_holds_on_foreign_stream() -> None:
    source = request()
    foreign_stream = source.stream.model_copy(update={"stream_id": "foreign"})
    rejected = prepare_loop_semantic_reduction(source.model_copy(update={"stream": foreign_stream}))

    assert rejected.kind == "CALL_PREPARATION_REJECTED_V1"
    assert rejected.code == "HOLD"


def test_contradictory_selected_evidence_builds_typed_hold() -> None:
    source = request()
    witness_source = source.complete_anchor_sources[-1]
    from chiplog.capabilities.effects.dispatch_outcome_contracts import DispatchOutcomeRecordV2

    witness = DispatchOutcomeRecordV2.model_validate_json(witness_source.canonical_record_bytes)
    contradictory_snapshot = witness.snapshot.model_copy(update={"conflicting_evidence": True})
    contradictory = witness.model_copy(update={"snapshot": contradictory_snapshot})
    changed_witness_source = witness_source.model_copy(
        update={"canonical_record_bytes": contradictory.canonical_bytes()}
    )
    sources = (*source.complete_anchor_sources[:-1], changed_witness_source)
    prepared = prepare_loop_semantic_reduction(
        source.model_copy(update={"complete_anchor_sources": sources})
    )

    assert isinstance(prepared, PreparedLoopSemanticReduction)
    assert prepared.record.disposition.kind == "HOLD"
    assert prepared.record.disposition.reason == "CONTRADICTORY"


def test_compatible_refinement_advances_exact_selected_predecessor() -> None:
    source = request()
    first = prepare_loop_semantic_reduction(source)
    assert isinstance(first, PreparedLoopSemanticReduction)
    first_raw = first.record.canonical_bytes()
    import hashlib

    first_digest = hashlib.sha256(first_raw).hexdigest()
    selected_source = _source(
        first.record,
        record_id="chiplog.loop.semantic-reduction.v1:" + first_digest,
        owner="agent_loop",
        schema="chiplog.loop.semantic-reduction.v1",
        subject=_head(source.stream.stream_id, "record:" + first_digest, first_digest),
    )
    expected = SelectedLoopSemanticReduction(record=first.record, source=selected_source)
    refined = prepare_loop_semantic_reduction(
        source.model_copy(update={"expected": expected, "command_id": "refine-reduction"})
    )

    assert isinstance(refined, PreparedLoopSemanticReduction)
    assert refined.record.predecessor.head == "record:" + first_digest
    assert refined.record.reduction_id == first.record.reduction_id
    assert refined.record.disposition == first.record.disposition
