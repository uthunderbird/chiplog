"""Public v2/v3 join contracts for OPEN executable preparation DTOs."""

import json
import subprocess
import sys
from typing import Any, Protocol

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError
from tests.support.execution_fan_out import fixture
from tests.support.execution_versions import execution_run_v3

import chiplog.capabilities.agent_loop.execution_completion_contracts as completion
import chiplog.capabilities.agent_loop.execution_initialization_contracts as initialization
import chiplog.capabilities.agent_loop.execution_recovery_contracts as recovery
import chiplog.capabilities.agent_loop.execution_recovery_observations as observations
import chiplog.capabilities.agent_loop.model_attempt_recovery_contracts as model_attempt
import chiplog.capabilities.agent_loop.post_terminal_contracts as post_terminal
import chiplog.capabilities.agent_loop.readonly_execution_contracts as readonly
from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.capabilities.agent_loop.execution_history_contracts import ExecutionRunRecordV3
from chiplog.capabilities.agent_loop.execution_history_transition_contracts import (
    CreateExecutionRunV3,
)
from chiplog.capabilities.agent_loop.execution_run_versions import (
    CreateExecutionRunVersion,
    ExecutionRun,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Present


def _head(subject: str) -> CallSubjectHead:
    return CallSubjectHead(
        subject_id=subject,
        revision=Present(head="record:" + subject, fingerprint="a" * 64),
    )


class CanonicalDTO(Protocol):
    def canonical_bytes(self) -> bytes: ...


def test_execution_run_union_roundtrips_nonempty_v3_history_call() -> None:
    run = execution_run_v3()
    restored: ExecutionRunRecord | ExecutionRunRecordV3 = TypeAdapter(ExecutionRun).validate_json(
        run.canonical_bytes()
    )
    assert restored == run
    assert restored.turns[0].attempts[0].response_base64 is not None

    wire = json.loads(run.canonical_bytes())
    wire["schema_id"] = "chiplog.agent-loop.execution-record.v2"
    with pytest.raises(ValidationError):
        TypeAdapter(ExecutionRun).validate_json(json.dumps(wire))
    wire["schema_id"] = "unknown.run"
    with pytest.raises(ValidationError):
        TypeAdapter(ExecutionRun).validate_json(json.dumps(wire))


async def test_execution_run_union_retains_v2_canonical_payload() -> None:
    captured = await fixture()
    payload = captured.captured_run.canonical_bytes()
    restored: ExecutionRunRecord | ExecutionRunRecordV3 = TypeAdapter(ExecutionRun).validate_json(
        payload
    )
    assert restored.canonical_bytes() == payload


@pytest.mark.parametrize(
    ("owner", "field", "discriminator"),
    (
        (initialization.PrepareInboxExecution, "create", "kind"),
        (initialization.PrepareScheduledExecution, "create", "kind"),
        (initialization.PreparedExecutionInitialization, "run", "schema_id"),
        (observations.ExecutionRecoveryCut, "complete_ordered_run_lineage", "schema_id"),
        (recovery.PrepareExecutionSuspension, "run", "schema_id"),
        (recovery.PrepareExecutionResume, "run", "schema_id"),
        (recovery.PrepareExecutionSuccessor, "run", "schema_id"),
        (recovery.PrepareExecutionSuccessor, "successor", "kind"),
        (recovery.PrepareNextExecutionTurn, "run", "schema_id"),
        (recovery.PrepareAbortCancelExecution, "run", "schema_id"),
        (recovery.PreparedExecutionSuspension, "run", "schema_id"),
        (recovery.PreparedExecutionResume, "run", "schema_id"),
        (recovery.PreparedExecutionSuccessor, "superseded_run", "schema_id"),
        (recovery.PreparedExecutionSuccessor, "successor_run", "schema_id"),
        (recovery.PreparedNextExecutionTurn, "run", "schema_id"),
        (recovery.PreparedExecutionTerminal, "run", "schema_id"),
        (readonly.PrepareReadOnlyAttempt, "run", "schema_id"),
        (readonly.PrepareReadOnlyPending, "run", "schema_id"),
        (completion.PrepareExecutionCompletion, "run", "schema_id"),
        (completion.PreparedExecutionCompletion, "run", "schema_id"),
        (completion.PreparedExecutionCompletionReject, "run", "schema_id"),
        (model_attempt.ReplaceExecutionModelAttempt, "run", "schema_id"),
        (model_attempt.PreparedModelAttemptReplacement, "run", "schema_id"),
        (post_terminal.PrepareTerminalWork, "terminal_run", "schema_id"),
    ),
)
def test_every_open_embedded_run_boundary_exposes_the_version_discriminator(
    owner: type[BaseModel], field: str, discriminator: str
) -> None:
    property_schema = owner.model_json_schema()["properties"][field]
    if field == "complete_ordered_run_lineage":
        property_schema = property_schema["items"]
    assert property_schema["discriminator"]["propertyName"] == discriminator
    assert len(property_schema["oneOf"]) == 2


async def test_initialization_and_recovery_cut_accept_v3_without_changing_v2_bytes() -> None:
    v3 = execution_run_v3()
    captured = await fixture()
    v2_bytes = captured.captured_run.canonical_bytes()
    create = CreateExecutionRunV3(
        command_id="create-v3",
        tenant=v3.tenant,
        principal=v3.principal,
        run_id=v3.run_id,
        prompt=v3.prompt,
        policy=v3.policy,
        origin=v3.origin,
        contour_head=v3.contour_head,
        policy_head=v3.policy_head,
        worker_session=v3.worker_session,
    )
    assert TypeAdapter(CreateExecutionRunVersion).validate_json(create.canonical_bytes()) == create
    bad_create = json.loads(create.canonical_bytes())
    bad_create["kind"] = "UNKNOWN_CREATE_EXECUTION_RUN"
    with pytest.raises(ValidationError):
        TypeAdapter(CreateExecutionRunVersion).validate_json(json.dumps(bad_create))

    prepared = initialization.PreparedExecutionInitialization(
        source_request_fingerprint="a" * 64,
        run=v3,
        input_binding=initialization.AdmittedExecutionBinding(
            original_inbox=_head("inbox"),
            original_custody=_head("custody"),
            normalization=_head("normalization"),
        ),
        proposal_fingerprint="b" * 64,
    )
    assert (
        TypeAdapter(initialization.ExecutionInitializationResult).validate_json(
            prepared.canonical_bytes()
        )
        == prepared
    )

    cut_wire = {
        **(await _recovery_cut_wire()).model_dump(mode="json"),
        "complete_ordered_run_lineage": [json.loads(v3.canonical_bytes())],
    }
    cut = observations.ExecutionRecoveryCut.model_validate_json(json.dumps(cut_wire))
    assert cut.complete_ordered_run_lineage == (v3,)
    assert captured.captured_run.canonical_bytes() == v2_bytes


async def _recovery_cut_wire() -> observations.ExecutionRecoveryCut:
    """Use the existing v2 owner fixture for all unrelated recovery evidence."""

    from chiplog.capabilities.agent_loop.recovery_frontier_contracts import (
        FrontierMember,
        FrozenRunBindings,
        RecoveryFrontier,
        RecoveryFrontierRegistry,
        RecoveryRegistryRow,
    )

    captured = await fixture()
    return observations.ExecutionRecoveryCut(
        tenant_id="tenant",
        database_id="database",
        tenant_commit_sequence=1,
        materialization_commitment="a" * 64,
        current_run=_head("run"),
        complete_ordered_run_lineage=(captured.captured_run,),
        complete_ordered_responses=(),
        frontier=RecoveryFrontier(
            tenant_id="tenant",
            run_id="run",
            tenant_commit_sequence=1,
            registry=RecoveryFrontierRegistry(
                registry_id="registry",
                version="1",
                fingerprint="b" * 64,
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
                    ordered_heads=(_head("run").revision,),
                    fingerprint="c" * 64,
                ),
            ),
            ordered_calls=(),
            canonicalization_version="chiplog.recovery.frontier.v1",
            fingerprint="d" * 64,
        ),
        current_bindings=FrozenRunBindings(
            objective="objective",
            requested_work="work",
            prompt_artifact=_head("prompt").revision,
            ordered_tool_specs=(_head("tool").revision,),
            generated_schema=_head("schema").revision,
            semantic_bindings=(),
            recipient_effect_bindings=(),
            authority_scope=_head("scope").revision,
            authority_mandate_heads=(),
            policy=_head("policy").revision,
            no_retry_boundaries=(),
        ),
        original_closures=(),
        current_reductions=(),
        complete_sources=(
            observations.RecoverySourceRecord(
                owner="agent-loop",
                subject=_head("source"),
                schema_id="source.v1",
                canonical_record_bytes=b"source",
                selected_decision=_head("decision"),
                physical_record=_head("physical"),
            ),
        ),
        complete_causal_changes=(),
        complete_inventory_fingerprint="e" * 64,
    )


