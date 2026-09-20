"""Pure owner algebra evidence; publication, clock and source custody remain HOLD."""

import hashlib
import json
from typing import Any

import pytest

from chiplog.capabilities.agent_loop.recovery_contracts import Absent, CoalescedSubject
from chiplog.capabilities.agent_loop.scheduler_contracts import (
    DueCoordinate,
    MissedOccurrencePolicyHead,
    ScheduleDefinitionHead,
    SchedulerEligibilityBoundary,
    SchedulerIntervalBound,
)
from chiplog.capabilities.agent_loop.scheduler_domain import (
    CANONICAL_VERSION,
    COORDINATE_VERSION,
    SchedulerDomainError,
    coordinate_value,
    eligibility_overflows,
    eligible_manifest,
    execution_subjects,
    measure_eligibility,
    occurrence_identity,
    policy_branch,
    undisposed_occurrence,
    verify_manifest,
)

DIGEST = "a" * 64


def coordinate(value: int) -> DueCoordinate:
    return DueCoordinate(
        coordinate_policy_version=COORDINATE_VERSION,
        canonical_coordinate=str(value),
    )


def boundary(policy: str = "COALESCE") -> SchedulerEligibilityBoundary:
    return SchedulerEligibilityBoundary(
        schedule_definition_head=ScheduleDefinitionHead(
            schedule_id="schedule",
            schedule_revision="revision",
            head="schedule-head",
            fingerprint=DIGEST,
        ),
        missed_occurrence_policy_head=MissedOccurrencePolicyHead.model_validate(
            {
                "policy_id": "policy",
                "policy_revision": "revision",
                "head": "policy-head",
                "fingerprint": DIGEST,
                "kind": policy,
            }
        ),
        previous_due_boundary=coordinate(2),
        cutoff_due_coordinate=coordinate(12),
        enumeration_frontier="frontier",
        predecessor_interval=Absent(),
        canonicalization_version=CANONICAL_VERSION,
    )


