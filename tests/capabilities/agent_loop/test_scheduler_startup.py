"""Selected immutable bytes, not historical command preparation, drive startup."""

import base64
import hashlib
import json
from typing import Literal

import pytest

from chiplog.capabilities.agent_loop.contracts import BudgetPolicy, EndpointSelection
from chiplog.capabilities.agent_loop.recovery_contracts import (
    Absent,
    ExhaustionBinding,
    FirstPublication,
    PhysicalRootRolloverFence,
    PreRootDecisionFence,
    Present,
    RolloverAuthorityRef,
)
from chiplog.capabilities.agent_loop.scheduler_configuration import (
    ConfigurationSnapshot,
    FixedIntervalDefinition,
    PolicyRevision,
    ScheduleRevision,
    amend,
    enumerate_definition,
    genesis,
    policy_head,
    replace_bound,
    schedule_head,
)
from chiplog.capabilities.agent_loop.scheduler_contracts import (
    DecideIntervalCommand,
    DueCoordinate,
    ExhaustedLease,
    PhysicalRootRolloverCommand,
    ResolveIntervalCommand,
    SchedulerCommandIdentity,
    SchedulerContextRef,
    SchedulerEligibilityBoundary,
    SchedulerEligibilityManifest,
    SchedulerIntervalBound,
    SchedulerIntervalBoundHead,
    SchedulerOverflowHold,
)
from chiplog.capabilities.agent_loop.scheduler_domain import (
    CANONICAL_VERSION,
    COORDINATE_VERSION,
    policy_branch,
)
from chiplog.capabilities.agent_loop.scheduler_materialization import (
    IntervalParentPrimitive,
    SchedulerCanonicalMember,
    SchedulerRunInputs,
    _compile,
    _overflow_candidate,
    prepare_interval,
    prepare_resolution,
)
from chiplog.capabilities.agent_loop.scheduler_preparation import (
    BatchPreparationCut,
    ConfigurationGenesisCommand,
    ConfigurationPreparationRequest,
    ConfigurationPreparationSnapshot,
    LeasePreparationRequest,
    LeasePreparationSnapshot,
    SchedulerLeaseTransitionRecord,
    prepare_configuration,
    prepare_lease,
)
from chiplog.capabilities.agent_loop.scheduler_rollover import (
    IssuedRolloverObservation,
    prepare_rollover,
    rollover_payload_fingerprint,
    rollover_snapshot_fingerprint,
)
from chiplog.capabilities.agent_loop.scheduler_startup import (
    MaterializedSchedulerRow,
    SchedulerStartupHold,
    SelectedSchedulerBatch,
    _rollover,
    validate_selected_scheduler_records,
)

from .support import issue, view


