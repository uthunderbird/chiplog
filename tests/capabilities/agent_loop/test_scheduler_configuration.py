"""Configuration stream proposals and definition-derived bounded enumeration."""

import hashlib

import pytest

from chiplog.capabilities.agent_loop.contracts import BudgetPolicy, EndpointSelection
from chiplog.capabilities.agent_loop.recovery_contracts import Absent, Present
from chiplog.capabilities.agent_loop.scheduler_configuration import (
    ConfigurationSnapshot,
    DisposedOccurrence,
    EnumerationOverflow,
    FixedIntervalDefinition,
    amend,
    definition_enumeration,
    definition_members,
    enumerate_definition,
    genesis,
    policy_head,
    replace_bound,
    schedule_head,
    stream_definition,
)
from chiplog.capabilities.agent_loop.scheduler_contracts import (
    DueCoordinate,
    SchedulerContextRef,
    SchedulerEligibilityBoundary,
    SchedulerIntervalBound,
)
from chiplog.capabilities.agent_loop.scheduler_domain import (
    CANONICAL_VERSION,
    COORDINATE_VERSION,
    SchedulerDomainError,
    occurrence_identity,
)
from chiplog.capabilities.agent_loop.scheduler_materialization import SchedulerRunInputs


def context() -> SchedulerContextRef:
    return SchedulerContextRef(
        tenant_id="tenant",
        service_identity="scheduler",
        session_id="session",
        mandate_head="mandate",
        issuance_id="issuance",
        issuance_fingerprint="a" * 64,
    )


