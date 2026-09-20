"""Scheduler public consumer shape only; no state-machine conformance claim."""

from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from chiplog.capabilities.agent_loop.recovery_contracts import (
    Absent,
    CoalescedSubject,
    ExecutionLineageBinding,
    FirstPublication,
    IndividualSubject,
    PhysicalRootBinding,
    PreRootDecisionFence,
    Present,
    RolloverPredecessor,
)
from chiplog.capabilities.agent_loop.scheduler_contracts import (
    DecideIntervalCommand,
    DueCoordinate,
    ExecutionRootLeaseState,
    GenesisLease,
    IndividualDisposition,
    IntervalBranch,
    MaterializationCommitment,
    MaterializationIdentity,
    MissedOccurrencePolicyHead,
    OccurrenceDisposition,
    OverflowActive,
    OverflowResolved,
    ScheduleDefinitionHead,
    SchedulerCommandIdentity,
    SchedulerEligibilityBoundary,
    SchedulerEligibilityManifest,
    SchedulerIntervalBound,
    SchedulerIntervalBoundHead,
    SchedulerIntervalResolutionDecision,
    SchedulerLineageView,
    SchedulerOverflowHold,
    SchedulerPort,
    StreamingEligibilityEvidence,
    UndisposedOccurrence,
)

DIGEST = "a" * 64


def test_decide_command_accepts_exact_streaming_witness_without_member_tuple() -> None:
    command = DecideIntervalCommand(
        identity=SchedulerCommandIdentity(
            command_id="stream", schema_version="1", canonicalization_version="1"
        ),
        boundary=boundary(),
        bound_head=bound_head(),
        manifest=StreamingEligibilityEvidence(
            manifest_digest=DIGEST,
            member_count=300,
            first_member=present("first"),
            last_member=present("last"),
            order_contract_version="1",
            enumeration_completeness_proof=present("proof"),
        ),
        publication_fence=PreRootDecisionFence(
            command_id="stream",
            disposition=FirstPublication(
                decision=Absent(), expected_canonical_absence_manifest="absence"
            ),
            scheduler_authority_head="authority",
            broker_generation="broker",
            runtime_generation="runtime",
        ),
    )
    restored = DecideIntervalCommand.model_validate_json(command.canonical_bytes())
    assert restored == command and isinstance(restored.manifest, StreamingEligibilityEvidence)
    values = command.model_dump(mode="json")
    values["manifest"]["kind"] = "TRUNCATED_SUCCESS"
    with pytest.raises(ValidationError):
        DecideIntervalCommand.model_validate(values)


def present(head: str = "head") -> Present:
    return Present(head=head, fingerprint=DIGEST)


def boundary() -> SchedulerEligibilityBoundary:
    return SchedulerEligibilityBoundary(
        schedule_definition_head=ScheduleDefinitionHead(
            schedule_id="schedule",
            schedule_revision="revision",
            head="schedule-head",
            fingerprint=DIGEST,
        ),
        missed_occurrence_policy_head=MissedOccurrencePolicyHead(
            policy_id="policy",
            policy_revision="policy-revision",
            head="policy-head",
            fingerprint=DIGEST,
            kind="COALESCE",
        ),
        previous_due_boundary=DueCoordinate(
            coordinate_policy_version="coordinate-1",
            canonical_coordinate="0000",
        ),
        cutoff_due_coordinate=DueCoordinate(
            coordinate_policy_version="coordinate-1",
            canonical_coordinate="0010",
        ),
        enumeration_frontier="frontier",
        predecessor_interval=Absent(),
        canonicalization_version="1",
    )


def bound_head() -> SchedulerIntervalBoundHead:
    return SchedulerIntervalBoundHead(
        head_id="bound-head",
        predecessor=Absent(),
        generation=0,
        bound=SchedulerIntervalBound(
            max_member_count=2,
            max_manifest_bytes=1024,
            max_serialized_batch_bytes=4096,
        ),
        owner_id="agent-loop",
        authority_epoch="authority",
        broker_generation="broker",
        runtime_graph_generation="graph",
        canonicalization_version="1",
    )