def history(
    policy: Literal["SKIP", "COALESCE", "MATERIALIZE_EACH"] = "SKIP",
    limits: SchedulerIntervalBound | None = None,
) -> tuple[SelectedSchedulerBatch, ...]:
    context = SchedulerContextRef(
        tenant_id="tenant",
        service_identity="scheduler",
        session_id="expired-session",
        mandate_head="historical-mandate",
        issuance_id="historical-issuance",
        issuance_fingerprint="a" * 64,
    )
    definition = FixedIntervalDefinition(
        start_ns=0,
        period_ns=1,
        end_exclusive_ns="NO_END",
        run_inputs=SchedulerRunInputs(
            tenant="tenant",
            principal="principal",
            prompt="prompt",
            policy=BudgetPolicy(),
            origin=EndpointSelection(
                kind="ORIGIN_EXACT",
                ingress_binding_head="ingress",
                endpoint_head="endpoint",
                endpoint_id="endpoint",
                provider="hermetic-local",
                recipient="principal",
                canonical_address="local://principal",
                credential_binding_head="credential",
            ),
            contour_head="contour",
            policy_head="policy",
            worker_session="worker",
            authority_epoch="epoch",
        ),
    )
    bound = limits or SchedulerIntervalBound(
        max_member_count=10, max_manifest_bytes=100000, max_serialized_batch_bytes=1000000
    )
    snapshot = genesis(
        ConfigurationSnapshot(schedule=None, policy=None, bound=None, active_hold=Absent()),
        schedule_id="schedule",
        definition=definition,
        policy=policy,
        bound=bound,
        context=context,
        command_id="genesis",
        authority_epoch="old-epoch",
        broker_generation="old-broker",
        runtime_graph_generation="old-graph",
    ).proposed
    assert snapshot.schedule and snapshot.policy and snapshot.bound
    objects = (
        ("scheduler.schedule-definition", schedule_head(snapshot.schedule).head, snapshot.schedule),
        ("scheduler.missed-policy", policy_head(snapshot.policy).head, snapshot.policy),
        ("scheduler.interval-bound", snapshot.bound.head_id, snapshot.bound),
    )
    records = tuple(
        SchedulerCanonicalMember(
            record_kind=kind,
            record_id=identity,
            schema_id="chiplog." + kind + ".v1",
            canonical_base64=base64.b64encode(value.canonical_bytes()).decode(),
            fingerprint=hashlib.sha256(value.canonical_bytes()).hexdigest(),
        )
        for kind, identity, value in objects
    )
    first = SelectedSchedulerBatch(
        tenant_id="tenant",
        physical_database_id="physical-db",
        publication_ordinal=7,
        decision=Present(head="decision-7", fingerprint="b" * 64),
        historical_validator="chiplog.scheduler.selected-records.v1",
        context=context,
        command_id="genesis",
        operation="scheduler.genesis",
        records=records,
    )
    next_bound = replace_bound(
        snapshot,
        observed=snapshot.bound,
        proposed_bound=bound,
        context=context,
        command_id="replace",
        authority_epoch="old-epoch",
        broker_generation="old-broker",
        runtime_graph_generation="old-graph",
    ).proposed.bound
    assert next_bound
    member = SchedulerCanonicalMember(
        record_kind="scheduler.interval-bound",
        record_id=next_bound.head_id,
        schema_id="chiplog.scheduler.interval-bound.v1",
        canonical_base64=base64.b64encode(next_bound.canonical_bytes()).decode(),
        fingerprint=hashlib.sha256(next_bound.canonical_bytes()).hexdigest(),
    )
    second = first.model_copy(
        update={
            "publication_ordinal": 9,
            "decision": Present(head="decision-9", fingerprint="c" * 64),
            "command_id": "replace",
            "operation": "scheduler.replace_bound",
            "records": (member,),
        }
    )
    return first, second


def rows(selected: tuple[SelectedSchedulerBatch, ...]) -> tuple[MaterializedSchedulerRow, ...]:
    return tuple(
        MaterializedSchedulerRow(
            tenant_id=batch.tenant_id,
            physical_database_id=batch.physical_database_id,
            publication_ordinal=batch.publication_ordinal,
            member_ordinal=ordinal,
            decision=batch.decision,
            record=record,
        )
        for batch in selected
        for ordinal, record in enumerate(batch.records)
    )


def test_actual_configuration_producer_bytes_are_the_only_admitted_startup_taxonomy() -> None:
    original = history()[0]
    schedule = ScheduleRevision.model_validate_json(
        base64.b64decode(original.records[0].canonical_base64)
    )
    bound = SchedulerIntervalBoundHead.model_validate_json(
        base64.b64decode(original.records[2].canonical_base64)
    )
    command = ConfigurationGenesisCommand(
        identity=SchedulerCommandIdentity(
            command_id="genesis", schema_version="1", canonicalization_version="1"
        ),
        schedule_id=schedule.schedule_id,
        definition=schedule.definition,
        policy="SKIP",
        bound=bound.bound,
    )
    cut = BatchPreparationCut(
        tenant_id="tenant",
        tenant_frontier=0,
        materialization_commitment="a" * 64,
        registry_head="registry",
        registry_fingerprint="b" * 64,
        submission_id="submission",
        authorized_context=original.context,
        authorized_command_fingerprint=hashlib.sha256(command.canonical_bytes()).hexdigest(),
        authority_proof=Present(head="authority", fingerprint="c" * 64),
    )
    prepared = prepare_configuration(
        ConfigurationPreparationRequest(
            operation="scheduler.genesis",
            context=original.context,
            snapshot=ConfigurationPreparationSnapshot(
                cut=cut,
                current=ConfigurationSnapshot(
                    schedule=None, policy=None, bound=None, active_hold=Absent()
                ),
                authority_epoch="old-epoch",
                broker_generation="old-broker",
                runtime_graph_generation="old-graph",
                scheduler_authority_head="authority",
            ),
            command_bytes=command.canonical_bytes(),
        )
    )
    selected = (original.model_copy(update={"records": prepared.records}),)
    index = validate_selected_scheduler_records(
        tenant_id="tenant",
        physical_database_id="physical-db",
        selected=selected,
        materialized=rows(selected),
    )
    assert len(index.schedules) == len(index.policies) == len(index.bounds) == 1
    alias = tuple(
        record.model_copy(update={"record_kind": record.record_kind.removeprefix("scheduler.")})
        for record in prepared.records
    )
    forged = (original.model_copy(update={"records": alias}),)
    with pytest.raises(SchedulerStartupHold):
        validate_selected_scheduler_records(
            tenant_id="tenant",
            physical_database_id="physical-db",
            selected=forged,
            materialized=rows(forged),
        )


