"""Public consumers for executable recovery; wire objects are not writer credentials."""

import base64

import pytest
from pydantic import TypeAdapter, ValidationError
from tests.support.delivery_completion import _captured as captured
from tests.support.execution_fan_out import fixture
from tests.support.fan_out_shapes import head

from chiplog.capabilities.agent_loop import execution_completion_contracts as completion
from chiplog.capabilities.agent_loop import execution_recovery_contracts as recovery
from chiplog.capabilities.agent_loop import execution_recovery_observations as obs
from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.capabilities.agent_loop.execution_transition_contracts import CreateExecutionRun
from chiplog.capabilities.agent_loop.post_terminal_contracts import PreparedPostTerminalWork
from chiplog.capabilities.agent_loop.recovery_contracts import (
    Absent,
    OriginalObligationBinding,
    Present,
)
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import (
    EvidenceReduction,
    FrontierMember,
    FrozenRunBindings,
    RecoveryFrontier,
    RecoveryFrontierRegistry,
    RecoveryRegistryRow,
    SuspensionBaseline,
)


async def cut() -> obs.ExecutionRecoveryCut:
    captured_run = await fixture()
    frontier = RecoveryFrontier(
        tenant_id="tenant",
        run_id="run",
        tenant_commit_sequence=4,
        registry=RecoveryFrontierRegistry(
            registry_id="registry",
            version="1",
            fingerprint="a" * 64,
            ordered_rows=(
                RecoveryRegistryRow(
                    family="RUN",
                    ordinal=0,
                    subject_extractor_id="run",
                    cardinality_rule="exact-one",
                    terminal_conflict_rule="reject-rival",
                    serialization_rule="canonical",
                    canonicalization_version="1",
                ),
            ),
        ),
        ordered_members=(
            FrontierMember(
                family="RUN",
                subject="run",
                branch="ACTIVE",
                ordered_heads=(head("run").revision,),
                fingerprint="b" * 64,
            ),
        ),
        ordered_calls=(),
        canonicalization_version="chiplog.recovery.frontier.v1",
        fingerprint="c" * 64,
    )
    return obs.ExecutionRecoveryCut(
        tenant_id="tenant",
        database_id="database",
        tenant_commit_sequence=4,
        materialization_commitment="d" * 64,
        current_run=head("run"),
        complete_ordered_run_lineage=(captured_run.captured_run,),
        complete_ordered_responses=(),
        frontier=frontier,
        current_bindings=FrozenRunBindings(
            objective="Plan",
            requested_work="Plan",
            prompt_artifact=head("prompt").revision,
            ordered_tool_specs=(head("tool").revision,),
            generated_schema=head("schema").revision,
            semantic_bindings=(),
            recipient_effect_bindings=(),
            authority_scope=head("scope").revision,
            authority_mandate_heads=(),
            policy=head("policy").revision,
            no_retry_boundaries=(),
        ),
        original_closures=(),
        current_reductions=(),
        complete_sources=(
            obs.RecoverySourceRecord(
                owner="agent_loop",
                subject=head("source"),
                schema_id="source.v1",
                canonical_record_bytes=b"\xff\x00original-source",
                selected_decision=head("decision"),
                physical_record=head("physical"),
            ),
        ),
        complete_causal_changes=(),
        complete_inventory_fingerprint="e" * 64,
    )


async def suspension() -> recovery.PreparedExecutionSuspension:
    observed = await cut()
    baseline = obs.ExecutionSuspensionBaseline(
        baseline_id="baseline",
        suspension_command_id="suspend",
        run_id="run",
        predecessor_run=observed.current_run,
        source_cut_fingerprint=observed.digest(),
        frontier=observed.frontier,
        bindings=observed.current_bindings,
        original_obligations=(),
        activation_blocking_predicates=(),
    )
    baseline_head = CallSubjectHead(
        subject_id="baseline",
        revision=Present(head="record:" + baseline.digest(), fingerprint=baseline.digest()),
    )
    values = observed.complete_ordered_run_lineage[-1].model_dump()
    values.update(
        state="SUSPENDED",
        predecessor=values["head"],
        head="suspended-head",
        suspension_baseline=baseline_head,
        event="Suspended",
    )
    run = ExecutionRunRecord.model_validate(values)
    run_head = CallSubjectHead(
        subject_id=run.run_id, revision=Present(head=run.head, fingerprint=run.digest())
    )
    pair = obs.ExecutionSuspensionPair(
        suspension_command_id="suspend",
        source_cut_fingerprint=observed.digest(),
        predecessor_run=observed.current_run,
        baseline=baseline_head,
        suspended_run=run_head,
    )
    return recovery.PreparedExecutionSuspension(
        source_request_fingerprint="a" * 64,
        baseline=baseline,
        run=run,
        pair=pair,
        complete_batch_fingerprint="b" * 64,
    )