def definition() -> FixedIntervalDefinition:
    return FixedIntervalDefinition(
        start_ns=10,
        period_ns=10,
        end_exclusive_ns="NO_END",
        run_inputs=SchedulerRunInputs(
            tenant="tenant",
            principal="principal",
            prompt="scheduled",
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


def initialized() -> ConfigurationSnapshot:
    return genesis(
        ConfigurationSnapshot(schedule=None, policy=None, bound=None, active_hold=Absent()),
        schedule_id="schedule",
        definition=definition(),
        policy="COALESCE",
        bound=SchedulerIntervalBound(
            max_member_count=3, max_manifest_bytes=10000, max_serialized_batch_bytes=100000
        ),
        context=context(),
        command_id="genesis",
        authority_epoch="epoch",
        broker_generation="broker",
        runtime_graph_generation="graph",
    ).proposed


def boundary(snapshot: ConfigurationSnapshot, cutoff: str = "50") -> SchedulerEligibilityBoundary:
    assert snapshot.schedule and snapshot.policy
    return SchedulerEligibilityBoundary(
        schedule_definition_head=schedule_head(snapshot.schedule),
        missed_occurrence_policy_head=policy_head(snapshot.policy),
        previous_due_boundary=DueCoordinate(
            coordinate_policy_version=COORDINATE_VERSION, canonical_coordinate="10"
        ),
        cutoff_due_coordinate=DueCoordinate(
            coordinate_policy_version=COORDINATE_VERSION, canonical_coordinate=cutoff
        ),
        enumeration_frontier="frontier",
        predecessor_interval=Absent(),
        canonicalization_version=CANONICAL_VERSION,
    )


def test_genesis_is_complete_and_heads_bind_actual_records() -> None:
    current = initialized()
    assert current.schedule and current.policy and current.bound
    assert current.bound.generation == 0
    assert (
        schedule_head(current.schedule).fingerprint
        == hashlib.sha256(current.schedule.canonical_bytes()).hexdigest()
    )
    assert (
        policy_head(current.policy).fingerprint
        == hashlib.sha256(current.policy.canonical_bytes()).hexdigest()
    )
    assert ConfigurationSnapshot.model_validate_json(current.canonical_bytes()) == current
    for partial in (current, current.model_copy(update={"policy": None, "bound": None})):
        with pytest.raises(SchedulerDomainError, match="all three"):
            genesis(
                partial,
                schedule_id="schedule",
                definition=definition(),
                policy="SKIP",
                bound=current.bound.bound,
                context=context(),
                command_id="again",
                authority_epoch="epoch",
                broker_generation="broker",
                runtime_graph_generation="graph",
            )


def test_amendment_compares_both_streams_and_hold_blocks_both() -> None:
    current = initialized()
    assert current.schedule and current.policy
    schedule, policy = schedule_head(current.schedule), policy_head(current.policy)
    for changes in ({"policy": "SKIP"}, {"definition": definition()}):
        with pytest.raises(SchedulerDomainError, match="active overflow"):
            amend(
                current.model_copy(
                    update={"active_hold": Present(head="hold", fingerprint="b" * 64)}
                ),
                observed_schedule=schedule,
                observed_policy=policy,
                context=context(),
                command_id="amend",
                **changes,
            )
    changed = amend(
        current,
        observed_schedule=schedule,
        observed_policy=policy,
        context=context(),
        command_id="policy",
        policy="SKIP",
    ).proposed
    with pytest.raises(SchedulerDomainError, match="exact head"):
        amend(
            changed,
            observed_schedule=schedule,
            observed_policy=policy,
            context=context(),
            command_id="schedule",
            definition=definition(),
        )
    forged = schedule.model_copy(update={"fingerprint": "f" * 64})
    with pytest.raises(SchedulerDomainError, match="exact head"):
        amend(
            current,
            observed_schedule=forged,
            observed_policy=policy,
            context=context(),
            command_id="forged",
            policy="SKIP",
        )


def test_bound_can_advance_during_hold_but_stale_writer_cannot() -> None:
    current = initialized().model_copy(
        update={"active_hold": Present(head="hold", fingerprint="b" * 64)}
    )
    assert current.bound
    proposed = current.bound.bound.model_copy(update={"max_member_count": 4})
    next_snapshot = replace_bound(
        current,
        observed=current.bound,
        proposed_bound=proposed,
        context=context(),
        command_id="bound",
        authority_epoch="epoch",
        broker_generation="broker",
        runtime_graph_generation="graph",
    ).proposed
    assert next_snapshot.bound and next_snapshot.bound.generation == 1
    assert next_snapshot.bound.predecessor == Present(
        head=current.bound.head_id,
        fingerprint=hashlib.sha256(current.bound.canonical_bytes()).hexdigest(),
    )
    next_next = replace_bound(
        next_snapshot,
        observed=next_snapshot.bound,
        proposed_bound=proposed,
        context=context(),
        command_id="bound-next",
        authority_epoch="epoch",
        broker_generation="broker",
        runtime_graph_generation="graph",
    ).proposed
    assert next_next.bound and next_next.bound.generation == 2
    assert next_next.bound.predecessor == Present(
        head=next_snapshot.bound.head_id,
        fingerprint=hashlib.sha256(next_snapshot.bound.canonical_bytes()).hexdigest(),
    )
    assert next_snapshot.active_hold == current.active_hold
    with pytest.raises(SchedulerDomainError, match="head changed"):
        replace_bound(
            next_snapshot,
            observed=current.bound,
            proposed_bound=proposed,
            context=context(),
            command_id="bound",
            authority_epoch="epoch",
            broker_generation="broker",
            runtime_graph_generation="graph",
        )


def test_definition_enumeration_half_open_disposed_and_arithmetic_overflow() -> None:
    current = initialized()
    coordinate = DueCoordinate(
        coordinate_policy_version=COORDINATE_VERSION, canonical_coordinate="20"
    )
    item = DisposedOccurrence(
        occurrence_id=occurrence_identity(boundary(current).schedule_definition_head, coordinate),
        due_coordinate=coordinate,
        disposition=Present(head="disposed", fingerprint="d" * 64),
    )
    actual = enumerate_definition(current, boundary(current), (item,))
    assert not isinstance(actual, EnumerationOverflow)
    assert [row.due_coordinate.canonical_coordinate for row in actual.members] == ["10", "30", "40"]
    overflow = enumerate_definition(current, boundary(current, str(10**100)), ())
    assert isinstance(overflow, EnumerationOverflow)
    assert overflow.eligible_count == (10**100 - 1) // 10
    assert overflow.limit == 3
    for invalid in ((item, item), (item.model_copy(update={"occurrence_id": "wrong"}),)):
        with pytest.raises(SchedulerDomainError, match="disposed"):
            enumerate_definition(current, boundary(current), invalid)
    with pytest.raises(SchedulerDomainError, match="non-canonical"):
        enumerate_definition(current, boundary(current, "050"), ())


def test_definition_record_change_invalidates_boundary_and_revision_identity() -> None:
    current = initialized()
    assert current.schedule and current.policy
    old_boundary = boundary(current)
    next_snapshot = amend(
        current,
        observed_schedule=schedule_head(current.schedule),
        observed_policy=policy_head(current.policy),
        context=context(),
        command_id="revision",
        definition=definition(),
    ).proposed
    with pytest.raises(SchedulerDomainError, match="actual configuration"):
        enumerate_definition(next_snapshot, old_boundary, ())
    before = enumerate_definition(current, boundary(current, "20"), ())
    after = enumerate_definition(next_snapshot, boundary(next_snapshot, "20"), ())
    assert not isinstance(before, EnumerationOverflow) and not isinstance(
        after, EnumerationOverflow
    )
    assert before.members[0].occurrence_id != after.members[0].occurrence_id


@pytest.mark.parametrize("count", [0, 1, 4, 50])
def test_stream_digest_and_exact_bytes_match_independent_complete_encoder(count: int) -> None:
    import json

    current = initialized()
    if count == 50:
        assert current.schedule and current.policy
        current = current.model_copy(
            update={
                "schedule": current.schedule.model_copy(update={"schedule_id": "会議-🙂"}),
                "policy": current.policy.model_copy(update={"schedule_id": "会議-🙂"}),
            }
        )
    exact_boundary = boundary(current, str(10 + count * 10))
    plan = definition_enumeration(current, exact_boundary, ())
    members = tuple(definition_members(plan))
    payload = [
        exact_boundary.model_dump(mode="json"),
        [row.model_dump(mode="json") for row in members],
    ]
    digest = hashlib.sha256(
        json.dumps(
            ["scheduler-eligibility-manifest-v1", payload],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    manifest_bytes = json.dumps(
        {"fingerprint": digest, "members": payload[1]},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    assert current.bound
    for limit in (1, len(manifest_bytes), len(manifest_bytes) + 1):
        bounded = current.model_copy(
            update={
                "bound": current.bound.model_copy(
                    update={
                        "bound": current.bound.bound.model_copy(
                            update={"max_manifest_bytes": limit}
                        )
                    }
                )
            }
        )
        measured = stream_definition(
            bounded, exact_boundary, (), Present(head="proof", fingerprint="e" * 64)
        )
        assert measured.member_count == count
        assert measured.manifest_fingerprint == digest
        assert measured.manifest_bytes == len(manifest_bytes)
        if limit < len(manifest_bytes):
            assert measured.evidence.kind == "STREAMING_MANIFEST"
            assert measured.evidence.member_count == count
            if count:
                assert measured.evidence.first_member == Present(
                    head=members[0].undisposed_head, fingerprint=members[0].undisposed_fingerprint
                )
                assert measured.evidence.last_member == Present(
                    head=members[-1].undisposed_head, fingerprint=members[-1].undisposed_fingerprint
                )
        else:
            assert measured.evidence.kind == "FULL_MANIFEST"
            assert measured.evidence.manifest.canonical_bytes() == manifest_bytes


def test_stream_filters_disposed_before_endpoints_and_does_not_retain_large_manifest() -> None:
    import tracemalloc

    current = initialized()
    assert current.bound
    current = current.model_copy(
        update={
            "bound": current.bound.model_copy(
                update={"bound": current.bound.bound.model_copy(update={"max_manifest_bytes": 1})}
            )
        }
    )
    exact_boundary = boundary(current, "20010")
    coordinate = DueCoordinate(
        coordinate_policy_version=COORDINATE_VERSION, canonical_coordinate="10"
    )
    disposed = DisposedOccurrence(
        occurrence_id=occurrence_identity(exact_boundary.schedule_definition_head, coordinate),
        due_coordinate=coordinate,
        disposition=Present(head="disposed", fingerprint="d" * 64),
    )
    tracemalloc.start()
    try:
        measured = stream_definition(
            current, exact_boundary, (disposed,), Present(head="proof", fingerprint="e" * 64)
        )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert measured.member_count == 1999
    assert measured.evidence.kind == "STREAMING_MANIFEST"
    assert measured.manifest_bytes > 900_000
    assert peak < measured.manifest_bytes // 2
    assert measured.evidence.first_member.kind == "PRESENT"
    expected = next(
        definition_members(definition_enumeration(current, exact_boundary, (disposed,)))
    )
    assert measured.evidence.first_member.head == expected.undisposed_head


@pytest.mark.parametrize("mutation", ["omit", "duplicate", "reorder"])
def test_stream_rejects_incomplete_or_noncanonical_generator_before_evidence(
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    import chiplog.capabilities.agent_loop.scheduler_configuration as implementation

    current = initialized()
    exact_boundary = boundary(current)
    members = tuple(definition_members(definition_enumeration(current, exact_boundary, ())))
    changed = (
        members[:-1]
        if mutation == "omit"
        else ((*members[:-1], members[-2]) if mutation == "duplicate" else tuple(reversed(members)))
    )
    monkeypatch.setattr(implementation, "definition_members", lambda _: iter(changed))
    with pytest.raises(SchedulerDomainError, match="stream"):
        stream_definition(current, exact_boundary, (), Present(head="proof", fingerprint="e" * 64))
