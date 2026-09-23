"""Complete pure batches with real Run records; journal atomicity remains unclaimed."""

import base64
import hashlib
import json
from typing import Any

import pytest
from pydantic import ValidationError

from chiplog.capabilities.agent_loop.contracts import BudgetPolicy, EndpointSelection
from chiplog.capabilities.agent_loop.domain import validate_record
from chiplog.capabilities.agent_loop.recovery_contracts import (
    Absent,
    FirstPublication,
    PreRootDecisionFence,
    Present,
)
from chiplog.capabilities.agent_loop.scheduler_contracts import (
    DecideIntervalCommand,
    DueCoordinate,
    MissedOccurrencePolicyHead,
    ResolveIntervalCommand,
    ScheduleDefinitionHead,
    SchedulerCommandIdentity,
    SchedulerEligibilityBoundary,
    SchedulerEligibilityManifest,
    SchedulerIntervalBound,
    SchedulerIntervalBoundHead,
    SchedulerOverflowHold,
    UndisposedOccurrence,
)
from chiplog.capabilities.agent_loop.scheduler_domain import (
    CANONICAL_VERSION,
    COORDINATE_VERSION,
    SchedulerDomainError,
    eligible_manifest,
    undisposed_occurrence,
)
from chiplog.capabilities.agent_loop.scheduler_materialization import (
    SCHEMA_DAG,
    IntervalParentPrimitive,
    ScheduledBatchPrimitiveDomainV1,
    SchedulerRunInputs,
    materialize_occurrence,
    prepare_interval,
    prepare_resolution,
    validate_schema_dag,
)

DIGEST = "a" * 64


def inputs() -> SchedulerRunInputs:
    return SchedulerRunInputs(
        tenant="tenant",
        principal="principal",
        prompt="scheduled review",
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
        policy_head="loop-policy",
        worker_session="worker",
        authority_epoch="authority",
    )


def fixture(
    policy: str = "COALESCE",
    count: int = 3,
    *,
    batch_limit: int = 10_000_000,
    manifest_limit: int = 100_000,
) -> tuple[DecideIntervalCommand, tuple[UndisposedOccurrence, ...]]:
    schedule = ScheduleDefinitionHead(
        schedule_id="schedule",
        schedule_revision="revision",
        head="schedule-head",
        fingerprint=DIGEST,
    )
    boundary = SchedulerEligibilityBoundary(
        schedule_definition_head=schedule,
        missed_occurrence_policy_head=MissedOccurrencePolicyHead.model_validate(
            {
                "policy_id": "policy",
                "policy_revision": "policy-revision",
                "head": "policy-head",
                "fingerprint": DIGEST,
                "kind": policy,
            }
        ),
        previous_due_boundary=DueCoordinate(
            coordinate_policy_version=COORDINATE_VERSION,
            canonical_coordinate="0",
        ),
        cutoff_due_coordinate=DueCoordinate(
            coordinate_policy_version=COORDINATE_VERSION,
            canonical_coordinate="100",
        ),
        enumeration_frontier="frontier",
        predecessor_interval=Absent(),
        canonicalization_version=CANONICAL_VERSION,
    )
    source = tuple(
        undisposed_occurrence(
            schedule,
            DueCoordinate(
                coordinate_policy_version=COORDINATE_VERSION,
                canonical_coordinate=str(n),
            ),
            f"undisposed-{n}",
        )
        for n in range(count)
    )
    bound = SchedulerIntervalBoundHead(
        head_id="bound",
        predecessor=Absent(),
        generation=0,
        bound=SchedulerIntervalBound(
            max_member_count=10,
            max_manifest_bytes=manifest_limit,
            max_serialized_batch_bytes=batch_limit,
        ),
        owner_id="agent_loop",
        authority_epoch="authority",
        broker_generation="broker",
        runtime_graph_generation="graph",
        canonicalization_version=CANONICAL_VERSION,
    )
    return DecideIntervalCommand(
        identity=SchedulerCommandIdentity(
            command_id="interval",
            schema_version="1",
            canonicalization_version=CANONICAL_VERSION,
        ),
        boundary=boundary,
        bound_head=bound,
        manifest=eligible_manifest(boundary, source),
        publication_fence=PreRootDecisionFence(
            command_id="interval",
            disposition=FirstPublication(
                decision=Absent(),
                expected_canonical_absence_manifest="absence",
            ),
            scheduler_authority_head="authority",
            broker_generation="broker",
            runtime_generation="graph",
        ),
    ), source


def independent_encode(value: Any) -> bytes:
    if isinstance(value, dict):
        pairs = sorted(value.items(), key=lambda pair: (pair[0] != "kind", pair[0]))
        return (
            b"{"
            + b",".join(
                json.dumps(key, ensure_ascii=False).encode() + b":" + independent_encode(item)
                for key, item in pairs
            )
            + b"}"
        )
    if isinstance(value, (list, tuple)):
        return b"[" + b",".join(independent_encode(item) for item in value) + b"]"
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def proof() -> Present:
    return Present(head="independent-enumeration-proof", fingerprint=DIGEST)