def test_version_leaf_imports_in_fresh_processes() -> None:
    modules = (
        "chiplog.capabilities.agent_loop.execution_history_contracts",
        "chiplog.capabilities.agent_loop.readonly_execution_contracts",
        "chiplog.capabilities.agent_loop.execution_run_versions",
    )
    for order in (modules, tuple(reversed(modules))):
        subprocess.run(
            [sys.executable, "-c", "; ".join("import " + module for module in order)],
            check=True,
        )


def _roundtrip(adapter: Any, value: CanonicalDTO) -> None:
    assert TypeAdapter(adapter).validate_json(value.canonical_bytes()) == value


async def _v3_cut() -> observations.ExecutionRecoveryCut:
    run = execution_run_v3()
    wire = (await _recovery_cut_wire()).model_dump(mode="json")
    wire["complete_ordered_run_lineage"] = [json.loads(run.canonical_bytes())]
    return observations.ExecutionRecoveryCut.model_validate_json(json.dumps(wire))


async def _selected_suspension(
    cut: observations.ExecutionRecoveryCut,
) -> observations.SelectedExecutionSuspension:
    baseline = observations.ExecutionSuspensionBaseline(
        baseline_id="baseline",
        suspension_command_id="suspend",
        run_id="run-v3",
        predecessor_run=cut.current_run,
        source_cut_fingerprint=cut.digest(),
        frontier=cut.frontier,
        bindings=cut.current_bindings,
        original_obligations=(),
        activation_blocking_predicates=(),
    )
    baseline_head = _head("baseline")
    pair = observations.ExecutionSuspensionPair(
        suspension_command_id="suspend",
        source_cut_fingerprint=cut.digest(),
        predecessor_run=cut.current_run,
        baseline=baseline_head,
        suspended_run=_head("suspended"),
    )
    return observations.SelectedExecutionSuspension(
        baseline=baseline,
        pair=pair,
        selected_pair=_head("selected-pair"),
        selected_decision=_head("suspension-decision"),
        canonical_pair_bytes=pair.canonical_bytes(),
    )


