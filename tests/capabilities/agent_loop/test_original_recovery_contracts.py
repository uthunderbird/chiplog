"""Consumer boundaries for original loop streams, never proof of resolver authority."""

import pytest
from pydantic import TypeAdapter, ValidationError
from tests.support.fan_out_shapes import head

from chiplog.capabilities.agent_loop import original_recovery_contracts as original
from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.execution_recovery_observations import RecoverySourceRecord
from chiplog.capabilities.agent_loop.recovery_contracts import (
    Absent,
    OriginalObligationBinding,
    Present,
)
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import EvidenceReduction


def binding() -> OriginalObligationBinding:
    return OriginalObligationBinding(
        original_run_id="original-run",
        original_call_id="original-call",
        obligation_id="obligation",
        obligation_stream_id="original-obligation-stream",
        obligation_head="open-head",
        closure_predicate_id="closure",
        closure_predicate_version="1",
        resolver_id="resolver",
        resolver_version="1",
        reducer_id="reducer",
        reducer_version="1",
        evidence_stream_id="original-evidence-stream",
        evidence_head=Absent(),
    )


def source(name: str, owner: str = "agent_loop") -> RecoverySourceRecord:
    return RecoverySourceRecord(
        owner=owner,
        subject=head(name),
        schema_id=name + ".v1",
        canonical_record_bytes=b"\xff\x00" + name.encode(),
        selected_decision=head("selected:" + name),
        physical_record=head("physical:" + name),
    )


def stream(name: str) -> original.LoopRecoveryStream:
    return original.LoopRecoveryStream(
        stream_id=name, registry=head("registry"), subject=head(name), schema_id="stream.v1"
    )


def authority() -> original.IndependentResolverAuthority:
    return original.IndependentResolverAuthority(
        registered_authority=source("authority"), authenticated_invocation=source("invocation")
    )


def request() -> original.PrepareOriginalCallResolution:
    return original.PrepareOriginalCallResolution(
        command_id="resolve-original",
        cut=original.OriginalResolverCut(
            tenant_id="tenant",
            database_id="database",
            tenant_commit_sequence=7,
            materialization_commitment="a" * 64,
            original=binding(),
            obligation_stream=stream(binding().obligation_stream_id),
            current_obligation=source("obligation"),
            call_terminal=source("recovery-required"),
            complete_ordered_evidence=(source("witness", "effects"),),
            complete_selected_foreign_reductions=(source("effects-reduction", "effects"),),
            expected_closure=Absent(),
            expected_recovered_outcome=Absent(),
            resolver_policy=source("resolver-policy"),
            reducer_policy=source("reducer-policy"),
            authority=authority(),
        ),
    )


def ref(name: str, digest: str) -> CallSubjectHead:
    return CallSubjectHead(
        subject_id=name, revision=Present(head="record:" + digest, fingerprint=digest)
    )


def test_resolver_retains_original_identity_and_already_selected_foreign_evidence() -> None:
    value = request()
    adapter: TypeAdapter[original.OriginalRecoveryRequest] = TypeAdapter(
        original.OriginalRecoveryRequest
    )
    restored = adapter.validate_json(value.canonical_bytes())
    assert restored == value
    assert isinstance(restored, original.PrepareOriginalCallResolution)
    assert restored.cut.original.original_run_id == "original-run"
    assert restored.cut.complete_ordered_evidence[0].owner == "effects"
    assert restored.cut.complete_ordered_evidence[0].canonical_record_bytes == b"\xff\x00witness"
    assert "run" not in original.OriginalResolverCut.model_fields
    assert "fence" not in original.IndependentResolverAuthority.model_fields
    wire = value.model_dump()
    wire["append_evidence"] = source("new-evidence")
    with pytest.raises(ValidationError):
        adapter.validate_python(wire)


@pytest.mark.parametrize(
    "field",
    [
        "current_obligation",
        "call_terminal",
        "complete_ordered_evidence",
        "complete_selected_foreign_reductions",
        "expected_closure",
        "expected_recovered_outcome",
        "resolver_policy",
        "reducer_policy",
        "authority",
    ],
)
def test_original_cut_requires_explicit_cas_and_source_families(field: str) -> None:
    wire = request().cut.model_dump()
    del wire[field]
    with pytest.raises(ValidationError):
        original.OriginalResolverCut.model_validate(wire)


@pytest.mark.parametrize("owner", ["effects", "planning", "conversation", "successor"])
def test_loop_port_cannot_describe_itself_as_foreign_stream_writer(owner: str) -> None:
    wire = stream("evidence").model_dump()
    wire["owner"] = owner
    with pytest.raises(ValidationError):
        original.LoopRecoveryStream.model_validate(wire)