def test_immutable_historical_context_and_generations_rebuild_indexes_without_live_authority() -> (
    None
):
    selected = history()
    result = validate_selected_scheduler_records(
        tenant_id="tenant",
        physical_database_id="physical-db",
        selected=selected,
        materialized=rows(selected),
    )
    assert len(result.schedules) == len(result.policies) == len(result.bounds) == 1
    assert result.bounds[0][1].generation == 1
    assert result.bounds[0][1].runtime_graph_generation == "old-graph"
    assert len(result.selected_record_ids) == 4


def interval_history(
    kind: Literal["SKIP", "COALESCE", "MATERIALIZE_EACH"] = "SKIP",
    *,
    previous: str = "0",
    amended_start: int | None = None,
) -> tuple[SelectedSchedulerBatch, ...]:
    selected = history(kind)
    first, second = selected
    schedule = ScheduleRevision.model_validate_json(
        base64.b64decode(first.records[0].canonical_base64)
    )
    policy = PolicyRevision.model_validate_json(base64.b64decode(first.records[1].canonical_base64))
    bound = SchedulerIntervalBoundHead.model_validate_json(
        base64.b64decode(second.records[0].canonical_base64)
    )
    snapshot = ConfigurationSnapshot(
        schedule=schedule, policy=policy, bound=bound, active_hold=Absent()
    )
    if amended_start is not None:
        proposed = amend(
            snapshot,
            observed_schedule=schedule_head(schedule),
            observed_policy=policy_head(policy),
            context=first.context,
            command_id="amend",
            definition=schedule.definition.model_copy(update={"start_ns": amended_start}),
        ).proposed
        assert proposed.schedule
        schedule, snapshot = proposed.schedule, proposed
        raw = schedule.canonical_bytes()
        member = SchedulerCanonicalMember(
            record_kind="scheduler.schedule-definition",
            record_id=schedule_head(schedule).head,
            schema_id="chiplog.scheduler.schedule-definition.v1",
            canonical_base64=base64.b64encode(raw).decode(),
            fingerprint=hashlib.sha256(raw).hexdigest(),
        )
        amendment = first.model_copy(
            update={
                "publication_ordinal": 10,
                "decision": Present(head="decision-amend", fingerprint="d" * 64),
                "command_id": "amend",
                "operation": "scheduler.amend_schedule",
                "records": (member,),
            }
        )
        selected = (*selected, amendment)
    boundary = SchedulerEligibilityBoundary(
        schedule_definition_head=schedule_head(schedule),
        missed_occurrence_policy_head=policy_head(policy),
        previous_due_boundary=DueCoordinate(
            coordinate_policy_version=COORDINATE_VERSION, canonical_coordinate=previous
        ),
        cutoff_due_coordinate=DueCoordinate(
            coordinate_policy_version=COORDINATE_VERSION, canonical_coordinate="2"
        ),
        enumeration_frontier="frontier",
        predecessor_interval=Absent(),
        canonicalization_version=CANONICAL_VERSION,
    )
    manifest = enumerate_definition(snapshot, boundary, ())
    assert isinstance(manifest, SchedulerEligibilityManifest)
    parent = IntervalParentPrimitive(
        kind="ORDINARY",
        command=SchedulerCommandIdentity(
            command_id="interval", schema_version="1", canonicalization_version="1"
        ),
        boundary=boundary,
        current_bound=bound,
        original_bound=bound,
        original_hold=Absent(),
        operator_proof=Absent(),
        manifest=manifest,
        branch=policy_branch(kind, len(manifest.members)),
        run_inputs=schedule.definition.run_inputs,
    )
    candidate = _compile(parent, manifest.members)
    interval = first.model_copy(
        update={
            "publication_ordinal": 11,
            "decision": Present(head="decision-10", fingerprint="e" * 64),
            "command_id": "interval",
            "operation": "scheduler.decide_interval",
            "records": candidate.records,
        }
    )
    complete = (*selected, interval)
    return complete