def _continuation(cut: observations.ExecutionRecoveryCut) -> observations.ContinuationReadyRecord:
    accounting = observations.SealedAccountingRecord(
        accounting_id="accounting",
        source_cut_fingerprint=cut.digest(),
        sealed_response=_head("response"),
        sealed_manifest_fingerprint="a" * 64,
        registry=_head("registry"),
        frontier=_head("frontier"),
        complete_ordered_calls=(),
    )
    return observations.ContinuationReadyRecord(
        readiness_id="ready",
        source_cut_fingerprint=cut.digest(),
        accounting=accounting,
        complete_original_closures=(),
        complete_current_reductions=(),
    )


async def test_v3_recovery_requests_and_terminal_result_roundtrip_public_unions() -> None:
    import chiplog.capabilities.agent_loop.execution_recovery_contracts as recovery
    from chiplog.capabilities.agent_loop.execution_history_transition_contracts import (
        CreateExecutionRunV3,
    )
    from chiplog.capabilities.agent_loop.post_terminal_contracts import PreparedPostTerminalWork

    run = execution_run_v3()
    cut = await _v3_cut()
    selected = await _selected_suspension(cut)
    continuation = _continuation(cut)
    fence = (await fixture()).request.cut.fence
    successor = CreateExecutionRunV3(
        command_id="create-successor",
        tenant=run.tenant,
        principal=run.principal,
        run_id="successor-v3",
        prompt=run.prompt,
        policy=run.policy,
        origin=run.origin,
        contour_head=run.contour_head,
        policy_head=run.policy_head,
        worker_session=run.worker_session,
    )
    requests: tuple[recovery.ExecutionRecoveryRequest, ...] = (
        recovery.PrepareExecutionSuspension(
            command_id="suspend",
            run=run,
            cut=cut,
            activation_blocking_predicates=(),
            fence=fence,
        ),
        recovery.PrepareExecutionResume(
            command_id="resume",
            run=run,
            original_suspension=selected,
            cut=cut,
            disposition_version="1",
            activation_payload=b"resume",
            fence=fence,
        ),
        recovery.PrepareExecutionSuccessor(
            command_id="successor",
            run=run,
            original_suspension=selected,
            cut=cut,
            disposition_version="1",
            successor=successor,
            fence=fence,
        ),
        recovery.PrepareNextExecutionTurn(
            command_id="next",
            run=run,
            cut=cut,
            immediately_preceding_response=_head("response"),
            expected_current_turn=_head("turn"),
            next_ordinal=2,
            continuation=continuation,
            fence=fence,
        ),
        recovery.PrepareAbortCancelExecution(
            command_id="cancel",
            run=run,
            target="CANCELLED",
            authenticated_cause=cut.complete_sources[0],
            cut=cut,
            complete_accounting=(),
            fence=fence,
        ),
    )
    for request in requests:
        _roundtrip(recovery.ExecutionRecoveryRequest, request)

    manifest = observations.ExecutionTerminalManifest(
        manifest_id="terminal",
        command_id="terminal-command",
        prior_run=cut.current_run,
        target="CANCELLED",
        source_cut_fingerprint=cut.digest(),
        complete_accounting=(),
        complete_open_original_obligations=(),
    )
    terminal = recovery.PreparedExecutionTerminal(
        source_request_fingerprint="a" * 64,
        manifest=manifest,
        run=run,
        work=PreparedPostTerminalWork(
            source_request_fingerprint="b" * 64,
            ordered_work=(),
            complete_records=(),
            complete_commitment="c" * 64,
        ),
        complete_batch_fingerprint="d" * 64,
    )
    edge = recovery.ExecutionSuccessorEdge(
        command_id="successor",
        original_pair=_head("selected-pair"),
        predecessor_before=_head("before"),
        predecessor_superseded=_head("superseded"),
        successor_created=_head("successor"),
        source_cut_fingerprint=cut.digest(),
        disposition_version="1",
        changed_binding_manifest=(_head("changed"),),
        complete_initialization=(_head("initialization"),),
        original_obligations=(),
        inherited_no_retry_boundaries=(),
        inherited_pending_branches=(),
        observation_frontier=1,
        fence=fence,
    )
    results: tuple[recovery.ExecutionRecoveryResult, ...] = (
        recovery.PreparedExecutionSuspension(
            source_request_fingerprint="a" * 64,
            baseline=selected.baseline,
            run=run,
            pair=selected.pair,
            complete_batch_fingerprint="b" * 64,
        ),
        recovery.PreparedExecutionResume(
            source_request_fingerprint="a" * 64,
            original_pair=selected.selected_pair,
            run=run,
            complete_batch_fingerprint="b" * 64,
        ),
        recovery.PreparedExecutionSuccessor(
            source_request_fingerprint="a" * 64,
            superseded_run=run,
            successor_run=run,
            edge=edge,
            lineage_advance=recovery.NonSchedulerSuccessor(),
            complete_initialization_bytes=(b"initialization",),
            complete_batch_fingerprint="b" * 64,
        ),
        recovery.PreparedNextExecutionTurn(
            source_request_fingerprint="a" * 64,
            run=run,
            continuation=continuation,
            complete_batch_fingerprint="b" * 64,
        ),
        terminal,
    )
    for result in results:
        _roundtrip(recovery.ExecutionRecoveryResult, result)