async def selected_suspension() -> obs.SelectedExecutionSuspension:
    prepared = await suspension()
    return obs.SelectedExecutionSuspension(
        baseline=prepared.baseline,
        pair=prepared.pair,
        selected_pair=head("selected-pair"),
        selected_decision=head("suspension-decision"),
        canonical_pair_bytes=prepared.pair.canonical_bytes(),
    )


async def test_complete_cut_preserves_binary_sources_and_explicit_empty_families() -> None:
    original = await cut()
    restored = obs.ExecutionRecoveryCut.model_validate_json(original.canonical_bytes())
    assert restored == original
    assert restored.complete_sources[0].canonical_record_bytes == b"\xff\x00original-source"
    for field in (
        "complete_ordered_run_lineage",
        "complete_ordered_responses",
        "original_closures",
        "current_reductions",
        "complete_sources",
        "complete_causal_changes",
    ):
        wire = original.model_dump()
        del wire[field]
        with pytest.raises(ValidationError):
            obs.ExecutionRecoveryCut.model_validate(wire)


async def test_suspension_hash_dag_has_no_new_run_back_reference_in_baseline() -> None:
    result = await suspension()
    adapter: TypeAdapter[recovery.ExecutionRecoveryResult] = TypeAdapter(
        recovery.ExecutionRecoveryResult
    )
    assert adapter.validate_json(result.canonical_bytes()) == result
    assert result.run.suspension_baseline == result.pair.baseline
    assert result.pair.baseline.revision.fingerprint == result.baseline.digest()
    assert result.pair.suspended_run.revision.fingerprint == result.run.digest()
    assert result.baseline.predecessor_run == result.pair.predecessor_run
    assert "suspended_run_head" not in obs.ExecutionSuspensionBaseline.model_fields
    with pytest.raises(ValidationError):
        SuspensionBaseline.model_validate_json(result.baseline.canonical_bytes())
    wire = result.model_dump()
    del wire["pair"]
    with pytest.raises(ValidationError):
        adapter.validate_python(wire)


@pytest.mark.parametrize(
    "field", ["pair", "selected_pair", "selected_decision", "canonical_pair_bytes"]
)
async def test_resume_needs_selected_original_pair_not_a_reconstructed_baseline(field: str) -> None:
    selected = await selected_suspension()
    wire = selected.model_dump()
    del wire[field]
    with pytest.raises(ValidationError):
        obs.SelectedExecutionSuspension.model_validate(wire)
    assert (
        obs.SelectedExecutionSuspension.model_validate_json(selected.canonical_bytes()) == selected
    )


async def test_accounting_and_readiness_are_distinct_result_types() -> None:
    observed = await cut()
    accounting = obs.SealedAccountingRecord(
        accounting_id="accounting",
        source_cut_fingerprint=observed.digest(),
        sealed_response=head("response"),
        sealed_manifest_fingerprint="a" * 64,
        registry=head("registry"),
        frontier=head("frontier"),
        complete_ordered_calls=(),
    )
    with pytest.raises(ValidationError):
        obs.ContinuationReadyRecord.model_validate_json(accounting.canonical_bytes())
    ready = obs.ContinuationReadyRecord(
        readiness_id="ready",
        source_cut_fingerprint=observed.digest(),
        accounting=accounting,
        complete_original_closures=(),
        complete_current_reductions=(),
    )
    assert obs.ContinuationReadyRecord.model_validate_json(ready.canonical_bytes()) == ready
    for field in ("accounting", "complete_original_closures", "complete_current_reductions"):
        wire = ready.model_dump()
        del wire[field]
        with pytest.raises(ValidationError):
            obs.ContinuationReadyRecord.model_validate(wire)