@pytest.mark.parametrize(
    "kind,expected_count", [("SKIP", 8), ("COALESCE", 18), ("MATERIALIZE_EACH", 26)]
)
def test_recorded_interval_validates_immutable_manifest_and_causal_envelope(
    kind: Literal["SKIP", "COALESCE", "MATERIALIZE_EACH"], expected_count: int
) -> None:
    complete = interval_history(kind)
    selected, interval = complete[:-1], complete[-1]
    records = interval.records
    result = validate_selected_scheduler_records(
        tenant_id="tenant",
        physical_database_id="physical-db",
        selected=complete,
        materialized=rows(complete),
    )
    assert len(result.selected_record_ids) == expected_count
    for altered in (
        interval.model_copy(update={"records": (records[0], *records[2:])}),
        interval.model_copy(
            update={
                "records": (
                    *records[:-1],
                    records[-1].model_copy(update={"fingerprint": "f" * 64}),
                )
            }
        ),
    ):
        bad = (*selected, altered)
        with pytest.raises(SchedulerStartupHold):
            validate_selected_scheduler_records(
                tenant_id="tenant",
                physical_database_id="physical-db",
                selected=bad,
                materialized=rows(bad),
            )


@pytest.mark.parametrize("amended_start", [None, 1])
def test_first_boundary_is_original_revision_zero_even_after_amendment(
    amended_start: int | None,
) -> None:
    good = interval_history(amended_start=amended_start)
    result = validate_selected_scheduler_records(
        tenant_id="tenant",
        physical_database_id="physical-db",
        selected=good,
        materialized=rows(good),
    )
    assert result.registered_genesis[0][1].canonical_coordinate == "0"
    assert result.registered_genesis[0][2].head == good[0].records[0].record_id
    # Every downstream identity, manifest and envelope is recomputed consistently;
    # only the immutable original genesis proves the omitted initial range.
    shifted = interval_history(previous="1", amended_start=amended_start)
    with pytest.raises(SchedulerStartupHold) as rejected:
        validate_selected_scheduler_records(
            tenant_id="tenant",
            physical_database_id="physical-db",
            selected=shifted,
            materialized=rows(shifted),
        )
    assert rejected.value.__cause__ is not None
    assert "registered original revision0 genesis" in str(rejected.value.__cause__)


def overflow_history(
    dimension: str,
) -> tuple[tuple[SelectedSchedulerBatch, ...], DecideIntervalCommand, IntervalParentPrimitive]:
    limits = SchedulerIntervalBound(
        max_member_count=1 if dimension == "MEMBER_COUNT" else 10,
        max_manifest_bytes=1 if dimension == "MANIFEST_BYTES" else 100000,
        max_serialized_batch_bytes=1 if dimension == "SERIALIZED_BATCH_BYTES" else 1000000,
    )
    selected = history("COALESCE", limits)
    parent = IntervalParentPrimitive.model_validate_json(
        base64.b64decode(interval_history("COALESCE")[-1].records[0].canonical_base64)
    )
    bound = SchedulerIntervalBoundHead.model_validate_json(
        base64.b64decode(selected[-1].records[0].canonical_base64)
    )
    parent = parent.model_copy(update={"current_bound": bound, "original_bound": bound})
    command = DecideIntervalCommand(
        identity=parent.command,
        boundary=parent.boundary,
        bound_head=bound,
        manifest=parent.manifest,
        publication_fence=PreRootDecisionFence(
            command_id=parent.command.command_id,
            disposition=FirstPublication(
                decision=Absent(), expected_canonical_absence_manifest="absence"
            ),
            scheduler_authority_head="authority",
            broker_generation="broker",
            runtime_generation="graph",
        ),
    )
    candidate = prepare_interval(
        command,
        bound,
        parent.manifest.members,
        parent.run_inputs,
        Present(head="enumeration", fingerprint="f" * 64),
    )
    assert isinstance(candidate.result, SchedulerOverflowHold)
    batch = selected[0].model_copy(
        update={
            "publication_ordinal": 11,
            "decision": Present(head="overflow-decision", fingerprint="d" * 64),
            "command_id": command.identity.command_id,
            "operation": "scheduler.decide_interval",
            "records": candidate.records,
        }
    )
    return (*selected, batch), command, parent


