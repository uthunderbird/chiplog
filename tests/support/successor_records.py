"""Real Phase-C successor request/result fixtures for consumer tests."""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from typing import Literal

from pydantic import TypeAdapter

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.capabilities.agent_loop.execution_history_contracts import ExecutionRunRecordV3
from chiplog.capabilities.agent_loop.execution_history_transition_contracts import (
    CreateExecutionRunV3,
)
from chiplog.capabilities.agent_loop.execution_recovery_contracts import (
    ExecutionSuccessorEdge,
    NonSchedulerSuccessor,
    PreparedExecutionSuccessor,
    PrepareExecutionSuccessor,
    SchedulerSuccessorAdvance,
)
from chiplog.capabilities.agent_loop.execution_recovery_observations import (
    ExecutionRecoveryCut,
    ExecutionSuspensionBaseline,
    ExecutionSuspensionPair,
    RecoverySourceRecord,
    SelectedExecutionSuspension,
)
from chiplog.capabilities.agent_loop.execution_run_record_contracts import (
    ExecutionRunCanonicalMember,
)
from chiplog.capabilities.agent_loop.execution_run_versions import (
    CreateExecutionRunVersion,
    ExecutionRun,
)
from chiplog.capabilities.agent_loop.execution_transition_contracts import CreateExecutionRun
from chiplog.capabilities.agent_loop.recovery_contracts import (
    Absent,
    ExecutionLineageBinding,
    ExhaustionBinding,
    FirstPublication,
    LeaseBinding,
    OriginalObligationBinding,
    PhysicalRootBinding,
    PhysicalRootRolloverFence,
    PreRootDecisionFence,
    Present,
    RecoveryDTO,
    RolloverAuthorityRef,
    SchedulerExecutionFence,
    TrustedClockProofRef,
)
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import (
    FrontierMember,
    FrozenRunBindings,
    PendingCallFrontier,
    ReadOnlyAttemptMember,
    ReadOnlyRetryLineage,
    RecoveryFrontier,
    RecoveryFrontierRegistry,
    RecoveryRegistryRow,
    ReferenceExternalObligation,
)
from chiplog.capabilities.agent_loop.recovery_record_contracts import (
    AGENT_LOOP_OWNER,
    RecoveryRecordMember,
)
from chiplog.capabilities.agent_loop.scheduler_contracts import (
    DecideIntervalCommand,
    DueCoordinate,
    ExhaustedLease,
    HeldLease,
    MissedOccurrencePolicyHead,
    PhysicalRootRolloverCommand,
    ScheduleDefinitionHead,
    SchedulerCommandIdentity,
    SchedulerEligibilityBoundary,
    SchedulerIntervalBound,
    SchedulerIntervalBoundHead,
    SchedulerLineageView,
)
from chiplog.capabilities.agent_loop.scheduler_domain import (
    CANONICAL_VERSION,
    COORDINATE_VERSION,
    eligible_manifest,
    undisposed_occurrence,
)
from chiplog.capabilities.agent_loop.scheduler_materialization import (
    SchedulerCanonicalMember,
    SchedulerRunInputs,
    prepare_interval,
)
from chiplog.capabilities.agent_loop.scheduler_rollover import (
    IssuedRolloverObservation,
    prepare_rollover,
    rollover_payload_fingerprint,
    rollover_snapshot_fingerprint,
)
from chiplog.capabilities.agent_loop.successor_ancestry_contracts import (
    ReadSuccessorRunAncestryResultV1,
    ReadSuccessorRunAncestryV1,
    SelectedSuccessorRunSourceV1,
    SuccessorAncestryCutV1,
    successor_ancestry_request_fingerprint,
)
from chiplog.capabilities.agent_loop.successor_record_contracts import (
    SCHEDULER_EPOCH_OBSERVATION_SCHEMA,
    SCHEDULER_LINEAGE_ADVANCE_SCHEMA,
    SUCCESSOR_INITIALIZATION_SCHEMA,
    EpochCreationLineageWitness,
    ExecutionSuccessorInitializationRecord,
    PriorSuccessorObservationLineageWitness,
    RetainedSuccessorInput,
    SchedulerSuccessorEpochObservationRecord,
    SchedulerSuccessorInputs,
    SchedulerSuccessorLineageAdvanceRecord,
    decode_retained_successor_input,
    decode_successor_record_member,
)
from tests.support.execution_fan_out import fixture as captured_fixture
from tests.support.execution_versions import execution_run_v3
from tests.support.scheduler_execution import endpoint_selection as scheduler_endpoint_selection


def present(name: str) -> Present:
    return Present(head="physical:" + name, fingerprint=hashlib.sha256(name.encode()).hexdigest())


def head(name: str) -> CallSubjectHead:
    return CallSubjectHead(subject_id=name, revision=present(name))


def run_head(run: ExecutionRunRecord | ExecutionRunRecordV3) -> CallSubjectHead:
    return CallSubjectHead(
        subject_id=run.run_id,
        revision=Present(head=run.head, fingerprint=run.digest()),
    )


def content_member(kind: str, schema_id: str, record: RecoveryDTO) -> RecoveryRecordMember:
    raw = record.canonical_bytes()
    fingerprint = hashlib.sha256(raw).hexdigest()
    return RecoveryRecordMember(
        owner=AGENT_LOOP_OWNER,
        record_kind=kind,
        schema_id=schema_id,
        record_id=schema_id + ":" + fingerprint,
        canonical_record_bytes=raw,
        fingerprint=fingerprint,
    )


def suspension_baseline_member(baseline: ExecutionSuspensionBaseline) -> RecoveryRecordMember:
    raw = baseline.canonical_bytes()
    return RecoveryRecordMember(
        owner=AGENT_LOOP_OWNER,
        record_kind="SUSPENSION_BASELINE",
        schema_id="chiplog.execution.suspension-baseline.v2",
        record_id=baseline.baseline_id,
        canonical_record_bytes=raw,
        fingerprint=hashlib.sha256(raw).hexdigest(),
    )


def bindings() -> FrozenRunBindings:
    return FrozenRunBindings(
        objective="objective",
        requested_work="work",
        prompt_artifact=present("prompt"),
        ordered_tool_specs=(present("tool"),),
        generated_schema=present("schema"),
        semantic_bindings=(),
        recipient_effect_bindings=(),
        authority_scope=present("scope"),
        authority_mandate_heads=(),
        policy=present("policy"),
        no_retry_boundaries=(),
    )