async def test_recovery_commands_do_not_require_their_own_selected_outputs() -> None:
    observed = await cut()
    captured_run = await fixture()
    original = await selected_suspension()
    suspended = (await suspension()).run
    requests: tuple[recovery.ExecutionRecoveryRequest, ...] = (
        recovery.PrepareExecutionAccounting(command_id="account", cut=observed),
        recovery.PrepareExecutionContinuation(
            command_id="ready", cut=observed, complete_accounting=()
        ),
        recovery.PrepareExecutionSuspension(
            command_id="suspend",
            run=captured_run.captured_run,
            cut=observed,
            activation_blocking_predicates=(),
            fence=captured_run.request.cut.fence,
        ),
        recovery.PrepareExecutionResume(
            command_id="resume",
            run=suspended,
            original_suspension=original,
            cut=observed,
            disposition_version="1",
            activation_payload=b"\xff\x00activation",
            fence=captured_run.request.cut.fence,
        ),
        recovery.PrepareAbortCancelExecution(
            command_id="cancel",
            run=captured_run.captured_run,
            target="CANCELLED",
            authenticated_cause=observed.complete_sources[0],
            cut=observed,
            complete_accounting=(),
            fence=captured_run.request.cut.fence,
        ),
    )
    adapter: TypeAdapter[recovery.ExecutionRecoveryRequest] = TypeAdapter(
        recovery.ExecutionRecoveryRequest
    )
    for request in requests:
        assert adapter.validate_json(request.canonical_bytes()) == request
        wire = request.model_dump()
        wire["ready"] = True
        with pytest.raises(ValidationError):
            adapter.validate_python(wire)


async def test_abort_cancel_preparation_cannot_publish_without_work_companions() -> None:
    observed = await cut()
    manifest = obs.ExecutionTerminalManifest(
        manifest_id="terminal-manifest",
        command_id="cancel",
        prior_run=observed.current_run,
        target="CANCELLED",
        source_cut_fingerprint=observed.digest(),
        complete_accounting=(),
        complete_open_original_obligations=(),
    )
    work = PreparedPostTerminalWork(
        source_request_fingerprint="a" * 64,
        ordered_work=(),
        complete_records=(),
        complete_commitment="b" * 64,
    )
    result = recovery.PreparedExecutionTerminal(
        source_request_fingerprint="c" * 64,
        manifest=manifest,
        run=observed.complete_ordered_run_lineage[-1],
        work=work,
        complete_batch_fingerprint="d" * 64,
    )
    # An ACTIVE output remains representable but is rejected by the owner/writer,
    # whose runtime behavior is outside this consumer test.
    adapter: TypeAdapter[recovery.ExecutionRecoveryResult] = TypeAdapter(
        recovery.ExecutionRecoveryResult
    )
    assert adapter.validate_json(result.canonical_bytes()) == result
    for field in ("manifest", "work"):
        wire = result.model_dump()
        del wire[field]
        with pytest.raises(ValidationError):
            adapter.validate_python(wire)


async def test_completion_carries_all_earlier_joins_and_captured_bytes_directly() -> None:
    execution = await fixture(complete=True)
    _, delivery = await captured()
    raw = base64.b64decode(execution.captured_run.turns[-1].attempts[-1].response_base64 or "")
    request = completion.PrepareExecutionCompletion(
        command_id="complete",
        run=execution.captured_run,
        selected_attempt=head("attempt"),
        selector_generation=0,
        visibility_manifest=head("visibility"),
        exact_captured_response=raw,
        cut=await cut(),
        complete_earlier_continuations=(),
        delivery=delivery,
        fence=execution.request.cut.fence,
    )
    restored = completion.PrepareExecutionCompletion.model_validate_json(request.canonical_bytes())
    assert restored == request
    assert restored.exact_captured_response == raw
    for field in (
        "complete_earlier_continuations",
        "cut",
        "visibility_manifest",
        "exact_captured_response",
    ):
        wire = request.model_dump()
        del wire[field]
        with pytest.raises(ValidationError):
            completion.PrepareExecutionCompletion.model_validate(wire)
    with pytest.raises(ValidationError):
        recovery.PrepareNextExecutionTurn.model_validate_json(request.canonical_bytes())