@pytest.mark.parametrize("dimension", ["MEMBER_COUNT", "MANIFEST_BYTES", "SERIALIZED_BATCH_BYTES"])
def test_historical_overflow_recomputes_exact_dimension_scalar_and_complete_witness(
    dimension: str,
) -> None:
    selected, command, parent = overflow_history(dimension)
    result = validate_selected_scheduler_records(
        tenant_id="tenant",
        physical_database_id="physical-db",
        selected=selected,
        materialized=rows(selected),
    )
    assert len(result.active_holds) == 1 and not result.lineages
    hold = result.active_holds[0]
    assert hold.exceeded_dimension == dimension
    for actual, limit, wrong_dimension in (
        (hold.actual_value + 1, hold.limit, hold.exceeded_dimension),
        (hold.actual_value, hold.limit + 1, hold.exceeded_dimension),
        (
            hold.actual_value,
            hold.limit,
            "MANIFEST_BYTES" if dimension != "MANIFEST_BYTES" else "MEMBER_COUNT",
        ),
    ):
        forged = _overflow_candidate(
            command,
            parent.current_bound,
            hold.evidence,
            wrong_dimension,
            actual,
            limit,
            hold.operator_recovery_owner,
        )
        bad = (*selected[:-1], selected[-1].model_copy(update={"records": forged.records}))
        with pytest.raises(SchedulerStartupHold) as rejected:
            validate_selected_scheduler_records(
                tenant_id="tenant",
                physical_database_id="physical-db",
                selected=bad,
                materialized=rows(bad),
            )
        assert rejected.value.__cause__ is not None
        assert "dimension/precedence/actual/limit" in str(rejected.value.__cause__)


@pytest.mark.parametrize("dimension", ["MEMBER_COUNT", "MANIFEST_BYTES", "SERIALIZED_BATCH_BYTES"])
def test_resolution_consumes_exact_active_hold_and_publishes_one_complete_root_batch(
    dimension: str,
) -> None:
    selected, ordinary, parent = overflow_history(dimension)
    hold = SchedulerOverflowHold.model_validate_json(
        base64.b64decode(selected[-1].records[0].canonical_base64)
    )
    first = selected[0]
    schedule = ScheduleRevision.model_validate_json(
        base64.b64decode(first.records[0].canonical_base64)
    )
    policy = PolicyRevision.model_validate_json(base64.b64decode(first.records[1].canonical_base64))
    snapshot = ConfigurationSnapshot(
        schedule=schedule, policy=policy, bound=hold.bound_head, active_hold=hold.hold
    )
    successor = replace_bound(
        snapshot,
        observed=hold.bound_head,
        proposed_bound=SchedulerIntervalBound(
            max_member_count=10, max_manifest_bytes=100000, max_serialized_batch_bytes=1000000
        ),
        context=first.context,
        command_id="sufficient-bound",
        authority_epoch="old-epoch",
        broker_generation="old-broker",
        runtime_graph_generation="old-graph",
    ).proposed.bound
    assert successor
    raw = successor.canonical_bytes()
    bound_member = SchedulerCanonicalMember(
        record_kind="scheduler.interval-bound",
        record_id=successor.head_id,
        schema_id="chiplog.scheduler.interval-bound.v1",
        canonical_base64=base64.b64encode(raw).decode(),
        fingerprint=hashlib.sha256(raw).hexdigest(),
    )
    bound_batch = first.model_copy(
        update={
            "publication_ordinal": 12,
            "decision": Present(head="bound-after-hold", fingerprint="f" * 64),
            "command_id": "sufficient-bound",
            "operation": "scheduler.replace_bound",
            "records": (bound_member,),
        }
    )
    command = ResolveIntervalCommand(
        identity=parent.command.model_copy(update={"command_id": "resolve"}),
        active_hold=hold,
        successor_bound=successor,
        boundary=parent.boundary,
        complete_manifest=parent.manifest,
        operator_proof=Present(head="historical-resolution-proof", fingerprint="a" * 64),
        publication_fence=ordinary.publication_fence.model_copy(update={"command_id": "resolve"}),
    )
    candidate = prepare_resolution(command, successor, parent.manifest.members, parent.run_inputs)
    resolved = first.model_copy(
        update={
            "publication_ordinal": 13,
            "decision": Present(head="resolution-decision", fingerprint="c" * 64),
            "command_id": "resolve",
            "operation": "scheduler.resolve_interval",
            "records": candidate.records,
        }
    )
    complete = (*selected, bound_batch, resolved)
    index = validate_selected_scheduler_records(
        tenant_id="tenant",
        physical_database_id="physical-db",
        selected=complete,
        materialized=rows(complete),
    )
    assert not index.active_holds and len(index.lineages) == 1
    for bad in (
        (*selected, resolved),
        (
            *selected,
            bound_batch,
            resolved.model_copy(update={"records": (*resolved.records[:-2], resolved.records[-1])}),
        ),
        (
            *selected,
            bound_batch,
            resolved.model_copy(update={"operation": "scheduler.decide_interval"}),
        ),
        (
            *selected,
            bound_batch,
            resolved,
            resolved.model_copy(
                update={
                    "publication_ordinal": 14,
                    "decision": Present(head="rival-resolution", fingerprint="d" * 64),
                }
            ),
        ),
    ):
        with pytest.raises(SchedulerStartupHold):
            validate_selected_scheduler_records(
                tenant_id="tenant",
                physical_database_id="physical-db",
                selected=bad,
                materialized=rows(bad),
            )