def obligation() -> ReferenceExternalObligation:
    return ReferenceExternalObligation(
        original=OriginalObligationBinding(
            original_run_id="run",
            original_call_id="call",
            obligation_id="obligation",
            obligation_stream_id="stream",
            obligation_head="obligation-head",
            closure_predicate_id="predicate",
            closure_predicate_version="1",
            resolver_id="resolver",
            resolver_version="1",
            reducer_id="reducer",
            reducer_version="1",
            evidence_stream_id="evidence",
            evidence_head=present("evidence"),
        ),
        observation_frontier=1,
    )


def pending() -> PendingCallFrontier:
    return PendingCallFrontier(
        original_call_id="call",
        response_id="response",
        pending=present("pending"),
        terminal=Absent(),
        call_outcome=Absent(),
        initialized=present("initialized"),
        lineage=ReadOnlyRetryLineage(
            lineage_id="lineage",
            original_call_id="call",
            max_attempts=2,
            budget_version="1",
            reducer_id="reducer",
            reducer_version="1",
        ),
        ordered_attempts=(
            ReadOnlyAttemptMember(
                ordinal=0,
                attempt_id="attempt",
                initialized=present("initialized"),
                accepted=present("accepted"),
                outcome=Absent(),
                result_or_obligation=Absent(),
                predecessor=Absent(),
            ),
        ),
        last_retryable_failure=present("failure"),
        counter=present("counter"),
        attempts_consumed=1,
        next_ordinal=1,
        readonly_proof=present("readonly"),
        snapshot=present("snapshot"),
        execution_binding=Absent(),
        crossed_binding_heads=(),
        closure_registry_id="registry",
        closure_registry_version="1",
    )


@dataclass(frozen=True)
class NonSchedulerSuccessorFixture:
    request: PrepareExecutionSuccessor
    result: PreparedExecutionSuccessor
    edge_member: RecoveryRecordMember
    baseline_member: RecoveryRecordMember
    pair_member: RecoveryRecordMember
    initialization_member: RecoveryRecordMember


@dataclass(frozen=True)
class SchedulerSuccessorFixture:
    request: PrepareExecutionSuccessor
    result: PreparedExecutionSuccessor
    edge_member: RecoveryRecordMember
    baseline_member: RecoveryRecordMember
    pair_member: RecoveryRecordMember
    initialization_member: RecoveryRecordMember
    scheduler_members: tuple[RecoveryRecordMember, RecoveryRecordMember]
    scheduler_inputs: SchedulerSuccessorInputs


@dataclass(frozen=True)
class RepeatedSchedulerSuccessorFixture:
    first: SchedulerSuccessorFixture
    second: SchedulerSuccessorFixture
    historical_baseline_member: RecoveryRecordMember


async def non_scheduler_successor() -> NonSchedulerSuccessorFixture:
    captured = await captured_fixture()
    active = captured.captured_run
    frontier = RecoveryFrontier(
        tenant_id="tenant",
        run_id=active.run_id,
        tenant_commit_sequence=1,
        registry=RecoveryFrontierRegistry(
            registry_id="registry",
            version="1",
            fingerprint="a" * 64,
            ordered_rows=(
                RecoveryRegistryRow(
                    family="RUN",
                    ordinal=0,
                    subject_extractor_id="run",
                    cardinality_rule="one",
                    terminal_conflict_rule="reject",
                    serialization_rule="canonical",
                    canonicalization_version="1",
                ),
            ),
        ),
        ordered_members=(
            FrontierMember(
                family="RUN",
                subject=active.run_id,
                branch="ACTIVE",
                ordered_heads=(run_head(active).revision,),
                fingerprint="b" * 64,
            ),
        ),
        ordered_calls=(),
        canonicalization_version="chiplog.recovery.frontier.v1",
        fingerprint="c" * 64,
    )
    cut = ExecutionRecoveryCut(
        tenant_id="tenant",
        database_id="database",
        tenant_commit_sequence=1,
        materialization_commitment="d" * 64,
        current_run=run_head(active),
        complete_ordered_run_lineage=(active,),
        complete_ordered_responses=(),
        frontier=frontier,
        current_bindings=bindings(),
        original_closures=(),
        current_reductions=(),
        complete_sources=(
            RecoverySourceRecord(
                owner="agent_loop",
                subject=head("source"),
                schema_id="source.v1",
                canonical_record_bytes=b"source",
                selected_decision=head("source-decision"),
                physical_record=head("source-physical"),
            ),
        ),
        complete_causal_changes=(),
        complete_inventory_fingerprint="e" * 64,
    )
    baseline = ExecutionSuspensionBaseline(
        baseline_id="baseline",
        suspension_command_id="suspend",
        run_id=active.run_id,
        predecessor_run=cut.current_run,
        source_cut_fingerprint=cut.digest(),
        frontier=frontier,
        bindings=cut.current_bindings,
        original_obligations=(),
        activation_blocking_predicates=(),
    )
    baseline_member = suspension_baseline_member(baseline)
    baseline_ref = CallSubjectHead(
        subject_id=baseline.baseline_id,
        revision=Present(head=baseline_member.record_id, fingerprint=baseline_member.fingerprint),
    )
    suspended = _native_scheduler_run(
        active,
        state="SUSPENDED",
        predecessor=active.head,
        suspension_baseline=baseline_ref,
        event="Suspended",
    )
    pair = ExecutionSuspensionPair(
        suspension_command_id="suspend",
        source_cut_fingerprint=cut.digest(),
        predecessor_run=cut.current_run,
        baseline=baseline_ref,
        suspended_run=run_head(suspended),
    )
    pair_member = content_member("SUSPENSION_PAIR", "chiplog.execution.suspension-pair.v2", pair)
    selected = SelectedExecutionSuspension(
        baseline=baseline,
        pair=pair,
        selected_pair=CallSubjectHead(
            subject_id="pair",
            revision=Present(head=pair_member.record_id, fingerprint=pair_member.fingerprint),
        ),
        selected_decision=head("decision"),
        canonical_pair_bytes=pair.canonical_bytes(),
    )
    cut = cut.model_copy(
        update={
            "current_run": run_head(suspended),
            "complete_ordered_run_lineage": (active, suspended),
            "frontier": cut.frontier.model_copy(
                update={"run_id": suspended.run_id, "fingerprint": "f" * 64}
            ),
        }
    )
    successor = _native_scheduler_run(
        active,
        run_id="successor",
        state="CREATED",
        predecessor=suspended.head,
        turns=(),
        event="Created",
    )
    superseded = _native_scheduler_run(
        suspended, state="SUPERSEDED", predecessor=suspended.head, event="Superseded"
    )
    fence = captured.request.cut.fence.model_copy(update={"run_head": suspended.head})
    request = PrepareExecutionSuccessor(
        command_id="successor-command",
        run=suspended,
        original_suspension=selected,
        cut=cut,
        disposition_version="1",
        successor=CreateExecutionRun(
            command_id="create-successor",
            tenant=successor.tenant,
            principal=successor.principal,
            run_id=successor.run_id,
            prompt=successor.prompt,
            policy=successor.policy,
            origin=successor.origin,
            contour_head=successor.contour_head,
            policy_head=successor.policy_head,
            worker_session=successor.worker_session,
        ),
        fence=fence,
    )
    initialization = ExecutionSuccessorInitializationRecord(
        command_id=request.command_id,
        predecessor_run=run_head(suspended),
        successor_run=run_head(successor),
        source_cut_fingerprint=cut.digest(),
        disposition_version=request.disposition_version,
        observation_frontier=1,
        current_bindings=cut.current_bindings,
        execution_fence=request.fence,
        original_obligations=(obligation(),),
        inherited_no_retry_boundaries=(head("boundary"),),
        inherited_pending_branches=(pending(),),
    )
    initialization_member = content_member(
        "SUCCESSOR_INITIALIZATION", SUCCESSOR_INITIALIZATION_SCHEMA, initialization
    )
    initialization_ref = CallSubjectHead(
        subject_id=successor.run_id,
        revision=Present(
            head=initialization_member.record_id, fingerprint=initialization_member.fingerprint
        ),
    )
    edge = ExecutionSuccessorEdge(
        command_id=request.command_id,
        original_pair=selected.selected_pair,
        predecessor_before=run_head(suspended),
        predecessor_superseded=run_head(superseded),
        successor_created=run_head(successor),
        source_cut_fingerprint=cut.digest(),
        disposition_version=request.disposition_version,
        changed_binding_manifest=(head("changed"),),
        complete_initialization=(initialization_ref,),
        original_obligations=(obligation(),),
        inherited_no_retry_boundaries=(head("boundary"),),
        inherited_pending_branches=(
            CallSubjectHead(subject_id="call", revision=pending().pending),
        ),
        observation_frontier=1,
        fence=request.fence,
    )
    edge_member = content_member("SUCCESSOR_EDGE", "chiplog.execution.successor-edge.v1", edge)
    result = PreparedExecutionSuccessor(
        source_request_fingerprint=hashlib.sha256(request.canonical_bytes()).hexdigest(),
        superseded_run=superseded,
        successor_run=successor,
        edge=edge,
        lineage_advance=NonSchedulerSuccessor(),
        complete_initialization_bytes=(initialization.canonical_bytes(),),
        complete_batch_fingerprint="a" * 64,
    )
    return NonSchedulerSuccessorFixture(
        request, result, edge_member, baseline_member, pair_member, initialization_member
    )