@pytest.mark.parametrize("policy", ["SKIP", "COALESCE", "MATERIALIZE_EACH"])
@pytest.mark.parametrize("count", [0, 1, 3])
def test_complete_policy_batches_have_exact_real_run_root_epoch_lease_bijections(
    policy: str,
    count: int,
) -> None:
    command, source = fixture(policy, count)
    batch = prepare_interval(command, command.bound_head, source, inputs(), proof())
    assert not isinstance(batch.result, SchedulerOverflowHold)
    expected_runs = 0 if policy == "SKIP" or count == 0 else (1 if policy == "COALESCE" else count)
    assert len(batch.occurrences) == expected_runs
    assert len(batch.result.dispositions) == count
    assert batch.result.resulting_boundary == command.boundary.cutoff_due_coordinate
    assert len({member.record_id for member in batch.records}) == len(batch.records)
    assert sum(member.record_kind == "agent_loop.run" for member in batch.records) == expected_runs
    assert sum(member.record_kind == "coalesced-aggregate" for member in batch.records) == (
        1 if policy == "COALESCE" and count > 1 else 0
    )
    for kind in ("stable-lineage", "physical-root", "physical-selector", "root-lease-genesis"):
        assert sum(member.record_kind == kind for member in batch.records) == expected_runs
    for occurrence in batch.occurrences:
        run = occurrence.run
        validate_record(None, run)
        assert run.root_binding != "NOT_APPLICABLE"
        assert run.run_id == run.root_binding.initial_run_id
        assert occurrence.commitment.lineage.current_run_id == run.run_id
        assert run.root_binding.root_id == occurrence.commitment.lineage.root_id
        assert base64.b64decode(run.root_binding.subject_canonical_base64) == (
            occurrence.primitive.subject.canonical_bytes()
        )
        assert occurrence.commitment.genesis_lease.generation == 0
        assert occurrence.commitment.parent_decision == batch.result.decision
        by_id = {member.record_id: member for member in batch.records}
        for reference in (
            occurrence.commitment.parent_decision,
            occurrence.commitment.initial_run,
            occurrence.commitment.initialization,
            occurrence.commitment.reciprocal_run_root,
            occurrence.commitment.physical_epoch,
            occurrence.commitment.companion_manifest,
        ):
            assert by_id[reference.head].fingerprint == reference.fingerprint
        primitive = occurrence.primitive.model_dump(mode="json")
        expected_genesis = hashlib.sha256(
            independent_encode(
                [
                    "execution-root-lease-genesis-v1",
                    primitive,
                ]
            )
        ).hexdigest()
        assert occurrence.commitment.genesis_lease.lease_head == (
            "execution-root-lease-genesis-v1:" + expected_genesis
        )
        for member in occurrence.members:
            payload = base64.b64decode(member.canonical_base64)
            assert hashlib.sha256(payload).hexdigest() == member.fingerprint
            if member.record_kind != "agent_loop.run":
                assert independent_encode(json.loads(payload)) == payload
        assert (
            occurrence.commitment.finalized_members_fingerprint
            == hashlib.sha256(
                independent_encode(
                    [
                        "scheduled-batch-finalized-members-v1",
                        [member.model_dump(mode="json") for member in occurrence.members],
                    ]
                )
            ).hexdigest()
        )
    expected_interval = hashlib.sha256(
        independent_encode(
            [
                "scheduler-interval-envelope-v1",
                {
                    "complete_non_envelope_records": [
                        item.model_dump(mode="json")
                        for item in batch.records
                        if item.record_kind != "interval-result"
                    ],
                    "result_without_commitment": batch.result.model_dump(
                        mode="json", exclude={"complete_commitment"}
                    ),
                },
            ]
        )
    ).hexdigest()
    assert batch.batch_fingerprint == expected_interval
    assert batch == prepare_interval(command, command.bound_head, source, inputs(), proof())
    assert batch.serialized_batch_bytes == len(
        independent_encode([item.model_dump(mode="json") for item in batch.records])
    )


def test_self_covering_slots_unknown_domains_and_cycles_reject() -> None:
    command, source = fixture(count=1)
    batch = prepare_interval(command, command.bound_head, source, inputs(), proof())
    primitive = batch.occurrences[0].primitive
    for forbidden in ("genesis_lease_head", "initialization", "batch_fingerprint", "commitment"):
        with pytest.raises(ValidationError):
            ScheduledBatchPrimitiveDomainV1.model_validate(
                {
                    **primitive.model_dump(),
                    forbidden: DIGEST,
                }
            )
    assert batch.parent_primitive is not None
    for forbidden in ("whole_batch_fingerprint", "child_root_id", "child_run_head"):
        with pytest.raises(ValidationError):
            IntervalParentPrimitive.model_validate(
                {
                    **batch.parent_primitive.model_dump(),
                    forbidden: DIGEST,
                }
            )
    validate_schema_dag(SCHEMA_DAG)
    for graph in (
        (*SCHEMA_DAG, SCHEMA_DAG[0]),
        (("parent_primitive", ("interval_envelope",)), *SCHEMA_DAG[1:]),
        (("parent_primitive", ("unknown",)), *SCHEMA_DAG[1:]),
    ):
        with pytest.raises(SchedulerDomainError):
            validate_schema_dag(graph)