async def test_v3_readonly_attempt_and_pending_roundtrip_public_union() -> None:
    from chiplog.capabilities.agent_loop.call_acceptance_contracts import (
        InitializedCallRecord,
        InitializedReadOnlyLineage,
        SealedCallInput,
    )
    from chiplog.capabilities.agent_loop.recovery_contracts import Absent
    from chiplog.capabilities.agent_loop.recovery_frontier_contracts import ReadOnlyRetryLineage

    run = execution_run_v3()
    captured = await fixture()
    lineage = ReadOnlyRetryLineage(
        lineage_id="history-lineage",
        original_call_id="history-call",
        max_attempts=2,
        budget_version="budget.v1",
        reducer_id="reducer",
        reducer_version="1",
    )
    call_wire = captured.request.ordered_calls[0].model_dump()
    call_wire.update(
        classification="READ_ONLY",
        retry_lineage=InitializedReadOnlyLineage(lineage=lineage),
    )
    initialized = InitializedCallRecord(
        original_call_id=lineage.original_call_id,
        call=SealedCallInput.model_validate(call_wire),
        predecessor=Absent(),
    )
    proof = readonly.RegisteredReadOnlyProof(
        registry=_head("registry"),
        tool_schema=_head("tool-schema"),
        tool_policy=_head("tool-policy"),
        implementation=_head("implementation"),
        no_mutation_proof=_head("proof"),
        proof_schema="no-mutation.v1",
        canonical_proof_bytes=b"proof",
        query_identity=_head("query"),
        canonical_query_bytes=b"query",
        snapshot=_head("snapshot"),
        snapshot_frontier=1,
        snapshot_contract=_head("snapshot-contract"),
    )
    observed = readonly.ReadOnlyLineageSnapshot(
        initialized_head=_head("initialized"),
        initialized=initialized,
        lineage=lineage,
        complete_ordered_attempts=(),
        shared_counter=_head("counter"),
        attempts_consumed=0,
        call_outcome=Absent(),
        terminal=Absent(),
        pending=Absent(),
        complete_manifest_fingerprint="a" * 64,
    )
    cut = readonly.ReadOnlyCurrentCut(
        tenant_id="tenant",
        database_id="database",
        tenant_commit_sequence=1,
        materialization_commitment="a" * 64,
        authority_registry=_head("registry"),
        sources=captured.request.cut.sources,
        applicability=captured.request.cut.fence,
    )
    attempt = readonly.PrepareReadOnlyAttempt(
        command_id="readonly-attempt",
        run=run,
        observed=observed,
        proof=proof,
        route=readonly.SameRunReadOnlyAttempt(pending=Absent()),
        cut=cut,
        fence=captured.request.cut.fence,
    )
    pending = readonly.PrepareReadOnlyPending(
        command_id="readonly-pending",
        run=run,
        observed=observed,
        current_proof=proof,
        crossed_binding_heads=(_head("changed-policy"),),
        closure_registry=_head("closure"),
        cut=cut,
        fence=captured.request.cut.fence,
    )
    _roundtrip(readonly.ReadOnlyPreparationRequest, attempt)
    _roundtrip(readonly.ReadOnlyPreparationRequest, pending)