def test_rollover_causal_mapper_preserves_stable_run_and_requires_all_six_exact_members() -> None:
    # Local transition induction fixture at UINT64_MAX, not a fabricated claim
    # that a short complete genesis-to-MAX journal was enumerated.
    current = view(2**64 - 1)
    assert current.lease.kind == "HELD"
    held = current.lease.binding
    exhausted = ExhaustedLease(
        lease_head="exhausted",
        exhausted_command_id="takeover",
        trusted_expiry=held.trusted_expiry,
        authority_epoch="authority",
        preceding_held_lease=held,
    )
    current = current.model_copy(update={"lease": exhausted})
    authority = RolloverAuthorityRef(
        proof_id="proof",
        proof_fingerprint="a" * 64,
        authority_head="operator",
        command_id="rollover",
        command_payload_fingerprint="b" * 64,
        predecessor_rollover=current.predecessor_rollover,
    )
    command = PhysicalRootRolloverCommand(
        identity=SchedulerCommandIdentity(
            command_id="rollover", schema_version="1", canonicalization_version="1"
        ),
        fence=PhysicalRootRolloverFence(
            lineage=current.lineage,
            physical_root=current.physical_root,
            exhaustion=ExhaustionBinding(
                hold_head=exhausted.lease_head,
                exhausted_command_id=exhausted.exhausted_command_id,
                lease=held,
                authority_epoch="authority",
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
        current,
        command,
        IssuedRolloverObservation(
            authority=authority,
            snapshot_fingerprint=rollover_snapshot_fingerprint(current),
            submission_id="submission",
            authority_epoch="authority",
            used_epoch_ids=(current.physical_root.current_epoch_id,),
            used_lease_heads=(held.lease_head, exhausted.lease_head),
        ),
        submission_id="submission",
    )
    batch = history()[0].model_copy(
        update={
            "command_id": "rollover",
            "operation": "scheduler.rollover",
            "records": candidate.records,
        }
    )
    observed = {current.lineage.root_id: current}
    epochs, leases = (
        {current.physical_root.current_epoch_id},
        {held.lease_head, exhausted.lease_head},
    )
    _rollover(batch, observed, epochs, leases)
    assert observed[current.lineage.root_id] == candidate.view
    assert (
        observed[current.lineage.root_id].lineage.current_run_id == current.lineage.current_run_id
    )
    for index in range(6):
        with pytest.raises(SchedulerStartupHold):
            _rollover(
                batch.model_copy(
                    update={"records": candidate.records[:index] + candidate.records[index + 1 :]}
                ),
                {current.lineage.root_id: current},
                {current.physical_root.current_epoch_id},
                {held.lease_head, exhausted.lease_head},
            )
    edge = candidate.records[4]
    body = json.loads(base64.b64decode(edge.canonical_base64))
    body["old_epoch_id"] = "rival-epoch"
    raw = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    forged = edge.model_copy(
        update={
            "canonical_base64": base64.b64encode(raw).decode(),
            "fingerprint": hashlib.sha256(raw).hexdigest(),
        }
    )
    with pytest.raises(SchedulerStartupHold):
        _rollover(
            batch.model_copy(
                update={"records": (*candidate.records[:4], forged, candidate.records[5])}
            ),
            {current.lineage.root_id: current},
            {current.physical_root.current_epoch_id},
            {held.lease_head, exhausted.lease_head},
        )


def test_whole_batch_equality_is_not_overflow_and_active_hold_blocks_rewritten_run_inputs() -> None:
    selected, command, parent = overflow_history("SERIALIZED_BATCH_BYTES")
    hold = SchedulerOverflowHold.model_validate_json(
        base64.b64decode(selected[-1].records[0].canonical_base64)
    )
    assert hold.evidence.kind == "FULL_MANIFEST"
    limit = hold.actual_value
    for _ in range(5):
        base = history(
            "COALESCE",
            SchedulerIntervalBound(
                max_member_count=10, max_manifest_bytes=100000, max_serialized_batch_bytes=limit
            ),
        )
        bound = SchedulerIntervalBoundHead.model_validate_json(
            base64.b64decode(base[-1].records[0].canonical_base64)
        )
        measured_parent = parent.model_copy(
            update={"current_bound": bound, "original_bound": bound}
        )
        actual = _compile(measured_parent, parent.manifest.members).serialized_batch_bytes
        if actual == limit:
            break
        limit = actual
    else:
        pytest.fail("exact byte-bound fixture did not converge")
    exact_command = command.model_copy(update={"bound_head": bound})
    forced = _overflow_candidate(
        exact_command,
        bound,
        hold.evidence,
        "SERIALIZED_BATCH_BYTES",
        actual,
        limit,
        hold.operator_recovery_owner,
    )
    bad = (*base, selected[-1].model_copy(update={"records": forced.records}))
    with pytest.raises(SchedulerStartupHold) as rejected:
        validate_selected_scheduler_records(
            tenant_id="tenant",
            physical_database_id="physical-db",
            selected=bad,
            materialized=rows(bad),
        )
    assert "dimension/precedence/actual/limit" in str(rejected.value.__cause__)
    old = ScheduleRevision.model_validate_json(
        base64.b64decode(selected[0].records[0].canonical_base64)
    )
    changed = old.model_copy(
        update={
            "revision": 1,
            "previous": Present(
                head=selected[0].records[0].record_id,
                fingerprint=selected[0].records[0].fingerprint,
            ),
            "command_id": "mutate-prompt",
            "definition": old.definition.model_copy(
                update={
                    "run_inputs": old.definition.run_inputs.model_copy(
                        update={"prompt": "x" * 10000}
                    )
                }
            ),
        }
    )
    raw = changed.canonical_bytes()
    member = SchedulerCanonicalMember(
        record_kind="scheduler.schedule-definition",
        record_id=schedule_head(changed).head,
        schema_id="chiplog.scheduler.schedule-definition.v1",
        canonical_base64=base64.b64encode(raw).decode(),
        fingerprint=hashlib.sha256(raw).hexdigest(),
    )
    amendment = selected[0].model_copy(
        update={
            "publication_ordinal": 12,
            "decision": Present(head="amend-during-hold", fingerprint="d" * 64),
            "command_id": "mutate-prompt",
            "operation": "scheduler.amend_schedule",
            "records": (member,),
        }
    )
    bad = (*selected, amendment)
    with pytest.raises(SchedulerStartupHold, match="active hold forbids"):
        validate_selected_scheduler_records(
            tenant_id="tenant",
            physical_database_id="physical-db",
            selected=bad,
            materialized=rows(bad),
        )


def test_historical_lease_chain_uses_recorded_time_and_exact_selected_predecessor() -> None:
    selected = interval_history("COALESCE")
    index = validate_selected_scheduler_records(
        tenant_id="tenant",
        physical_database_id="physical-db",
        selected=selected,
        materialized=rows(selected),
    )
    current = index.lineages[0]
    kinds: tuple[Literal["ACQUIRE", "RENEW", "TAKEOVER"], ...] = ("ACQUIRE", "RENEW", "TAKEOVER")
    for ordinal, kind in enumerate(kinds, 12):
        command, issued = issue(
            current,
            kind,
            now=50 if kind == "ACQUIRE" else 80 if kind == "RENEW" else 200,
            expiry=120 if kind == "ACQUIRE" else 160 if kind == "RENEW" else 240,
            generation=2 if kind == "TAKEOVER" else 1,
            lease_id="second" if kind == "TAKEOVER" else "first",
            command_id="lease-" + kind,
        )
        issued = issued.model_copy(
            update={"used_lease_ids": () if kind == "ACQUIRE" else ("first",)}
        )
        context = selected[0].context.model_copy(
            update={"service_identity": "worker", "session_id": "session"}
        )
        prepared = prepare_lease(
            LeasePreparationRequest(
                operation="scheduler.acquire"
                if kind == "ACQUIRE"
                else "scheduler.renew"
                if kind == "RENEW"
                else "scheduler.takeover",
                context=context,
                snapshot=LeasePreparationSnapshot(
                    tenant_id="tenant",
                    tenant_frontier=ordinal,
                    materialization_commitment="a" * 64,
                    registry_head="historical-registry",
                    registry_fingerprint="b" * 64,
                    current=current,
                    issued=issued,
                    submission_id="submission",
                ),
                command_bytes=command.canonical_bytes(),
            )
        )
        member = SchedulerCanonicalMember(
            record_kind=prepared.record_kind,
            record_id=prepared.record_id,
            schema_id=prepared.schema_id,
            canonical_base64=base64.b64encode(prepared.canonical_record_bytes).decode(),
            fingerprint=prepared.fingerprint,
        )
        batch = selected[0].model_copy(
            update={
                "publication_ordinal": ordinal,
                "decision": Present(head="decision-" + kind, fingerprint="f" * 64),
                "context": context,
                "command_id": command.identity.command_id,
                "operation": "scheduler." + kind.lower(),
                "records": (member,),
            }
        )
        proposed = (*selected, batch)
        index = validate_selected_scheduler_records(
            tenant_id="tenant",
            physical_database_id="physical-db",
            selected=proposed,
            materialized=rows(proposed),
        )
        assert index.lineages[0].lease.kind == "HELD"
        record = SchedulerLeaseTransitionRecord.model_validate_json(prepared.canonical_record_bytes)
        bad_state = record.candidate.state
        assert bad_state.kind == "HELD"
        altered = record.model_copy(
            update={
                "candidate": record.candidate.model_copy(
                    update={
                        "state": bad_state.model_copy(
                            update={
                                "binding": bad_state.binding.model_copy(update={"generation": 99})
                            }
                        )
                    }
                )
            }
        )
        raw = altered.canonical_bytes()
        forged_member = member.model_copy(
            update={
                "canonical_base64": base64.b64encode(raw).decode(),
                "fingerprint": hashlib.sha256(raw).hexdigest(),
            }
        )
        bad = (*selected, batch.model_copy(update={"records": (forged_member,)}))
        with pytest.raises(SchedulerStartupHold):
            validate_selected_scheduler_records(
                tenant_id="tenant",
                physical_database_id="physical-db",
                selected=bad,
                materialized=rows(bad),
            )
        selected, current = proposed, index.lineages[0]


@pytest.mark.parametrize(
    "mutation", ["missing", "extra", "reorder", "database", "tenant", "ordinal", "bytes"]
)
def test_materialized_cut_requires_exact_bidirectional_bytes_and_physical_identity(
    mutation: str,
) -> None:
    selected = history()
    materialized = rows(selected)
    if mutation == "missing":
        materialized = materialized[:-1]
    elif mutation == "extra":
        materialized = (*materialized, materialized[-1])
    elif mutation == "reorder":
        materialized = tuple(reversed(materialized))
    else:
        key, value = {
            "database": ("physical_database_id", "other-db"),
            "tenant": ("tenant_id", "other-tenant"),
            "ordinal": ("member_ordinal", 99),
            "bytes": (
                "record",
                materialized[-1].record.model_copy(update={"fingerprint": "d" * 64}),
            ),
        }[mutation]
        materialized = (*materialized[:-1], materialized[-1].model_copy(update={key: value}))
    with pytest.raises(SchedulerStartupHold):
        validate_selected_scheduler_records(
            tenant_id="tenant",
            physical_database_id="physical-db",
            selected=selected,
            materialized=materialized,
        )


@pytest.mark.parametrize(
    "mutation",
    ["missing-companion", "reordered-history", "context", "command", "unsupported-external"],
)
def test_selected_chain_cannot_be_repaired_or_reauthorized_at_startup(mutation: str) -> None:
    first, second = history()
    if mutation == "missing-companion":
        first = first.model_copy(update={"records": first.records[:-1]})
    elif mutation == "reordered-history":
        first, second = second, first
    elif mutation == "context":
        second = second.model_copy(
            update={"context": second.context.model_copy(update={"session_id": "new-session"})}
        )
    elif mutation == "command":
        second = second.model_copy(update={"command_id": "new-command"})
    else:
        second = second.model_copy(update={"operation": "unknown.external.successor"})
    selected = first, second
    with pytest.raises(SchedulerStartupHold):
        validate_selected_scheduler_records(
            tenant_id="tenant",
            physical_database_id="physical-db",
            selected=selected,
            materialized=rows(selected),
        )