def _scheduler_run(
    run: ExecutionRunRecord | ExecutionRunRecordV3, **changes: object
) -> ExecutionRunRecord | ExecutionRunRecordV3:
    """Revalidate a native v2/v3 Run after making a concrete lifecycle state."""

    return type(run).model_validate(run.model_dump() | changes)


def _native_scheduler_run(
    run: ExecutionRunRecord | ExecutionRunRecordV3, **changes: object
) -> ExecutionRunRecord | ExecutionRunRecordV3:
    """Reissue one v2/v3 Run with its native self-derived head."""

    values = run.model_dump()
    values.update(changes, head="pending")
    pending = type(run).model_validate(values)
    return pending.model_copy(update={"head": "loop:" + pending.digest()})


def _run_member(run: ExecutionRunRecord | ExecutionRunRecordV3) -> ExecutionRunCanonicalMember:
    raw = run.canonical_bytes()
    return ExecutionRunCanonicalMember(
        record_id=run.head,
        schema_id=run.schema_id,
        canonical_record_bytes=raw,
        fingerprint=hashlib.sha256(raw).hexdigest(),
    )


def _retained_input(
    subject_id: str, head_id: str, schema_id: str, body: bytes
) -> RetainedSuccessorInput:
    return RetainedSuccessorInput(
        reference=CallSubjectHead(
            subject_id=subject_id,
            revision=Present(head=head_id, fingerprint=hashlib.sha256(body).hexdigest()),
        ),
        schema_id=schema_id,
        canonical_record_bytes=body,
    )


@dataclass(frozen=True)
class NativeSchedulerSources:
    lineage: ExecutionLineageBinding
    physical_root: PhysicalRootBinding
    inputs: SchedulerSuccessorInputs