async def test_v3_completion_model_and_terminal_work_roundtrip_public_unions() -> None:
    from tests.support.delivery_completion import _captured

    import chiplog.capabilities.agent_loop.execution_completion_contracts as completion
    import chiplog.capabilities.agent_loop.model_attempt_recovery_contracts as model
    import chiplog.capabilities.agent_loop.post_terminal_contracts as terminal_work
    from chiplog.capabilities.agent_loop.delivery_preparation import (
        DeliveryCompletion,
        prepare_completion,
    )

    run = execution_run_v3()
    cut = await _v3_cut()
    fence = (await fixture()).request.cut.fence
    _, delivery = await _captured()
    request = completion.PrepareExecutionCompletion(
        command_id="complete",
        run=run,
        selected_attempt=_head("attempt"),
        selector_generation=0,
        visibility_manifest=_head("manifest"),
        exact_captured_response=delivery.captured_response,
        cut=cut,
        complete_earlier_continuations=(),
        delivery=delivery,
        fence=fence,
    )
    _roundtrip(completion.PrepareExecutionCompletion, request)
    terminal_manifest = observations.ExecutionTerminalManifest(
        manifest_id="complete-terminal",
        command_id="complete",
        prior_run=cut.current_run,
        target="SUCCEEDED",
        source_cut_fingerprint=cut.digest(),
        complete_accounting=(),
        complete_open_original_obligations=(),
    )
    success = completion.PreparedExecutionCompletion(
        source_request_fingerprint="a" * 64,
        complete_earlier_continuations=(),
        delivery=prepare_completion(
            DeliveryCompletion.model_validate_json(delivery.captured_response), delivery
        ),
        terminal_manifest=terminal_manifest,
        run=run,
        complete_owner_commitment="b" * 64,
    )
    rejected = completion.PreparedExecutionCompletionReject(
        source_request_fingerprint="a" * 64,
        original_captured_attempt=_head("attempt"),
        preserved_trace=_head("trace"),
        visibility_manifest=_head("manifest"),
        reasons=("SCHEMA",),
        run=run,
        complete_owner_commitment="b" * 64,
    )
    _roundtrip(completion.ExecutionCompletionResult, success)
    _roundtrip(completion.ExecutionCompletionResult, rejected)

    replacement = model.ReplaceExecutionModelAttempt(
        command_id="replace",
        run=run,
        selected_attempt=_head("attempt"),
        expected_selector=0,
        no_exposure=model.RegisteredModelNoExposure(
            registry=_head("registry"),
            proof=_head("proof"),
            original_run=_head("run"),
            original_attempt=_head("attempt"),
            lineage_id="lineage-v3",
            selector_generation=0,
            immutable_request=_head("request"),
            visibility_manifest=_head("manifest"),
            provider_contract="hermetic-model.v1",
            recipient="hermetic-model",
            observed_emission_head=_head("not-emitted"),
            source_schema="pre-emission-cas.v1",
            canonical_source_bytes=b"source",
        ),
        fence=fence,
    )
    _roundtrip(model.ModelAttemptRecoveryRequest, replacement)
    replacement_result = model.PreparedModelAttemptReplacement(
        source_request_fingerprint="a" * 64,
        original_attempt=_head("original"),
        superseded_attempt=_head("superseded"),
        replacement_attempt=_head("replacement"),
        run=run,
        proposal_fingerprint="b" * 64,
    )
    _roundtrip(model.ModelAttemptRecoveryResult, replacement_result)
    work = terminal_work.PrepareTerminalWork(
        identity=terminal_work.WorkCommandIdentity(tenant_id="tenant", command_id="work"),
        terminal_run=run,
        original_terminalization_request=b"terminal-request",
        terminal_manifest=_head("terminal"),
        ordered_open_obligations=(),
    )
    _roundtrip(terminal_work.PostTerminalWorkRequest, work)
