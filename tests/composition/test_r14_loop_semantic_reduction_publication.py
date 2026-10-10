"""The loop semantic reducer command retains one exact owner proposal."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from chiplog.adapters.driven.deployment_trust import IndependentTenantDecisionJournal
from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.execution_recovery_observations import RecoverySourceRecord
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
from chiplog.capabilities.effects.dispatch_outcomes import prepare_outcome
from chiplog.composition.r14_accepted_open_accounting_publication import (
    _reduction_matches_current_evidence,
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
from chiplog.composition.r14_original_call_resolution_source import (
    _authenticate_append_evidence_suffix,
    _verify_selected_commitment_chain,
)
from chiplog.platform._owner_publication_contracts import (
    SingleOwnerBatch,
)
from chiplog.platform._sqlite import (
    EventAppender,
    FenceAdvanceCommand,
    SQLiteMaterializer,
)
from chiplog.platform.authority_gate import AuthorityGate
from chiplog.platform.authority_reads import capture_authority_storage_state
from chiplog.platform.broker import BrokerSession, PublicPortCall, PublicPortSuccess
from chiplog.platform.owner_decision_journal import IndependentOwnerDecisionJournal
from chiplog.platform.owner_publications import (
    BrokerPublicationCoordinator,
    PreparedOwnerPublication,
)
from tests.capabilities.agent_loop.test_original_call_resolution_preparation import (
    _closed_r16,
    _head,
)
from tests.capabilities.agent_loop.test_original_call_resolution_preparation import (
    request as resolution_request,
)
from tests.capabilities.agent_loop.test_original_semantic_reduction_preparation import request
from tests.capabilities.effects.support.dispatch_outcomes import original_send
from tests.capabilities.effects.test_dispatch_outcomes import request as outcome_request


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


def test_turn_consumer_holds_when_authenticated_evidence_is_newer_than_reduction() -> None:
    prepared = prepare_loop_semantic_reduction(request())
    assert isinstance(prepared, PreparedLoopSemanticReduction)
    reduction = SimpleNamespace(record=prepared.record)
    sources = request().complete_ordered_evidence

    assert _reduction_matches_current_evidence(reduction, sources)
    original_witness = request().complete_anchor_sources[-1]
    successor_decision = original_witness.selected_decision.model_copy(
        update={"subject_id": "new-decision"}
    )
    successor_witness = original_witness.model_copy(
        update={"selected_decision": successor_decision}
    )
    assert not _reduction_matches_current_evidence(
        reduction,
        sources,
        current_witness=successor_witness,
        reduction_witness=original_witness,
    )
    late_source = sources[-1].model_copy(
        update={
            "physical_record": sources[-1].physical_record.model_copy(
                update={"subject_id": "late-rival"}
            )
        }
    )
    assert not _reduction_matches_current_evidence(
        reduction, (*sources, late_source)
    )
    assert not _reduction_matches_current_evidence(reduction, ())


def test_authenticated_late_evidence_chain_rejects_gap_fork_and_wrong_terminal() -> None:
    steps = {8: ("base", "append-1"), 9: ("append-1", "append-2")}
    _verify_selected_commitment_chain(7, "base", 9, "append-2", steps)

    with pytest.raises(ValueError, match="sequence gap"):
        _verify_selected_commitment_chain(7, "base", 9, "append-2", {8: steps[8]})
    with pytest.raises(ValueError, match="forks"):
        _verify_selected_commitment_chain(
            7, "base", 9, "append-2", {8: steps[8], 9: ("foreign", "append-2")}
        )
    with pytest.raises(ValueError, match="terminal commitment"):
        _verify_selected_commitment_chain(7, "base", 9, "stale", steps)


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
        record_id=source.physical_record.revision.head,
        canonical_bytes=source.canonical_record_bytes,
        fingerprint=hashlib.sha256(source.canonical_record_bytes).hexdigest(),
    )
    decision = SimpleNamespace(
        decision_id=source.selected_decision.subject_id,
        decision_fingerprint=source.selected_decision.revision.fingerprint,
        tenant_commit_sequence=5,
        prepared=SimpleNamespace(request=SimpleNamespace(complete_records=(member,))),
    )
    decisions = [decision]
    runtime = SimpleNamespace(_owner_decisions=lambda: pytest.fail("snapshot should be reused"))

    def owner_snapshot():
        return OwnerJournalSnapshot("tenant-original", "head", tuple(decisions), frozenset())

    validate_selected_witness_at_cut(
        runtime, source, tenant_commit_sequence=6, snapshot=owner_snapshot()
    )

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
        validate_selected_witness_at_cut(
            runtime, source, tenant_commit_sequence=7, snapshot=owner_snapshot()
        )

    forged = source.model_copy(
        update={
            "canonical_record_bytes": later_bytes
        }
    )
    with pytest.raises(ValueError, match="physical member"):
        validate_selected_witness_at_cut(
            runtime, forged, tenant_commit_sequence=6, snapshot=owner_snapshot()
        )


def _owner_outcome_batch(outcome, *, tenant: str, sequence: int, predecessor: str):
    raw = outcome.canonical_bytes()
    fingerprint = hashlib.sha256(raw).hexdigest()
    command_id = f"selected-r16-outcome-{sequence}"
    command_raw = f"select:{command_id}".encode()

    def digest(value: bytes) -> str:
        return hashlib.sha256(value).hexdigest()

    return SingleOwnerBatch.model_validate(
        {
            "operation": (
                "effects.reconcile"
                if outcome.command.operation == "RESOLVE_OBLIGATION"
                else "effects.record_evidence"
            ),
            "identity": {
                "tenant_id": tenant,
                "command_id": command_id,
                "command_fingerprint": digest(command_raw),
                "canonicalization_version": "chiplog.owner-publication.v1",
            },
            "authentication": {
                "kind": "WORKER",
                "invocation": {
                    "issuance_id": "issued",
                    "issuance_fingerprint": digest(b"issued"),
                    "broker_epoch": "epoch",
                    "broker_session": "session",
                    "runtime_generation": "generation",
                    "operation_subject": command_id,
                },
                "applicability_schema": "r14-test.selected-r16-outcome.v1",
                "applicability_bytes": command_raw,
                "applicability_fingerprint": digest(command_raw),
            },
            "expected": {
                "tenant_id": tenant,
                "tenant_frontier": sequence - 1,
                "expected_materialization_commitment": predecessor,
                "registry_head": "registry",
                "registry_fingerprint": digest(b"registry"),
                "ordered_heads": (),
                "complete_manifest_fingerprint": digest(raw),
            },
            "command": {
                "owner": "effects",
                "schema_id": outcome.command.schema_id,
                "canonical_bytes": outcome.command.canonical_bytes(),
                "fingerprint": digest(outcome.command.canonical_bytes()),
            },
            "complete_records": (
                {
                    "owner": "effects",
                    "record_kind": "DISPATCH_OUTCOME",
                    "record_id": outcome.record.head,
                    "schema_id": outcome.schema_id,
                    "canonical_bytes": raw,
                    "fingerprint": fingerprint,
                },
            ),
            "complete_batch_fingerprint": digest(b"batch:" + raw),
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("foreign_obligation", (False, True))
async def test_append_suffix_authenticates_real_selected_owner_lineage(
    tmp_path: Path, foreign_obligation: bool
) -> None:
    send, _, closed = _closed_r16()
    if foreign_obligation:
        other_send = original_send(("foreign-subject",))
        other_open = prepare_outcome(outcome_request(other_send, other_send))
        appended = prepare_outcome(
            outcome_request(
                other_open,
                other_send,
                positive=("foreign-subject",),
                name="foreign-obligation-evidence",
            )
        )
    else:
        appended = prepare_outcome(
            outcome_request(
                closed,
                send,
                negative=("subject",),
                name="late-rival-evidence",
            )
        )

    path = tmp_path / "r14-owner-lineage.sqlite"
    tenant = send.snapshot.intent.mandate.tenant_id
    owner_journal = IndependentOwnerDecisionJournal(
        IndependentTenantDecisionJournal(tmp_path / "owner-journal"), tenant
    )

    class Authority:
        async def prepare(self, batch):
            return PreparedOwnerPublication(
                batch,
                "issued",
                "fence",
                0,
                batch.expected.expected_materialization_commitment,
            )

        def authenticate_replay(self, query):
            return None

        def check_prepared(self, prepared):
            return None

        def materialization_state(self, decision):
            return "COMPLETE"

        def check_selected_predecessor(self, decision):
            return True

    async def publish(appender, outcome, sequence: int):
        predecessor = capture_authority_storage_state(path)[0]
        batch = _owner_outcome_batch(
            outcome, tenant=tenant, sequence=sequence, predecessor=predecessor
        )
        result = await BrokerPublicationCoordinator(
            appender, Authority(), owner_journal
        ).commit(batch)
        assert result.kind == "COMMITTED"
        decision = owner_journal.lookup(tenant, batch.identity.command_id)
        assert decision is not None
        return decision

    with SQLiteMaterializer(
        path,
        record_contracts={"effects": "chiplog.effects.dispatch-outcome-record.v2"},
    ) as store:
        async with EventAppender(store, capacity=2) as appender:
            await appender.advance_fence(FenceAdvanceCommand(tenant, "fence", 0))
            base = await publish(appender, closed, 1)
            await publish(appender, appended, 2)

    base_outcome = DispatchOutcomeRecordV2.model_validate_json(
        base.prepared.request.complete_records[0].canonical_bytes
    )
    base_record = base_outcome.record
    base_ref = _head(base_record.subject_id, base_record.head, base_record.fingerprint)
    base_witness = RecoverySourceRecord(
        owner="effects",
        subject=base_ref,
        schema_id=base_outcome.schema_id,
        canonical_record_bytes=base_outcome.canonical_bytes(),
        selected_decision=_head(
            base.decision_id, base.decision_head, base.decision_fingerprint
        ),
        physical_record=base_ref,
    )

    from chiplog.composition.r14_runtime import R14PlanningRuntime
    from chiplog.platform.authority_reads import capture_authority_snapshot_commitment
    from chiplog.platform.workspace_snapshot import read_connection

    gate = AuthorityGate(path)
    with read_connection(path) as connection:
        terminal = capture_authority_snapshot_commitment(connection, tenant)
    loop_journal = SimpleNamespace(entries=lambda: ())
    runtime = SimpleNamespace(
        _tenant_id=tenant,
        _database=path,
        _authority_gate=lambda: gate,
        _owner_decisions=lambda: owner_journal,
        _loop_decisions=lambda: loop_journal,
        _owner_command=R14PlanningRuntime._owner_command,
        _publication=lambda entry: pytest.fail("no loop publication is selected"),
        _check_database_identity=lambda: None,
        _commitment_journal=SimpleNamespace(load=lambda _tenant: terminal),
        _pending=lambda: (),
    )

    with gate.hold():
        if foreign_obligation:
            with pytest.raises(LoopRejected, match="same-obligation append"):
                _authenticate_append_evidence_suffix(
                    runtime,
                    base_sequence=base.tenant_commit_sequence,
                    base_commitment=base.resulting_commitment,
                    end_sequence=2,
                    end_commitment=terminal,
                    base_witness=base_witness,
                    owner_snapshot=owner_journal.snapshot(),
                    loop_entries=(),
                )
        else:
            _authenticate_append_evidence_suffix(
                runtime,
                base_sequence=base.tenant_commit_sequence,
                base_commitment=base.resulting_commitment,
                end_sequence=2,
                end_commitment=terminal,
                base_witness=base_witness,
                owner_snapshot=owner_journal.snapshot(),
                loop_entries=(),
            )


def test_selection_prefix_rejects_rewritten_or_truncated_history() -> None:
    prior = (("decision-a", b"entry-a", {}, "batch-a"),)

    assert _selection_prefix(prior, (*prior, ("decision-b", b"entry-b", {}, "batch-b")))
    assert not _selection_prefix(prior, (("decision-a", b"changed", {}, "batch-a"),))
    assert not _selection_prefix(prior, ())