def _native_scheduler_sources() -> NativeSchedulerSources:
    """Select real materialized scheduler lineage, selector, and epoch members."""

    schedule = ScheduleDefinitionHead(
        schedule_id="scheduler-schedule",
        schedule_revision="scheduler-schedule-v1",
        head="scheduler-schedule-head",
        fingerprint="7" * 64,
    )
    source_coordinate = DueCoordinate(
        coordinate_policy_version=COORDINATE_VERSION,
        canonical_coordinate="1",
    )
    boundary = SchedulerEligibilityBoundary(
        schedule_definition_head=schedule,
        missed_occurrence_policy_head=MissedOccurrencePolicyHead(
            policy_id="scheduler-policy",
            policy_revision="scheduler-policy-v1",
            head="scheduler-policy-head",
            fingerprint="8" * 64,
            kind="MATERIALIZE_EACH",
        ),
        previous_due_boundary=DueCoordinate(
            coordinate_policy_version=COORDINATE_VERSION,
            canonical_coordinate="0",
        ),
        cutoff_due_coordinate=DueCoordinate(
            coordinate_policy_version=COORDINATE_VERSION,
            canonical_coordinate="2",
        ),
        enumeration_frontier="scheduler-frontier",
        predecessor_interval=Absent(),
        canonicalization_version=CANONICAL_VERSION,
    )
    source = (undisposed_occurrence(schedule, source_coordinate, "scheduler-undisposed-head"),)
    bound = SchedulerIntervalBoundHead(
        head_id="scheduler-bound",
        predecessor=Absent(),
        generation=0,
        bound=SchedulerIntervalBound(
            max_member_count=10,
            max_manifest_bytes=100_000,
            max_serialized_batch_bytes=10_000_000,
        ),
        owner_id="agent_loop",
        authority_epoch="scheduler-authority",
        broker_generation="scheduler-broker",
        runtime_graph_generation="scheduler-runtime",
        canonicalization_version=CANONICAL_VERSION,
    )
    command = DecideIntervalCommand(
        identity=SchedulerCommandIdentity(
            command_id="scheduler-materialize",
            schema_version="1",
            canonicalization_version=CANONICAL_VERSION,
        ),
        boundary=boundary,
        bound_head=bound,
        manifest=eligible_manifest(boundary, source),
        publication_fence=PreRootDecisionFence(
            command_id="scheduler-materialize",
            disposition=FirstPublication(
                decision=Absent(), expected_canonical_absence_manifest="scheduler-absence"
            ),
            scheduler_authority_head="scheduler-authority",
            broker_generation="scheduler-broker",
            runtime_generation="scheduler-runtime",
        ),
    )
    candidate = prepare_interval(
        command,
        bound,
        source,
        SchedulerRunInputs(
            tenant="tenant",
            principal="principal",
            prompt="scheduler prompt",
            policy=BudgetPolicy(),
            origin=scheduler_endpoint_selection(),
            contour_head="contour",
            policy_head="policy",
            worker_session="worker",
            authority_epoch="scheduler-authority",
        ),
        Present(head="scheduler-enumeration", fingerprint="9" * 64),
    )
    occurrence = candidate.occurrences[0]
    members = {member.record_kind: member for member in candidate.records}

    def selected(kind: str) -> RetainedSuccessorInput:
        member: SchedulerCanonicalMember = members[kind]
        return _retained_input(
            occurrence.commitment.lineage.root_id
            if kind == "stable-lineage"
            else (
                occurrence.commitment.physical_root.selector_id
                if kind == "physical-selector"
                else occurrence.commitment.physical_root.current_epoch_id
            ),
            member.record_id,
            member.schema_id,
            base64.b64decode(member.canonical_base64),
        )

    return NativeSchedulerSources(
        lineage=occurrence.commitment.lineage,
        physical_root=occurrence.commitment.physical_root,
        inputs=SchedulerSuccessorInputs(
            original_lineage=selected("stable-lineage"),
            current_selector=selected("physical-selector"),
            selected_epoch=selected("physical-root"),
            continuity=EpochCreationLineageWitness(),
        ),
    )


def _native_rollover_sources(submission_id: str = "rollover-submission") -> NativeSchedulerSources:
    """Use scheduler_rollover's producer bytes for a newly selected physical epoch."""

    genesis = _native_scheduler_sources()
    command_id = "rollover-command-" + submission_id
    held = LeaseBinding(
        lease_head="rollover-held",
        holder_id="worker",
        holder_session_id="session",
        lease_id="rollover-lease",
        generation=2**64 - 1,
        trusted_expiry=100,
        clock_contract_version="clock.v1",
    )
    current = SchedulerLineageView(
        lineage=genesis.lineage,
        physical_root=genesis.physical_root,
        lease=HeldLease(binding=held),
        predecessor_rollover=Absent(),
    )
    exhausted = current.model_copy(
        update={
            "lease": ExhaustedLease(
                lease_head="rollover-exhausted",
                exhausted_command_id=command_id,
                trusted_expiry=100,
                authority_epoch="rollover-authority",
                preceding_held_lease=held,
            )
        }
    )
    authority = RolloverAuthorityRef(
        proof_id="rollover-proof",
        proof_fingerprint="a" * 64,
        authority_head="rollover-operator",
        command_id=command_id,
        command_payload_fingerprint="a" * 64,
        predecessor_rollover=Absent(),
    )
    command = PhysicalRootRolloverCommand(
        identity=SchedulerCommandIdentity(
            command_id=command_id, schema_version="1", canonicalization_version="1"
        ),
        fence=PhysicalRootRolloverFence(
            lineage=exhausted.lineage,
            physical_root=exhausted.physical_root,
            exhaustion=ExhaustionBinding(
                hold_head="rollover-exhausted",
                exhausted_command_id=command_id,
                lease=held,
                authority_epoch="rollover-authority",
            ),
            authority=authority,
        ),
    )
    authority = authority.model_copy(
        update={"command_payload_fingerprint": rollover_payload_fingerprint(command)}
    )
    command = command.model_copy(
        update={"fence": command.fence.model_copy(update={"authority": authority})}
    )
    candidate = prepare_rollover(
        exhausted,
        command,
        IssuedRolloverObservation(
            authority=authority,
            snapshot_fingerprint=rollover_snapshot_fingerprint(exhausted),
            submission_id=submission_id,
            authority_epoch="rollover-authority",
            used_epoch_ids=(exhausted.physical_root.current_epoch_id,),
            used_lease_heads=(held.lease_head, "rollover-exhausted"),
        ),
        submission_id=submission_id,
    )
    records = {row.record_kind: row for row in candidate.records}

    def retained(kind: str, subject: str) -> RetainedSuccessorInput:
        row = records[kind]
        return _retained_input(
            subject, row.record_id, row.schema_id, base64.b64decode(row.canonical_base64)
        )

    return NativeSchedulerSources(
        lineage=candidate.view.lineage,
        physical_root=candidate.view.physical_root,
        inputs=SchedulerSuccessorInputs(
            original_lineage=genesis.inputs.original_lineage,
            current_selector=retained(
                "scheduler.rollover-physical-selector", candidate.view.physical_root.selector_id
            ),
            selected_epoch=retained(
                "scheduler.rollover-physical-root", candidate.view.physical_root.current_epoch_id
            ),
            continuity=EpochCreationLineageWitness(),
        ),
    )