def reference_hash(domain: str, value: object) -> str:
    # Independent json encoder instance rather than production canonical/digest helpers.
    encoder = json.JSONEncoder(sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256("".join(encoder.iterencode([domain, value])).encode("utf-8")).hexdigest()


@pytest.mark.parametrize(
    "policy,expected",
    [
        ("SKIP", ("BOUNDARY_ONLY_NO_WORK", "SKIP_ALL", "SKIP_ALL")),
        ("COALESCE", ("BOUNDARY_ONLY_NO_WORK", "COALESCE_SINGLE", "COALESCE_MULTI")),
        (
            "MATERIALIZE_EACH",
            (
                "BOUNDARY_ONLY_NO_WORK",
                "MATERIALIZE_EACH_SINGLE",
                "MATERIALIZE_EACH_MULTI",
            ),
        ),
    ],
)
def test_every_policy_selects_zero_one_many_without_a_fourth_disposition(
    policy: Any,
    expected: tuple[str, ...],
) -> None:
    assert tuple(policy_branch(policy, count) for count in (0, 1, 3)) == expected
    for bad_count in (-1, True, 1.5):
        with pytest.raises(SchedulerDomainError):
            policy_branch(policy, bad_count)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", ["00", "01", "-1", "+1", "1.0", " 1", "١"])
def test_coordinate_aliases_cannot_change_occurrence_or_interval_order(value: str) -> None:
    with pytest.raises(SchedulerDomainError):
        coordinate_value(
            DueCoordinate(
                coordinate_policy_version=COORDINATE_VERSION,
                canonical_coordinate=value,
            )
        )


def test_identity_and_manifest_match_independent_encoder_and_half_open_interval() -> None:
    cut = boundary()
    schedule = cut.schedule_definition_head
    values = tuple(
        undisposed_occurrence(schedule, coordinate(n), f"head-{n}") for n in (12, 10, 1, 2)
    )
    manifest = eligible_manifest(cut, values)
    assert [coordinate_value(item.due_coordinate) for item in manifest.members] == [2, 10]
    expected_identity = "scheduler-occurrence-v1:" + reference_hash(
        "scheduler-occurrence-v1",
        ["schedule", "revision", coordinate(2).model_dump(mode="json")],
    )
    assert occurrence_identity(schedule, coordinate(2)) == expected_identity
    assert manifest.fingerprint == reference_hash(
        "scheduler-eligibility-manifest-v1",
        [
            cut.model_dump(mode="json"),
            [item.model_dump(mode="json") for item in manifest.members],
        ],
    )
    assert verify_manifest(cut, manifest, tuple(reversed(values))) == manifest
    changed_revision = schedule.model_copy(update={"schedule_revision": "successor"})
    assert occurrence_identity(schedule, coordinate(2)) != occurrence_identity(
        changed_revision,
        coordinate(2),
    )


def test_submitted_manifest_omission_addition_duplicate_reorder_and_head_mutations_reject() -> None:
    cut = boundary()
    values = tuple(
        undisposed_occurrence(
            cut.schedule_definition_head,
            coordinate(n),
            f"head-{n}",
        )
        for n in (2, 5, 10)
    )
    manifest = eligible_manifest(cut, values)
    extra = undisposed_occurrence(cut.schedule_definition_head, coordinate(6), "head-6")
    mutations = (
        values[:-1],
        (*values, extra),
        values + values[:1],
        tuple(reversed(values)),
        (values[0].model_copy(update={"undisposed_head": "rival"}), *values[1:]),
    )
    for members in mutations:
        with pytest.raises(SchedulerDomainError):
            verify_manifest(cut, manifest.model_copy(update={"members": members}), values)
    with pytest.raises(SchedulerDomainError):
        eligible_manifest(cut, values + values[:1])
    with pytest.raises(SchedulerDomainError):
        eligible_manifest(cut, (values[0].model_copy(update={"occurrence_id": "alias"}),))
    with pytest.raises(SchedulerDomainError):
        verify_manifest(cut.model_copy(update={"enumeration_frontier": "new"}), manifest, values)


def test_count_and_manifest_byte_edges_leave_whole_batch_admission_pending() -> None:
    cut = boundary()
    values = tuple(
        undisposed_occurrence(
            cut.schedule_definition_head,
            coordinate(n),
            f"head-{n}",
        )
        for n in (2, 5)
    )
    manifest = eligible_manifest(cut, values)
    measured = measure_eligibility(manifest)
    assert measured.whole_batch_admission == "PENDING_FINALIZATION"
    exact = SchedulerIntervalBound(
        max_member_count=2,
        max_manifest_bytes=measured.canonical_manifest_bytes,
        max_serialized_batch_bytes=1,
    )
    # Deliberately not whole-batch success despite a tiny serialized-batch bound.
    assert eligibility_overflows(manifest, exact) == ()
    count = eligibility_overflows(manifest, exact.model_copy(update={"max_member_count": 1}))
    assert [(item.dimension, item.actual, item.limit) for item in count] == [("MEMBER_COUNT", 2, 1)]
    size = eligibility_overflows(
        manifest,
        exact.model_copy(
            update={
                "max_manifest_bytes": measured.canonical_manifest_bytes - 1,
            }
        ),
    )
    assert size[0].dimension == "MANIFEST_BYTES"
    assert size[0].actual == size[0].limit + 1


def test_coalescing_binds_complete_manifest_and_individual_subjects_remain_distinct() -> None:
    cut = boundary()
    values = tuple(
        undisposed_occurrence(
            cut.schedule_definition_head,
            coordinate(n),
            f"head-{n}",
        )
        for n in (2, 5, 10)
    )
    manifest = eligible_manifest(cut, values)
    subjects = execution_subjects(cut, manifest, values)
    assert len(subjects) == 1 and isinstance(subjects[0], CoalescedSubject)
    assert subjects[0].manifest_fingerprint == manifest.fingerprint
    with pytest.raises(SchedulerDomainError):
        execution_subjects(cut, manifest.model_copy(update={"members": values[:1]}), values)
    each = boundary("MATERIALIZE_EACH")
    individuals = execution_subjects(each, eligible_manifest(each, values), values)
    assert len(individuals) == 3
    assert len({item.canonical_bytes() for item in individuals}) == 3
    skip = boundary("SKIP")
    assert execution_subjects(skip, eligible_manifest(skip, values), values) == ()