def test_public_consumer_has_separate_context_and_closed_operations() -> None:
    assert set(SchedulerPort.__annotations__) == set()
    for operation in (
        "decide_interval",
        "replace_bound",
        "resolve_interval",
        "transition_lease",
        "rollover",
        "lineage",
    ):
        assert "context" in getattr(SchedulerPort, operation).__annotations__
    assert not hasattr(SchedulerPort, "append")


def test_occurrence_disposition_does_not_accept_interval_only_branches() -> None:
    adapter: TypeAdapter[Any] = TypeAdapter(OccurrenceDisposition)
    for kind in ("BOUNDARY_ONLY_NO_WORK", "OVERFLOW_HOLD", "UNKNOWN"):
        with pytest.raises(ValidationError):
            adapter.validate_python({"kind": kind})
    interval: TypeAdapter[Any] = TypeAdapter(IntervalBranch)
    assert interval.validate_python("BOUNDARY_ONLY_NO_WORK") == "BOUNDARY_ONLY_NO_WORK"
    assert SchedulerEligibilityManifest(members=(), fingerprint=DIGEST).members == ()


def test_complete_boundary_and_bound_head_cannot_omit_exact_observations() -> None:
    values = boundary().model_dump()
    for field in values:
        with pytest.raises(ValidationError):
            SchedulerEligibilityBoundary.model_validate(
                {key: value for key, value in values.items() if key != field}
            )
    bound = bound_head()
    for field in ("max_member_count", "max_manifest_bytes", "max_serialized_batch_bytes"):
        with pytest.raises(ValidationError):
            SchedulerIntervalBound.model_validate({**bound.bound.model_dump(), field: 0})
    with pytest.raises(ValidationError):
        SchedulerIntervalBoundHead.model_validate({**bound.model_dump(), "generation": 2**64})
    with pytest.raises(ValidationError):
        bound.generation = 1


def test_streaming_overflow_retains_completeness_and_resolution_identity() -> None:
    evidence = StreamingEligibilityEvidence(
        manifest_digest=DIGEST,
        member_count=3,
        first_member=present("first"),
        last_member=present("last"),
        order_contract_version="1",
        enumeration_completeness_proof=present("proof"),
    )
    hold = SchedulerOverflowHold(
        hold=present("hold"),
        command=SchedulerCommandIdentity(
            command_id="command",
            schema_version="1",
            canonicalization_version="1",
        ),
        boundary=boundary(),
        bound_head=bound_head(),
        exceeded_dimension="MEMBER_COUNT",
        actual_value=3,
        limit=2,
        evidence=evidence,
        operator_recovery_owner="operator",
        state=OverflowActive(),
    )
    assert hold.state.kind == "ACTIVE"
    assert OverflowResolved(resolution_decision=present()).resolution_decision == present()
    with pytest.raises(ValidationError):
        OverflowResolved.model_validate({"kind": "RESOLVED_BY_OPERATOR"})
    with pytest.raises(ValidationError):
        StreamingEligibilityEvidence.model_validate(
            {
                key: value
                for key, value in evidence.model_dump().items()
                if key != "enumeration_completeness_proof"
            }
        )


def test_shared_lineage_types_keep_tagged_identity_and_epoch_lease_closed() -> None:
    individual = IndividualSubject(occurrence_id="same")
    coalesced = CoalescedSubject(aggregate_id="same", manifest_fingerprint=DIGEST)
    assert individual.canonical_bytes() != coalesced.canonical_bytes()
    assert individual.canonical_bytes().startswith(b'{"kind":"INDIVIDUAL"')
    assert GenesisLease(lease_head="genesis").generation == 0
    adapter: TypeAdapter[Any] = TypeAdapter(ExecutionRootLeaseState)
    for value in (
        {"kind": "UNLEASED", "lease_head": "head", "generation": 1},
        {"kind": "GENERATION_EXHAUSTED_HOLD", "lease_head": "head", "generation": 0},
        {"kind": "RESET", "lease_head": "head", "generation": 0},
    ):
        with pytest.raises(ValidationError):
            adapter.validate_python(value)