def test_resolver_result_hashes_basis_then_closure_then_outcome_then_batch() -> None:
    value = request()
    basis = original.OriginalResolutionBasis(
        basis_id="basis",
        source_request_fingerprint=value.digest(),
        original=binding(),
        original_terminal=value.cut.call_terminal.subject,
        selected_witness=head("witness"),
        complete_evidence_manifest=(head("witness"),),
        resolver_policy=head("resolver-policy"),
        reducer_policy=head("reducer-policy"),
        reduction_id="stable-reduction",
        accepted_semantic_class="confirmed",
    )
    basis_head = ref(basis.basis_id, basis.digest())
    closure = original.OriginalObligationClosureRecord(
        closure_id="closed",
        basis=basis_head,
        original=binding(),
        prior_obligation=head("obligation"),
        accepted_witness=head("witness"),
        closure_predicate=head("closure"),
    )
    closure_head = ref(closure.closure_id, closure.digest())
    outcome = original.RecoveredCallOutcomeRecord(
        outcome_id="outcome",
        basis=basis_head,
        original=binding(),
        closure=closure_head,
        accepted_witness=head("witness"),
        result_schema="result.v1",
        canonical_result_bytes=b"\xffresult",
        accepted_semantic_class="confirmed",
    )
    outcome_head = ref(outcome.outcome_id, outcome.digest())
    batch = original.OriginalResolverBatchRecord(
        batch_id="batch",
        command_id=value.command_id,
        basis=basis_head,
        closure=closure_head,
        recovered_outcome=outcome_head,
        source_cut_fingerprint=value.cut.digest(),
    )
    result = original.PreparedOriginalCallResolution(
        source_request_fingerprint=value.digest(),
        basis=basis,
        closure=closure,
        recovered_outcome=outcome,
        batch=batch,
        complete_batch_fingerprint="b" * 64,
    )
    adapter: TypeAdapter[original.OriginalRecoveryResult] = TypeAdapter(
        original.OriginalRecoveryResult
    )
    assert adapter.validate_json(result.canonical_bytes()) == result
    assert result.recovered_outcome.closure.revision.fingerprint == result.closure.digest()
    assert result.batch.recovered_outcome.revision.fingerprint == result.recovered_outcome.digest()
    for field in ("basis", "closure", "recovered_outcome", "batch"):
        wire = result.model_dump()
        del wire[field]
        with pytest.raises(ValidationError):
            adapter.validate_python(wire)
    wire = result.model_dump()
    wire["new_evidence"] = source("new-evidence")
    with pytest.raises(ValidationError):
        adapter.validate_python(wire)


def test_preclosure_reduction_does_not_invent_resolved_continuation_anchors() -> None:
    unresolved = original.UnresolvedReductionAnchor(
        original=binding(), closure=Absent(), recovered_outcome=Absent()
    )
    record = original.LoopSemanticReductionRecord(
        stream=stream(binding().evidence_stream_id),
        reduction_id="reduction",
        reducer_id="reducer",
        reducer_version="1",
        predecessor=Absent(),
        complete_ordered_evidence=(),
        anchor=unresolved,
        disposition=original.HeldReduction(
            reason="INCOMPLETE", conflicting_or_unclassified_evidence=()
        ),
    )
    assert (
        original.LoopSemanticReductionRecord.model_validate_json(record.canonical_bytes()) == record
    )
    with pytest.raises(ValidationError):
        EvidenceReduction.model_validate_json(record.canonical_bytes())
    with pytest.raises(ValidationError):
        original.ResolvedReductionAnchor.model_validate_json(unresolved.canonical_bytes())
    assert "current_reduction" not in original.LoopSemanticReductionRecord.model_fields


@pytest.mark.parametrize(
    "reason",
    [
        "INCOMPLETE",
        "RIVAL",
        "CONTRADICTORY",
        "MEANING_CHANGED",
        "UNCLASSIFIED",
        "REGISTRY_CHANGED",
    ],
)
def test_hold_reduction_preserves_resolved_original_anchors(reason: str) -> None:
    anchor = original.ResolvedReductionAnchor(
        original=binding(),
        accepted_witness=head("old-witness"),
        recovered_outcome=head("outcome"),
        closure=head("closed"),
        resolver_batch=head("batch"),
        accepted_semantic_class="confirmed",
    )
    held = original.HeldReduction.model_validate(
        {"reason": reason, "conflicting_or_unclassified_evidence": (head("late-evidence"),)}
    )
    record = original.LoopSemanticReductionRecord(
        stream=stream(binding().evidence_stream_id),
        reduction_id="stable-reduction",
        reducer_id="reducer",
        reducer_version="1",
        predecessor=head("old-reduction").revision,
        complete_ordered_evidence=(head("old-witness"), head("late-evidence")),
        anchor=anchor,
        disposition=held,
    )
    restored = original.LoopSemanticReductionRecord.model_validate_json(record.canonical_bytes())
    assert restored == record
    assert isinstance(restored.anchor, original.ResolvedReductionAnchor)
    assert restored.anchor.accepted_witness == head("old-witness")
    assert restored.reduction_id == "stable-reduction"
    result = original.PreparedLoopSemanticReduction(
        source_request_fingerprint="a" * 64, record=record, complete_batch_fingerprint="b" * 64
    )
    adapter: TypeAdapter[original.OriginalRecoveryResult] = TypeAdapter(
        original.OriginalRecoveryResult
    )
    assert adapter.validate_json(result.canonical_bytes()) == result


def test_reduction_request_retains_source_preimages_and_explicit_expected_absence() -> None:
    value = original.PrepareLoopSemanticReduction(
        command_id="reduce",
        tenant_id="tenant",
        database_id="database",
        tenant_commit_sequence=7,
        materialization_commitment="a" * 64,
        stream=stream(binding().evidence_stream_id),
        expected=Absent(),
        original=binding(),
        complete_ordered_evidence=(source("witness"),),
        complete_anchor_sources=(),
        reducer_policy=source("reducer"),
        authority=authority(),
    )
    adapter: TypeAdapter[original.OriginalRecoveryRequest] = TypeAdapter(
        original.OriginalRecoveryRequest
    )
    assert adapter.validate_json(value.canonical_bytes()) == value
    for field in (
        "expected",
        "complete_ordered_evidence",
        "complete_anchor_sources",
        "reducer_policy",
    ):
        wire = value.model_dump()
        del wire[field]
        with pytest.raises(ValidationError):
            adapter.validate_python(wire)