def test_parent_manifest_and_tagged_subject_mutations_change_or_reject_full_materialization() -> (
    None
):
    command, source = fixture()
    batch = prepare_interval(command, command.bound_head, source, inputs(), proof())
    occurrence = batch.occurrences[0]
    assert batch.parent_primitive is not None
    assert occurrence.primitive.subject.kind == "COALESCED"
    changed_subject = occurrence.primitive.subject.model_copy(update={"aggregate_id": "alias"})
    with pytest.raises(SchedulerDomainError, match="subject or branch"):
        materialize_occurrence(
            batch.parent_primitive, occurrence.commitment.parent_decision, changed_subject
        )
    assert isinstance(command.manifest, SchedulerEligibilityManifest)
    changed_manifest = command.manifest.model_copy(
        update={"members": command.manifest.members[:-1]}
    )
    with pytest.raises(SchedulerDomainError, match="manifest differs"):
        prepare_interval(
            command.model_copy(update={"manifest": changed_manifest}),
            command.bound_head,
            source,
            inputs(),
            proof(),
        )
    changed_inputs = inputs().model_copy(update={"prompt": "different scheduled work"})
    changed = prepare_interval(command, command.bound_head, source, changed_inputs, proof())
    assert changed.batch_fingerprint != batch.batch_fingerprint
    assert changed.occurrences[0].run.run_id != occurrence.run.run_id


def test_serialized_whole_batch_limit_counts_parent_every_run_and_companion_at_exact_edge() -> None:
    limit = 1_000_000
    for _ in range(10):
        command, source = fixture("MATERIALIZE_EACH", 3, batch_limit=limit)
        batch = prepare_interval(command, command.bound_head, source, inputs(), proof())
        actual = (
            batch.result.actual_value
            if isinstance(batch.result, SchedulerOverflowHold)
            else (batch.serialized_batch_bytes)
        )
        if actual == limit:
            break
        limit = actual
    else:
        pytest.fail("serialized-size fixed point did not converge")
    assert not isinstance(batch.result, SchedulerOverflowHold)
    assert len(batch.occurrences) == 3
    command, source = fixture("MATERIALIZE_EACH", 3, batch_limit=limit - 1)
    overflow = prepare_interval(command, command.bound_head, source, inputs(), proof())
    assert isinstance(overflow.result, SchedulerOverflowHold)
    assert overflow.result.exceeded_dimension == "SERIALIZED_BATCH_BYTES"
    assert overflow.result.actual_value == limit
    assert overflow.occurrences == ()
    assert [member.record_kind for member in overflow.records] == ["overflow-hold"]


def test_streaming_overflow_and_resolution_preserve_complete_membership_and_one_parent() -> None:
    command, source = fixture(manifest_limit=1)
    assert isinstance(command.manifest, SchedulerEligibilityManifest)
    batch = prepare_interval(command, command.bound_head, source, inputs(), proof())
    assert isinstance(batch.result, SchedulerOverflowHold)
    hold = batch.result
    assert hold.evidence.kind == "STREAMING_MANIFEST"
    assert hold.evidence.manifest_digest == command.manifest.fingerprint
    assert hold.evidence.member_count == 3
    assert hold.evidence.enumeration_completeness_proof == proof()
    with pytest.raises(SchedulerDomainError, match="active overflow hold"):
        prepare_interval(command, command.bound_head, source, inputs(), proof(), active_hold=hold)
    successor = command.bound_head.model_copy(
        update={
            "head_id": "successor-bound",
            "generation": 1,
            "predecessor": Present(
                head=command.bound_head.head_id, fingerprint=command.bound_head.digest()
            ),
            "bound": command.bound_head.bound.model_copy(update={"max_manifest_bytes": 100_000}),
        }
    )
    resolution = ResolveIntervalCommand(
        identity=command.identity.model_copy(update={"command_id": "resolve"}),
        active_hold=hold,
        successor_bound=successor,
        boundary=command.boundary,
        complete_manifest=command.manifest,
        operator_proof=proof(),
        publication_fence=command.publication_fence,
    )
    resolved = prepare_resolution(resolution, successor, source, inputs())
    assert not isinstance(resolved.result, SchedulerOverflowHold)
    assert resolved.result.kind == "INTERVAL_RESOLUTION"
    assert all(
        item.commitment.parent_decision == resolved.result.decision for item in resolved.occurrences
    )
    assert sum(item.record_kind == "overflow-resolution" for item in resolved.records) == 1
    with pytest.raises(SchedulerDomainError, match="current successor bound"):
        prepare_resolution(resolution, command.bound_head, source, inputs())