async def test_successor_and_next_turn_use_different_current_state_predicates() -> None:
    captured_run = await fixture()
    run = captured_run.captured_run
    observed = await cut()
    successor = recovery.PrepareExecutionSuccessor(
        command_id="successor",
        run=(await suspension()).run,
        original_suspension=await selected_suspension(),
        cut=observed,
        disposition_version="1",
        successor=CreateExecutionRun(
            command_id="initialize-successor",
            tenant=run.tenant,
            principal=run.principal,
            run_id="successor-run",
            prompt=run.prompt,
            policy=run.policy,
            origin=run.origin,
            contour_head=run.contour_head,
            policy_head=run.policy_head,
            worker_session=run.worker_session,
        ),
        fence=captured_run.request.cut.fence,
    )
    ready = obs.ContinuationReadyRecord(
        readiness_id="ready",
        source_cut_fingerprint=observed.digest(),
        accounting=obs.SealedAccountingRecord(
            accounting_id="accounting",
            source_cut_fingerprint=observed.digest(),
            sealed_response=head("preceding-response"),
            sealed_manifest_fingerprint="a" * 64,
            registry=head("registry"),
            frontier=head("frontier"),
            complete_ordered_calls=(),
        ),
        complete_original_closures=(),
        complete_current_reductions=(),
    )
    next_turn = recovery.PrepareNextExecutionTurn(
        command_id="next-turn",
        run=run,
        cut=observed,
        immediately_preceding_response=head("preceding-response"),
        expected_current_turn=head("current-turn"),
        next_ordinal=2,
        continuation=ready,
        fence=captured_run.request.cut.fence,
    )
    adapter: TypeAdapter[recovery.ExecutionRecoveryRequest] = TypeAdapter(
        recovery.ExecutionRecoveryRequest
    )
    for request in (successor, next_turn):
        assert adapter.validate_json(request.canonical_bytes()) == request
    with pytest.raises(ValidationError):
        recovery.PrepareNextExecutionTurn.model_validate_json(successor.canonical_bytes())
    for value in (False, 0, -1, 2**64):
        wire = next_turn.model_dump()
        wire["next_ordinal"] = value
        with pytest.raises(ValidationError):
            recovery.PrepareNextExecutionTurn.model_validate(wire)


async def test_recovered_witness_and_current_reduction_are_both_preserved() -> None:
    original = OriginalObligationBinding(
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
    closure = obs.OriginalRecoveredClosure(
        original=original,
        terminal_disposition=head("recovery-required").revision,
        recovered_outcome=head("recovered").revision,
        accepted_evidence=head("old-witness").revision,
        closure=head("closed").revision,
        resolver_batch=head("resolver-batch").revision,
        reduction_id="stable-reduction",
        accepted_semantic_class="confirmed",
    )
    reduction = EvidenceReduction(
        stream_id=original.evidence_stream_id,
        reduction_id=closure.reduction_id,
        reducer_id=original.reducer_id,
        reducer_version=original.reducer_version,
        current_reduction=head("new-current-reduction").revision,
        ordered_consumed_evidence=(
            closure.accepted_evidence,
            head("compatible-refinement").revision,
        ),
        accepted_witness=closure.accepted_evidence,
        accepted_outcome=closure.recovered_outcome,
        obligation_closure=closure.closure,
        resolver_batch=closure.resolver_batch,
        semantic_class="confirmed",
        consumability="CONSUMABLE",
    )
    values = (await cut()).model_dump()
    values.update(original_closures=(closure,), current_reductions=(reduction,))
    observed = obs.ExecutionRecoveryCut.model_validate(values)
    restored = obs.ExecutionRecoveryCut.model_validate_json(observed.canonical_bytes())
    assert restored.original_closures[0].accepted_evidence == head("old-witness").revision
    assert (
        restored.current_reductions[0].current_reduction == head("new-current-reduction").revision
    )
    assert restored.original_closures[0].original.original_run_id == "original-run"
    manifest = obs.ExecutionTerminalManifest(
        manifest_id="manifest",
        command_id="cancel",
        prior_run=observed.current_run,
        target="CANCELLED",
        source_cut_fingerprint=observed.digest(),
        complete_accounting=(),
        complete_open_original_obligations=(original,),
    )
    assert obs.ExecutionTerminalManifest.model_validate_json(manifest.canonical_bytes()) == manifest