def test_materialization_resolution_and_rollover_roundtrip_complete_public_views() -> None:
    subject = IndividualSubject(occurrence_id="occurrence")
    lineage = ExecutionLineageBinding(
        root_id="root",
        subject=subject,
        root_fingerprint=DIGEST,
        lineage_head="lineage-head",
        initial_run_id="run",
        current_run_id="run",
        schedule_id="schedule",
        schedule_revision="revision",
        policy_revision="policy-revision",
    )
    physical = PhysicalRootBinding(
        selector_id="selector",
        selector_head="selector-head",
        selector_version=0,
        current_epoch_id="epoch",
        current_epoch_head="epoch-head",
    )
    occurrence = UndisposedOccurrence(
        occurrence_id="occurrence",
        schedule_id="schedule",
        schedule_revision="revision",
        due_coordinate=boundary().previous_due_boundary,
        undisposed_head="undisposed",
        undisposed_fingerprint=DIGEST,
    )
    disposition = IndividualDisposition(
        occurrence=occurrence,
        materialization=MaterializationIdentity(
            materialization_id="batch",
            primitive_domain_fingerprint=DIGEST,
        ),
    )
    materialization = MaterializationCommitment(
        kind="INDIVIDUAL",
        parent_decision=present("resolution"),
        subject=subject,
        dispositions=(disposition,),
        lineage=lineage,
        initial_run=present("run"),
        initialization=present("init"),
        reciprocal_run_root=present("reciprocal"),
        physical_root=physical,
        physical_epoch=present("epoch"),
        genesis_lease=GenesisLease(lease_head="genesis"),
        companion_manifest=present("companions"),
        primitive_domain_fingerprint=DIGEST,
        finalized_members_fingerprint=DIGEST,
        batch_fingerprint=DIGEST,
        schema_dependency_manifest=present("dag"),
    )
    assert (
        MaterializationCommitment.model_validate_json(materialization.model_dump_json())
        == materialization
    )
    resolution = SchedulerIntervalResolutionDecision(
        decision=present("resolution"),
        command=SchedulerCommandIdentity(
            command_id="resolve",
            schema_version="1",
            canonicalization_version="1",
        ),
        original_hold=present("hold"),
        original_bound_head=bound_head(),
        successor_bound_head=SchedulerIntervalBoundHead(
            **{
                **bound_head().model_dump(),
                "head_id": "successor-bound",
                "generation": 1,
                "predecessor": present("bound-head"),
            }
        ),
        boundary=boundary(),
        manifest=SchedulerEligibilityManifest(members=(occurrence,), fingerprint=DIGEST),
        branch="COALESCE_SINGLE",
        dispositions=(disposition,),
        materializations=(materialization,),
        resulting_boundary=boundary().cutoff_due_coordinate,
        complete_commitment=DIGEST,
    )
    assert (
        SchedulerIntervalResolutionDecision.model_validate_json(resolution.model_dump_json())
        == resolution
    )
    rollover = SchedulerLineageView(
        lineage=lineage,
        physical_root=physical,
        lease=GenesisLease(lease_head="new-genesis"),
        predecessor_rollover=RolloverPredecessor(
            decision=present("rollover-decision"),
            edge=present("rollover-edge"),
        ),
    )
    assert SchedulerLineageView.model_validate_json(rollover.model_dump_json()) == rollover
    with pytest.raises(ValidationError):
        SchedulerLineageView.model_validate(
            {
                **rollover.model_dump(),
                "predecessor_rollover": present().model_dump(),
            }
        )