async def scheduler_successor(
    version: Literal["v2", "v3"], sources: NativeSchedulerSources | None = None
) -> SchedulerSuccessorFixture:
    """Build a complete scheduler successor graph from native versioned Runs."""

    base = await non_scheduler_successor()
    sources = _native_scheduler_sources() if sources is None else sources
    root = sources.physical_root
    lease = LeaseBinding(
        lease_head="scheduler-lease-head",
        holder_id="scheduler-holder",
        holder_session_id="scheduler-session",
        lease_id="scheduler-lease",
        generation=8,
        trusted_expiry=9,
        clock_contract_version="clock.v1",
    )
    lineage = sources.lineage
    root_binding = {
        "kind": "SCHEDULER_LINEAGE",
        "root_id": lineage.root_id,
        "subject_canonical_base64": base64.b64encode(lineage.subject.canonical_bytes()).decode(),
        "subject_schema_version": "chiplog.execution-lineage-subject.v1",
        "root_fingerprint": lineage.root_fingerprint,
        "initial_run_id": lineage.initial_run_id,
    }
    seed: ExecutionRunRecord | ExecutionRunRecordV3
    if version == "v2":
        seed = base.request.cut.complete_ordered_run_lineage[0]
    else:
        seed = execution_run_v3()
    active = _native_scheduler_run(seed, root_binding=root_binding)
    suspension_cut = base.request.cut.model_copy(
        update={"current_run": run_head(active), "complete_ordered_run_lineage": (active,)}
    )
    baseline = ExecutionSuspensionBaseline(
        baseline_id="scheduler-baseline",
        suspension_command_id="scheduler-suspend",
        run_id=lineage.current_run_id,
        predecessor_run=suspension_cut.current_run,
        source_cut_fingerprint=suspension_cut.digest(),
        frontier=suspension_cut.frontier,
        bindings=suspension_cut.current_bindings,
        original_obligations=(),
        activation_blocking_predicates=(),
    )
    baseline_member = suspension_baseline_member(baseline)
    baseline_ref = CallSubjectHead(
        subject_id=baseline.baseline_id,
        revision=Present(head=baseline_member.record_id, fingerprint=baseline_member.fingerprint),
    )
    suspended = _native_scheduler_run(
        active,
        run_id=lineage.current_run_id,
        state="SUSPENDED",
        head="scheduler-suspended-head",
        predecessor=active.head,
        suspension_baseline=baseline_ref,
        event="Suspended",
    )
    cut = suspension_cut.model_copy(
        update={
            "current_run": run_head(suspended),
            "complete_ordered_run_lineage": (active, suspended),
            "frontier": suspension_cut.frontier.model_copy(
                update={"run_id": suspended.run_id, "fingerprint": "e" * 64}
            ),
        }
    )
    successor = _native_scheduler_run(
        active,
        run_id="scheduler-successor-run",
        state="CREATED",
        head="scheduler-successor-head",
        predecessor=suspended.head,
        turns=(),
        suspension_baseline=None,
        event="Created",
    )
    superseded = _native_scheduler_run(
        suspended,
        state="SUPERSEDED",
        predecessor=suspended.head,
        head="scheduler-superseded-head",
        event="Superseded",
    )
    fence = SchedulerExecutionFence(
        run_head=suspended.head,
        lineage=lineage,
        physical_root=root,
        lease=lease,
        clock_proof=TrustedClockProofRef(
            proof_id="scheduler-clock-proof",
            proof_fingerprint="2" * 64,
            proof_version="clock-proof.v1",
            clock_contract_version=lease.clock_contract_version,
            fence_fingerprint="3" * 64,
            command_id="successor-command",
            command_payload_fingerprint="4" * 64,
            submission_id="scheduler-submission",
        ),
    )
    pair = ExecutionSuspensionPair(
        suspension_command_id=baseline.suspension_command_id,
        source_cut_fingerprint=suspension_cut.digest(),
        predecessor_run=suspension_cut.current_run,
        baseline=baseline_ref,
        suspended_run=run_head(suspended),
    )
    pair_member = content_member("SUSPENSION_PAIR", "chiplog.execution.suspension-pair.v2", pair)
    selected = SelectedExecutionSuspension(
        baseline=baseline,
        pair=pair,
        selected_pair=CallSubjectHead(
            subject_id="scheduler-pair",
            revision=Present(head=pair_member.record_id, fingerprint=pair_member.fingerprint),
        ),
        selected_decision=head("scheduler-suspension-decision"),
        canonical_pair_bytes=pair.canonical_bytes(),
    )
    if version == "v2":
        create: CreateExecutionRunVersion = CreateExecutionRun(
            command_id="create-scheduler-successor",
            tenant=successor.tenant,
            principal=successor.principal,
            run_id=successor.run_id,
            prompt=successor.prompt,
            policy=successor.policy,
            origin=successor.origin,
            contour_head=successor.contour_head,
            policy_head=successor.policy_head,
            worker_session=successor.worker_session,
        )
    else:
        create = CreateExecutionRunV3(
            command_id="create-scheduler-successor",
            tenant=successor.tenant,
            principal=successor.principal,
            run_id=successor.run_id,
            prompt=successor.prompt,
            policy=successor.policy,
            origin=successor.origin,
            contour_head=successor.contour_head,
            policy_head=successor.policy_head,
            worker_session=successor.worker_session,
        )
    request = PrepareExecutionSuccessor(
        command_id="successor-command",
        run=suspended,
        original_suspension=selected,
        cut=cut,
        disposition_version="scheduler-successor-v1",
        successor=create,
        fence=fence,
    )
    initialization = ExecutionSuccessorInitializationRecord(
        command_id=request.command_id,
        predecessor_run=run_head(suspended),
        successor_run=run_head(successor),
        source_cut_fingerprint=cut.digest(),
        disposition_version=request.disposition_version,
        observation_frontier=1,
        current_bindings=cut.current_bindings,
        execution_fence=fence,
        original_obligations=(obligation(),),
        inherited_no_retry_boundaries=(head("boundary"),),
        inherited_pending_branches=(pending(),),
    )
    initialization_member = content_member(
        "SUCCESSOR_INITIALIZATION", SUCCESSOR_INITIALIZATION_SCHEMA, initialization
    )
    initialization_ref = CallSubjectHead(
        subject_id=successor.run_id,
        revision=Present(
            head=initialization_member.record_id, fingerprint=initialization_member.fingerprint
        ),
    )
    scheduler_inputs = sources.inputs
    lineage_record = SchedulerSuccessorLineageAdvanceRecord(
        command_id=request.command_id,
        predecessor_run=run_head(suspended),
        successor_run=run_head(successor),
        previous_lineage=lineage,
        next_current_run_id=successor.run_id,
        physical_root=root,
        lease=lease,
        source_cut_fingerprint=cut.digest(),
    )
    lineage_member = content_member(
        "SCHEDULER_LINEAGE_ADVANCE", SCHEDULER_LINEAGE_ADVANCE_SCHEMA, lineage_record
    )
    epoch_record = SchedulerSuccessorEpochObservationRecord(
        command_id=request.command_id,
        predecessor_run=run_head(suspended),
        successor_run=run_head(successor),
        physical_root=root,
        lease=lease,
        previous_lineage=scheduler_inputs.original_lineage.reference,
        advanced_lineage=CallSubjectHead(
            subject_id=lineage.root_id,
            revision=Present(head=lineage_member.record_id, fingerprint=lineage_member.fingerprint),
        ),
        source_cut_fingerprint=cut.digest(),
    )
    epoch_member = content_member(
        "SCHEDULER_EPOCH_LINEAGE_OBSERVATION", SCHEDULER_EPOCH_OBSERVATION_SCHEMA, epoch_record
    )
    advance = SchedulerSuccessorAdvance(
        original_lineage=scheduler_inputs.original_lineage.reference,
        current_selector=scheduler_inputs.current_selector.reference,
        selected_epoch=scheduler_inputs.selected_epoch.reference,
        lineage_schema=SCHEDULER_LINEAGE_ADVANCE_SCHEMA,
        canonical_lineage_bytes=lineage_member.canonical_record_bytes,
        epoch_observation_schema=SCHEDULER_EPOCH_OBSERVATION_SCHEMA,
        canonical_epoch_observation_bytes=epoch_member.canonical_record_bytes,
    )
    edge = ExecutionSuccessorEdge(
        command_id=request.command_id,
        original_pair=selected.selected_pair,
        predecessor_before=run_head(suspended),
        predecessor_superseded=run_head(superseded),
        successor_created=run_head(successor),
        source_cut_fingerprint=cut.digest(),
        disposition_version=request.disposition_version,
        changed_binding_manifest=(head("scheduler-changed"),),
        complete_initialization=(initialization_ref,),
        original_obligations=(obligation(),),
        inherited_no_retry_boundaries=(head("boundary"),),
        inherited_pending_branches=(
            CallSubjectHead(subject_id="call", revision=pending().pending),
        ),
        observation_frontier=1,
        fence=fence,
    )
    edge_member = content_member("SUCCESSOR_EDGE", "chiplog.execution.successor-edge.v1", edge)
    result = PreparedExecutionSuccessor(
        source_request_fingerprint=hashlib.sha256(request.canonical_bytes()).hexdigest(),
        superseded_run=superseded,
        successor_run=successor,
        edge=edge,
        lineage_advance=advance,
        complete_initialization_bytes=(initialization.canonical_bytes(),),
        complete_batch_fingerprint="6" * 64,
    )
    # Public discriminated boundaries receive native serialized v2/v3 values.
    assert TypeAdapter(ExecutionRun).validate_json(suspended.canonical_bytes()) == suspended
    assert TypeAdapter(ExecutionRun).validate_json(successor.canonical_bytes()) == successor
    assert TypeAdapter(CreateExecutionRunVersion).validate_json(create.canonical_bytes()) == create
    return SchedulerSuccessorFixture(
        request,
        result,
        edge_member,
        baseline_member,
        pair_member,
        initialization_member,
        (lineage_member, epoch_member),
        scheduler_inputs,
    )


