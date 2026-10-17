"""The semantic reduction owner proposes exact current evidence and resolver anchors."""

from __future__ import annotations

from tests.capabilities.effects.support.dispatch_outcomes import original_send
from tests.support.capabilities.agent_loop.original_call_resolution_preparation import (
    _head,
    _source,
)
from tests.support.capabilities.agent_loop.original_semantic_reduction_preparation import (
    request,
)
from tests.support.capabilities.effects.dispatch_outcomes import request as outcome_request

from chiplog.capabilities.agent_loop.execution_recovery_observations import RecoverySourceRecord
from chiplog.capabilities.agent_loop.original_recovery_contracts import (
    PreparedLoopSemanticReduction,
    SelectedLoopSemanticReduction,
)
from chiplog.capabilities.agent_loop.original_semantic_reduction_preparation import (
    prepare_loop_semantic_reduction,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Absent
from chiplog.capabilities.effects.dispatch_outcomes import evidence_head, prepare_outcome


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


def test_owner_process_dispatches_semantic_reduction_operation() -> None:
    import base64

    from chiplog.capabilities.agent_loop._original_recovery_process import (
        PREPARE_SEMANTIC_REDUCTION_OPERATION,
        dispatch,
    )
    from chiplog.capabilities.agent_loop.recovery_wire_contracts import (
        ORIGINAL_RECOVERY_RESULT_SCHEMA,
        decode_original_recovery_result,
    )

    source = request()
    response = dispatch(PREPARE_SEMANTIC_REDUCTION_OPERATION, source.canonical_bytes())

    assert response["schema_id"] == ORIGINAL_RECOVERY_RESULT_SCHEMA
    prepared = decode_original_recovery_result(
        ORIGINAL_RECOVERY_RESULT_SCHEMA,
        base64.b64decode(response["payload"]),
    ).value
    assert prepared == prepare_loop_semantic_reduction(source)


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


def test_late_compatible_and_rival_evidence_prepare_exact_reduction_successors() -> None:
    source = request()
    first = prepare_loop_semantic_reduction(source)
    assert isinstance(first, PreparedLoopSemanticReduction)
    first_raw = first.record.canonical_bytes()
    import hashlib

    first_digest = hashlib.sha256(first_raw).hexdigest()
    first_source = _source(
        first.record,
        record_id="chiplog.loop.semantic-reduction.v1:" + first_digest,
        owner="agent_loop",
        schema="chiplog.loop.semantic-reduction.v1",
        subject=_head(source.stream.stream_id, "record:" + first_digest, first_digest),
    )
    expected = SelectedLoopSemanticReduction(record=first.record, source=first_source)

    send = original_send()
    opening = prepare_outcome(outcome_request(send, send))
    initial = prepare_outcome(outcome_request(opening, send, name="receipt-initial"))
    confirmed = prepare_outcome(
        outcome_request(initial, send, positive=("subject",), name="confirm")
    )
    closed = prepare_outcome(outcome_request(confirmed, send, close=True))

    def prepare_late(name: str, *, negative: tuple[str, ...] = ()):
        late = prepare_outcome(outcome_request(closed, send, negative=negative, name=name))
        evidence = late.command.evidence
        assert evidence is not None
        evidence_ref = evidence_head(evidence)
        carrier = _head(late.record.subject_id, late.record.head, late.record.fingerprint)
        evidence_source = RecoverySourceRecord(
            owner="effects",
            subject=_head(evidence_ref.subject_id, evidence_ref.head, evidence_ref.fingerprint),
            schema_id=late.schema_id,
            canonical_record_bytes=late.canonical_bytes(),
            selected_decision=_head("selected:late:" + name, "decision", "a" * 64),
            physical_record=carrier,
        )
        witness_source = RecoverySourceRecord(
            owner="effects",
            subject=carrier,
            schema_id=late.schema_id,
            canonical_record_bytes=late.canonical_bytes(),
            selected_decision=_head("selected:witness:late:" + name, "decision", "b" * 64),
            physical_record=carrier,
        )
        revised = source.model_copy(
            update={
                "command_id": "refine:" + name,
                "expected": expected,
                "complete_ordered_evidence": (*source.complete_ordered_evidence, evidence_source),
                "complete_anchor_sources": (
                    *source.complete_anchor_sources[:-1],
                    witness_source,
                ),
            }
        )
        return prepare_loop_semantic_reduction(revised), carrier

    compatible, compatible_carrier = prepare_late("late-neutral")
    assert isinstance(compatible, PreparedLoopSemanticReduction)
    assert compatible.record.predecessor.head == "record:" + first_digest
    assert compatible.record.predecessor.fingerprint == first_digest
    assert compatible.record.reduction_id == first.record.reduction_id
    assert compatible.record.disposition == first.record.disposition
    assert compatible.record.complete_ordered_evidence == (
        *first.record.complete_ordered_evidence,
        compatible_carrier,
    )

    rival, rival_carrier = prepare_late("late-rival", negative=("subject",))
    assert isinstance(rival, PreparedLoopSemanticReduction)
    assert rival.record.predecessor.head == "record:" + first_digest
    assert rival.record.reduction_id == first.record.reduction_id
    assert rival.record.complete_ordered_evidence[-1] == rival_carrier
    assert rival.record.disposition.kind == "HOLD"
    assert rival.record.disposition.reason == "CONTRADICTORY"
