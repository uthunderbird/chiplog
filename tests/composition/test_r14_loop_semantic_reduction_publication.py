"""The loop semantic reducer command retains one exact owner proposal."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.original_call_resolution_preparation import (
    prepare_original_call_resolution,
)
from chiplog.capabilities.agent_loop.original_recovery_contracts import (
    HeldReduction,
    PreparedLoopSemanticReduction,
    ResolvedReductionAnchor,
)
from chiplog.capabilities.agent_loop.original_semantic_reduction_preparation import (
    prepare_loop_semantic_reduction,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.agent_loop.recovery_wire_contracts import (
    ORIGINAL_RECOVERY_REQUEST_SCHEMA,
    ORIGINAL_RECOVERY_RESULT_SCHEMA,
)
from chiplog.capabilities.effects.dispatch_outcome_contracts import DispatchOutcomeRecordV2
from chiplog.composition.r14_accepted_open_accounting_publication import (
    _reduction_matches_resolution,
)
from chiplog.composition.r14_loop_semantic_reduction_publication import (
    EVIDENCE_FIELD,
    OPERATION,
    OWNER_OPERATION,
    RetainedLoopSemanticReductionPublication,
    _selection_prefix,
    command,
    validate_selected_command,
    validate_selected_witness_at_cut,
)
from chiplog.platform.broker import BrokerSession, PublicPortCall, PublicPortSuccess
from tests.capabilities.agent_loop.test_original_call_resolution_preparation import (
    request as resolution_request,
)
from tests.capabilities.agent_loop.test_original_semantic_reduction_preparation import request


def retained() -> RetainedLoopSemanticReductionPublication:
    source = request()
    prepared = prepare_loop_semantic_reduction(source)
    assert isinstance(prepared, PreparedLoopSemanticReduction)
    session = BrokerSession(
        tenant_id=source.tenant_id,
        broker_epoch=1,
        generation_id="generation-1",
        owner_id="agent_loop",
        session_id="agent-loop:generation-1",
    )
    call = PublicPortCall(
        operation_id=OWNER_OPERATION,
        request_id="semantic-reduction:test",
        caller=session.model_copy(
            update={"owner_id": "broker", "session_id": "broker:generation-1"}
        ),
        callee=session,
        schema_id=ORIGINAL_RECOVERY_REQUEST_SCHEMA,
        canonical_payload=source.canonical_bytes(),
        budget={
            "remaining_calls": 1,
            "remaining_depth": 1,
            "policy_version": 1,
            "absolute_deadline_ns": 10_000_000_000,
        },
    )
    reply = PublicPortSuccess(
        request_id=call.request_id,
        responder=session,
        schema_id=ORIGINAL_RECOVERY_RESULT_SCHEMA,
        canonical_payload=prepared.canonical_bytes(),
    )
    return RetainedLoopSemanticReductionPublication(
        tenant_id=source.tenant_id,
        database_id=source.database_id,
        request=source,
        prepared=prepared,
        owner_call=call,
        owner_reply=reply,
        expected_head=source.tenant_commit_sequence,
        predecessor=source.materialization_commitment,
    )


def test_command_rederives_one_semantic_reduction_record() -> None:
    evidence = retained()

    batch = command(evidence)

    assert batch.operation_kind == "agent_loop.loop-semantic-reduction.v1"
    assert len(batch.records) == 1
    assert batch.records[0].schema_id == "chiplog.loop.semantic-reduction.v1"
    assert batch.idempotency_key.startswith("chiplog.loop.semantic-reduction.v1:")


def test_command_rejects_mismatched_owner_reply() -> None:
    evidence = retained()
    changed = evidence.model_copy(
        update={"owner_reply": evidence.owner_reply.model_copy(update={"canonical_payload": b"{}"})}
    )

    with pytest.raises(ValueError, match="recovery-wire body"):
        command(changed)


def test_selected_command_binds_retained_owner_exchange_and_header() -> None:
    evidence = retained()
    batch = command(evidence)
    entry = {
        "operation_kind": OPERATION,
        "operation_id": batch.idempotency_key,
        "expected_head": batch.expected_head,
        "fingerprint": batch.request_fingerprint,
        "predecessor": evidence.predecessor,
        "resulting": "f" * 64,
        EVIDENCE_FIELD: evidence.canonical_bytes().decode(),
    }
    runtime = SimpleNamespace(
        _tenant_id=evidence.tenant_id,
        _database_identity=(evidence.database_id, 1, 2),
        _check_database_identity=lambda: None,
    )

    assert validate_selected_command(runtime, entry, batch) == evidence

    with pytest.raises(ValueError, match="selected semantic reduction command differs"):
        validate_selected_command(runtime, {**entry, "operation_id": "foreign"}, batch)


def test_turn_consumer_requires_exact_current_consumable_reduction() -> None:
    resolution = prepare_original_call_resolution(resolution_request())
    reduction = prepare_loop_semantic_reduction(request())
    assert resolution.kind == "PREPARED_ORIGINAL_CALL_RESOLUTION_V1"
    assert isinstance(reduction, PreparedLoopSemanticReduction)

    def reference(record_id, record):
        fingerprint = hashlib.sha256(record.canonical_bytes()).hexdigest()
        return CallSubjectHead(
            subject_id=record_id,
            revision=Present(head="record:" + fingerprint, fingerprint=fingerprint),
        )

    aligned_anchor = ResolvedReductionAnchor(
        original=resolution.basis.original,
        accepted_witness=resolution.basis.selected_witness,
        closure=reference(resolution.closure.closure_id, resolution.closure),
        recovered_outcome=reference(
            resolution.recovered_outcome.outcome_id, resolution.recovered_outcome
        ),
        resolver_batch=reference(resolution.batch.batch_id, resolution.batch),
        accepted_semantic_class=resolution.basis.accepted_semantic_class,
    )
    aligned = reduction.record.model_copy(update={"anchor": aligned_anchor})
    assert _reduction_matches_resolution(resolution, SimpleNamespace(record=aligned))

    held = reduction.record.model_copy(
        update={
            "disposition": HeldReduction(reason="RIVAL", conflicting_or_unclassified_evidence=())
        }
    )
    assert not _reduction_matches_resolution(resolution, SimpleNamespace(record=held))

    stale_anchor = reduction.record.anchor.model_copy(
        update={"accepted_witness": resolution.basis.original_terminal}
    )
    stale = reduction.record.model_copy(update={"anchor": stale_anchor})
    assert not _reduction_matches_resolution(resolution, SimpleNamespace(record=stale))


def test_historical_witness_must_be_latest_selected_outcome_at_its_cut() -> None:
    from chiplog.platform.owner_decision_journal import OwnerJournalSnapshot

    source = request().complete_anchor_sources[-1]
    decision_id = "selected-r16-outcome"
    source = source.model_copy(
        update={
            "selected_decision": CallSubjectHead(
                subject_id=decision_id,
                revision=Present(head=decision_id, fingerprint="d" * 64),
            )
        }
    )
    witness = DispatchOutcomeRecordV2.model_validate_json(source.canonical_record_bytes)
    member = SimpleNamespace(
        owner="effects",
        schema_id=source.schema_id,
        record_id=source.physical_record.subject_id,
        canonical_bytes=source.canonical_record_bytes,
        fingerprint=source.physical_record.revision.fingerprint,
    )
    decision = SimpleNamespace(
        decision_id=source.selected_decision.subject_id,
        decision_fingerprint=source.selected_decision.revision.fingerprint,
        tenant_commit_sequence=5,
        prepared=SimpleNamespace(request=SimpleNamespace(complete_records=(member,))),
    )
    decisions = [decision]
    runtime = SimpleNamespace(
        _owner_decisions=lambda: SimpleNamespace(
            snapshot=lambda: OwnerJournalSnapshot(
                "tenant-original", "head", tuple(decisions), frozenset()
            )
        )
    )

    validate_selected_witness_at_cut(runtime, source, tenant_commit_sequence=6)

    later = witness.model_copy(
        update={"snapshot": witness.snapshot.model_copy(update={"conflicting_evidence": True})}
    )
    later_bytes = later.canonical_bytes()
    later_member = SimpleNamespace(
        owner="effects",
        schema_id=source.schema_id,
        record_id="later-outcome",
        canonical_bytes=later_bytes,
        fingerprint=hashlib.sha256(later_bytes).hexdigest(),
    )
    decisions.append(
        SimpleNamespace(
            decision_id="later-decision",
            decision_fingerprint="e" * 64,
            tenant_commit_sequence=6,
            prepared=SimpleNamespace(request=SimpleNamespace(complete_records=(later_member,))),
        )
    )
    with pytest.raises(ValueError, match="not latest"):
        validate_selected_witness_at_cut(runtime, source, tenant_commit_sequence=7)

    forged = source.model_copy(
        update={
            "canonical_record_bytes": later_bytes
        }
    )
    with pytest.raises(ValueError, match="physical member"):
        validate_selected_witness_at_cut(runtime, forged, tenant_commit_sequence=6)


def test_selection_prefix_rejects_rewritten_or_truncated_history() -> None:
    prior = (("decision-a", b"entry-a", {}, "batch-a"),)

    assert _selection_prefix(prior, (*prior, ("decision-b", b"entry-b", {}, "batch-b")))
    assert not _selection_prefix(prior, (("decision-a", b"changed", {}, "batch-a"),))
    assert not _selection_prefix(prior, ())