async def repeated_scheduler_successors(
    version: Literal["v2", "v3"],
    source: Literal["genesis", "rollover", "rollover-alt"] = "genesis",
) -> RepeatedSchedulerSuccessorFixture:
    """Build two successors with a retained native Run-B ancestry exchange."""

    first = await scheduler_successor(
        version,
        (
            _native_rollover_sources("rollover-submission-alt")
            if source == "rollover-alt"
            else (_native_rollover_sources() if source == "rollover" else None)
        ),
    )
    prior_member, _prior_observation = first.scheduler_members
    original_successor = _native_scheduler_run(first.result.successor_run)
    first_initialization = ExecutionSuccessorInitializationRecord(
        command_id=first.request.command_id,
        predecessor_run=run_head(first.request.run),
        successor_run=run_head(original_successor),
        source_cut_fingerprint=first.request.cut.digest(),
        disposition_version=first.request.disposition_version,
        observation_frontier=1,
        current_bindings=first.request.cut.current_bindings,
        execution_fence=first.request.fence,
        original_obligations=(obligation(),),
        inherited_no_retry_boundaries=(head("boundary"),),
        inherited_pending_branches=(pending(),),
    )
    first_initialization_member = content_member(
        "SUCCESSOR_INITIALIZATION", SUCCESSOR_INITIALIZATION_SCHEMA, first_initialization
    )
    first_initialization_ref = CallSubjectHead(
        subject_id=original_successor.run_id,
        revision=Present(
            head=first_initialization_member.record_id,
            fingerprint=first_initialization_member.fingerprint,
        ),
    )
    previous_advance = decode_successor_record_member(prior_member).record
    assert isinstance(previous_advance, SchedulerSuccessorLineageAdvanceRecord)
    first_lineage = SchedulerSuccessorLineageAdvanceRecord(
        command_id=previous_advance.command_id,
        predecessor_run=previous_advance.predecessor_run,
        successor_run=run_head(original_successor),
        previous_lineage=previous_advance.previous_lineage,
        next_current_run_id=original_successor.run_id,
        physical_root=previous_advance.physical_root,
        lease=previous_advance.lease,
        source_cut_fingerprint=previous_advance.source_cut_fingerprint,
    )
    first_lineage_member = content_member(
        "SCHEDULER_LINEAGE_ADVANCE", SCHEDULER_LINEAGE_ADVANCE_SCHEMA, first_lineage
    )
    first_epoch = SchedulerSuccessorEpochObservationRecord(
        command_id=first_lineage.command_id,
        predecessor_run=first_lineage.predecessor_run,
        successor_run=first_lineage.successor_run,
        physical_root=first_lineage.physical_root,
        lease=first_lineage.lease,
        previous_lineage=first.scheduler_inputs.original_lineage.reference,
        advanced_lineage=CallSubjectHead(
            subject_id=first_lineage.previous_lineage.root_id,
            revision=Present(
                head=first_lineage_member.record_id,
                fingerprint=first_lineage_member.fingerprint,
            ),
        ),
        source_cut_fingerprint=first_lineage.source_cut_fingerprint,
    )
    first_epoch_member = content_member(
        "SCHEDULER_EPOCH_LINEAGE_OBSERVATION", SCHEDULER_EPOCH_OBSERVATION_SCHEMA, first_epoch
    )
    first_advance = first.result.lineage_advance.model_copy(
        update={
            "canonical_lineage_bytes": first_lineage_member.canonical_record_bytes,
            "canonical_epoch_observation_bytes": first_epoch_member.canonical_record_bytes,
        }
    )
    first_edge = first.result.edge.model_copy(
        update={
            "successor_created": run_head(original_successor),
            "complete_initialization": (first_initialization_ref,),
        }
    )
    first_edge_member = content_member(
        "SUCCESSOR_EDGE", "chiplog.execution.successor-edge.v1", first_edge
    )
    first_result = first.result.model_copy(
        update={
            "successor_run": original_successor,
            "edge": first_edge,
            "lineage_advance": first_advance,
            "complete_initialization_bytes": (first_initialization.canonical_bytes(),),
        }
    )
    first = SchedulerSuccessorFixture(
        first.request,
        first_result,
        first_edge_member,
        first.baseline_member,
        first.pair_member,
        first_initialization_member,
        (first_lineage_member, first_epoch_member),
        first.scheduler_inputs,
    )

    first_fence_lineage = first.request.fence.lineage
    assert isinstance(first_fence_lineage, ExecutionLineageBinding)
    prior_input = _retained_input(
        first_fence_lineage.root_id,
        first_lineage_member.record_id,
        first_lineage_member.schema_id,
        first_lineage_member.canonical_record_bytes,
    )
    decoded_prior = decode_retained_successor_input(prior_input, "original_lineage")
    assert decoded_prior.lineage is not None
    lineage = decoded_prior.lineage
    root = first.request.fence.physical_root
    lease = first.request.fence.lease
    assert isinstance(root, PhysicalRootBinding)
    assert isinstance(lease, LeaseBinding)
    active = _native_scheduler_run(
        original_successor,
        state="ACTIVE",
        predecessor=original_successor.head,
        event="RunActivated",
    )
    captured_source: ExecutionRunRecord | ExecutionRunRecordV3
    if version == "v2":
        captured_source = (await captured_fixture()).captured_run
    else:
        captured_source = execution_run_v3()
    captured = _native_scheduler_run(
        active,
        predecessor=active.head,
        event="ModelResponseCaptured",
        turns=captured_source.turns,
    )
    suspension_cut = first.request.cut.model_copy(
        update={
            "tenant_commit_sequence": 4,
            "current_run": run_head(captured),
            "complete_ordered_run_lineage": (original_successor, active, captured),
        }
    )
    baseline = ExecutionSuspensionBaseline(
        baseline_id="second-scheduler-baseline",
        suspension_command_id="second-scheduler-suspend",
        run_id=captured.run_id,
        predecessor_run=suspension_cut.current_run,
        source_cut_fingerprint=suspension_cut.digest(),
        frontier=suspension_cut.frontier,
        bindings=suspension_cut.current_bindings,
        original_obligations=(),
        activation_blocking_predicates=(),
    )
    baseline_member = suspension_baseline_member(baseline)
    baseline_ref = CallSubjectHead(
        subject_id=baseline.baseline_id,
        revision=Present(head=baseline_member.record_id, fingerprint=baseline_member.fingerprint),
    )
    suspended = _native_scheduler_run(
        captured,
        state="SUSPENDED",
        predecessor=captured.head,
        suspension_baseline=baseline_ref,
        event="Suspended",
    )
    successor = _native_scheduler_run(
        original_successor,
        run_id="second-scheduler-successor-run",
        state="CREATED",
        predecessor=suspended.head,
        turns=(),
        suspension_baseline=None,
        event="Created",
    )
    superseded = _native_scheduler_run(
        suspended,
        state="SUPERSEDED",
        predecessor=suspended.head,
        event="Superseded",
    )
    fence = SchedulerExecutionFence(
        run_head=suspended.head,
        lineage=lineage,
        physical_root=root,
        lease=lease,
        clock_proof=TrustedClockProofRef(
            proof_id="second-scheduler-clock-proof",
            proof_fingerprint="a" * 64,
            proof_version="clock-proof.v1",
            clock_contract_version=lease.clock_contract_version,
            fence_fingerprint="b" * 64,
            command_id="second-successor-command",
            command_payload_fingerprint="c" * 64,
            submission_id="second-scheduler-submission",
        ),
    )
    pair = ExecutionSuspensionPair(
        suspension_command_id=baseline.suspension_command_id,
        source_cut_fingerprint=suspension_cut.digest(),
        predecessor_run=suspension_cut.current_run,
        baseline=baseline_ref,
        suspended_run=run_head(suspended),
    )
    pair_member = content_member("SUSPENSION_PAIR", "chiplog.execution.suspension-pair.v2", pair)
    selected = SelectedExecutionSuspension(
        baseline=baseline,
        pair=pair,
        selected_pair=CallSubjectHead(
            subject_id="second-scheduler-pair",
            revision=Present(head=pair_member.record_id, fingerprint=pair_member.fingerprint),
        ),
        selected_decision=head("second-scheduler-suspension-decision"),
        canonical_pair_bytes=pair.canonical_bytes(),
    )
    recovery_cut = suspension_cut.model_copy(
        update={
            "tenant_commit_sequence": 5,
            "materialization_commitment": "e" * 64,
            "current_run": run_head(suspended),
            "complete_ordered_run_lineage": (original_successor, active, captured, suspended),
            "frontier": suspension_cut.frontier.model_copy(
                update={
                    "run_id": suspended.run_id,
                    "tenant_commit_sequence": 5,
                    "fingerprint": "f" * 64,
                }
            ),
        }
    )
    if isinstance(successor, ExecutionRunRecord):
        create: CreateExecutionRunVersion = CreateExecutionRun(
            command_id="create-second-scheduler-successor",
            tenant=successor.tenant,
            principal=successor.principal,
            run_id=successor.run_id,
            prompt=successor.prompt,
            policy=successor.policy,
            origin=successor.origin,
            contour_head=successor.contour_head,
            policy_head=successor.policy_head,
            worker_session=successor.worker_session,
        )
    else:
        create = CreateExecutionRunV3(
            command_id="create-second-scheduler-successor",
            tenant=successor.tenant,
            principal=successor.principal,
            run_id=successor.run_id,
            prompt=successor.prompt,
            policy=successor.policy,
            origin=successor.origin,
            contour_head=successor.contour_head,
            policy_head=successor.policy_head,
            worker_session=successor.worker_session,
        )
    request = PrepareExecutionSuccessor(
        command_id="second-successor-command",
        run=suspended,
        original_suspension=selected,
        cut=recovery_cut,
        disposition_version="scheduler-successor-v1",
        successor=create,
        fence=fence,
    )
    initialization = ExecutionSuccessorInitializationRecord(
        command_id=request.command_id,
        predecessor_run=run_head(suspended),
        successor_run=run_head(successor),
        source_cut_fingerprint=recovery_cut.digest(),
        disposition_version=request.disposition_version,
        observation_frontier=1,
        current_bindings=recovery_cut.current_bindings,
        execution_fence=fence,
        original_obligations=(obligation(),),
        inherited_no_retry_boundaries=(head("boundary"),),
        inherited_pending_branches=(pending(),),
    )
    initialization_member = content_member(
        "SUCCESSOR_INITIALIZATION", SUCCESSOR_INITIALIZATION_SCHEMA, initialization
    )
    initialization_ref = CallSubjectHead(
        subject_id=successor.run_id,
        revision=Present(
            head=initialization_member.record_id, fingerprint=initialization_member.fingerprint
        ),
    )
    prior_observation_ref = CallSubjectHead(
        subject_id=first_epoch_member.record_id,
        revision=Present(
            head=first_epoch_member.record_id, fingerprint=first_epoch_member.fingerprint
        ),
    )
    ancestry_cut = SuccessorAncestryCutV1(
        tenant_id=recovery_cut.tenant_id,
        database_id=recovery_cut.database_id,
        tenant_commit_sequence=recovery_cut.tenant_commit_sequence,
        materialization_commitment=recovery_cut.materialization_commitment,
        independent_journal_head=head("second-scheduler-journal"),
    )
    ancestry_request = ReadSuccessorRunAncestryV1(
        command_id=request.command_id,
        cut=ancestry_cut,
        prior_epoch_observation=prior_observation_ref,
        prior_lineage_advance=prior_input.reference,
        start_run=run_head(original_successor),
        end_run=run_head(suspended),
    )
    prior_decision = head("first-successor-decision")
    ancestry = ReadSuccessorRunAncestryResultV1(
        source_request_fingerprint=successor_ancestry_request_fingerprint(ancestry_request),
        cut=ancestry_cut,
        prior_epoch_observation=prior_observation_ref,
        prior_lineage_advance=prior_input.reference,
        prior_successor_selected_decision=prior_decision,
        prior_successor_commit_sequence=1,
        ordered_runs=tuple(
            SelectedSuccessorRunSourceV1(
                member=_run_member(run),
                selected_decision=prior_decision
                if sequence == 1
                else head(f"second-run-decision-{sequence}"),
                selected_commit_sequence=sequence,
            )
            for sequence, run in enumerate(
                (original_successor, active, captured, suspended), start=1
            )
        ),
    )
    inputs = SchedulerSuccessorInputs(
        original_lineage=prior_input,
        current_selector=first.scheduler_inputs.current_selector,
        selected_epoch=first.scheduler_inputs.selected_epoch,
        continuity=PriorSuccessorObservationLineageWitness(
            observation=first_epoch_member,
            ancestry_request=ancestry_request,
            ancestry=ancestry,
        ),
    )
    lineage_record = SchedulerSuccessorLineageAdvanceRecord(
        command_id=request.command_id,
        predecessor_run=run_head(suspended),
        successor_run=run_head(successor),
        previous_lineage=lineage,
        next_current_run_id=successor.run_id,
        physical_root=root,
        lease=lease,
        source_cut_fingerprint=recovery_cut.digest(),
    )
    lineage_member = content_member(
        "SCHEDULER_LINEAGE_ADVANCE", SCHEDULER_LINEAGE_ADVANCE_SCHEMA, lineage_record
    )
    epoch_record = SchedulerSuccessorEpochObservationRecord(
        command_id=request.command_id,
        predecessor_run=run_head(suspended),
        successor_run=run_head(successor),
        physical_root=root,
        lease=lease,
        previous_lineage=prior_input.reference,
        advanced_lineage=CallSubjectHead(
            subject_id=lineage.root_id,
            revision=Present(head=lineage_member.record_id, fingerprint=lineage_member.fingerprint),
        ),
        source_cut_fingerprint=recovery_cut.digest(),
    )
    epoch_member = content_member(
        "SCHEDULER_EPOCH_LINEAGE_OBSERVATION", SCHEDULER_EPOCH_OBSERVATION_SCHEMA, epoch_record
    )
    advance = SchedulerSuccessorAdvance(
        original_lineage=prior_input.reference,
        current_selector=inputs.current_selector.reference,
        selected_epoch=inputs.selected_epoch.reference,
        lineage_schema=SCHEDULER_LINEAGE_ADVANCE_SCHEMA,
        canonical_lineage_bytes=lineage_member.canonical_record_bytes,
        epoch_observation_schema=SCHEDULER_EPOCH_OBSERVATION_SCHEMA,
        canonical_epoch_observation_bytes=epoch_member.canonical_record_bytes,
    )
    edge = ExecutionSuccessorEdge(
        command_id=request.command_id,
        original_pair=selected.selected_pair,
        predecessor_before=run_head(suspended),
        predecessor_superseded=run_head(superseded),
        successor_created=run_head(successor),
        source_cut_fingerprint=recovery_cut.digest(),
        disposition_version=request.disposition_version,
        changed_binding_manifest=(head("second-scheduler-changed"),),
        complete_initialization=(initialization_ref,),
        original_obligations=(obligation(),),
        inherited_no_retry_boundaries=(head("boundary"),),
        inherited_pending_branches=(
            CallSubjectHead(subject_id="call", revision=pending().pending),
        ),
        observation_frontier=1,
        fence=fence,
    )
    edge_member = content_member("SUCCESSOR_EDGE", "chiplog.execution.successor-edge.v1", edge)
    result = PreparedExecutionSuccessor(
        source_request_fingerprint=hashlib.sha256(request.canonical_bytes()).hexdigest(),
        superseded_run=superseded,
        successor_run=successor,
        edge=edge,
        lineage_advance=advance,
        complete_initialization_bytes=(initialization.canonical_bytes(),),
        complete_batch_fingerprint="d" * 64,
    )
    return RepeatedSchedulerSuccessorFixture(
        first=first,
        second=SchedulerSuccessorFixture(
            request,
            result,
            edge_member,
            baseline_member,
            pair_member,
            initialization_member,
            (lineage_member, epoch_member),
            inputs,
        ),
        historical_baseline_member=baseline_member,
    )
