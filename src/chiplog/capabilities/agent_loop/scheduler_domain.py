"""Pure scheduler identity/eligibility algebra, not publication authority.

The registered writer must supply its complete authoritative occurrence snapshot
at the exact transaction frontier. These helpers cannot certify a caller's source
as complete. Whole-batch byte admission awaits complete Run initialization encoding.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Literal

from pydantic import Field

from chiplog.capabilities.agent_loop.recovery_contracts import (
    CoalescedSubject,
    Digest,
    IndividualSubject,
    RecoveryDTO,
)
from chiplog.capabilities.agent_loop.scheduler_contracts import (
    DueCoordinate,
    IntervalBranch,
    ScheduleDefinitionHead,
    SchedulerEligibilityBoundary,
    SchedulerEligibilityManifest,
    SchedulerIntervalBound,
    UndisposedOccurrence,
)

COORDINATE_VERSION = "chiplog.scheduler.unix-ns.v1"
CANONICAL_VERSION = "chiplog.scheduler.canonical.v1"


class SchedulerDomainError(ValueError):
    """Invalid, stale, aliased or unsupported scheduler proposal."""


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _digest(domain: str, value: object) -> str:
    # Explicit tuple framing prevents concatenation aliases between fields/domains.
    return hashlib.sha256(_canonical([domain, value])).hexdigest()


def coordinate_value(coordinate: DueCoordinate) -> int:
    if coordinate.coordinate_policy_version != COORDINATE_VERSION:
        raise SchedulerDomainError("unsupported scheduler coordinate policy")
    if re.fullmatch(r"0|[1-9][0-9]*", coordinate.canonical_coordinate) is None:
        raise SchedulerDomainError("non-canonical nanosecond coordinate")
    return int(coordinate.canonical_coordinate)


def occurrence_identity(schedule: ScheduleDefinitionHead, coordinate: DueCoordinate) -> str:
    coordinate_value(coordinate)
    return "scheduler-occurrence-v1:" + _digest(
        "scheduler-occurrence-v1",
        [schedule.schedule_id, schedule.schedule_revision, coordinate.model_dump(mode="json")],
    )


def undisposed_occurrence(
    schedule: ScheduleDefinitionHead,
    coordinate: DueCoordinate,
    undisposed_head: str,
) -> UndisposedOccurrence:
    identity = occurrence_identity(schedule, coordinate)
    fields = {
        "occurrence_id": identity,
        "schedule_id": schedule.schedule_id,
        "schedule_revision": schedule.schedule_revision,
        "due_coordinate": coordinate.model_dump(mode="json"),
        "undisposed_head": undisposed_head,
    }
    return UndisposedOccurrence(
        occurrence_id=identity,
        schedule_id=schedule.schedule_id,
        schedule_revision=schedule.schedule_revision,
        due_coordinate=coordinate,
        undisposed_head=undisposed_head,
        undisposed_fingerprint=_digest("scheduler-undisposed-v1", fields),
    )


def eligible_manifest(
    boundary: SchedulerEligibilityBoundary,
    authoritative_occurrences: tuple[UndisposedOccurrence, ...],
) -> SchedulerEligibilityManifest:
    """Enumerate the supplied writer snapshot, including half-open cut semantics."""
    if boundary.canonicalization_version != CANONICAL_VERSION:
        raise SchedulerDomainError("unsupported scheduler canonicalization version")
    start = coordinate_value(boundary.previous_due_boundary)
    cutoff = coordinate_value(boundary.cutoff_due_coordinate)
    if cutoff < start:
        raise SchedulerDomainError("interval cutoff precedes durable boundary")
    identities: set[str] = set()
    coordinates: set[int] = set()
    eligible: list[UndisposedOccurrence] = []
    schedule = boundary.schedule_definition_head
    for member in authoritative_occurrences:
        expected = undisposed_occurrence(schedule, member.due_coordinate, member.undisposed_head)
        if member != expected:
            raise SchedulerDomainError("occurrence identity, revision or head fingerprint mismatch")
        due = coordinate_value(member.due_coordinate)
        if member.occurrence_id in identities or due in coordinates:
            raise SchedulerDomainError("duplicate authoritative occurrence")
        identities.add(member.occurrence_id)
        coordinates.add(due)
        if start <= due < cutoff:
            eligible.append(member)
    members = tuple(sorted(eligible, key=lambda item: coordinate_value(item.due_coordinate)))
    fingerprint = _digest(
        "scheduler-eligibility-manifest-v1",
        [boundary.model_dump(mode="json"), [item.model_dump(mode="json") for item in members]],
    )
    return SchedulerEligibilityManifest(members=members, fingerprint=fingerprint)


def verify_manifest(
    boundary: SchedulerEligibilityBoundary,
    submitted: SchedulerEligibilityManifest,
    authoritative_occurrences: tuple[UndisposedOccurrence, ...],
) -> SchedulerEligibilityManifest:
    expected = eligible_manifest(boundary, authoritative_occurrences)
    if submitted.canonical_bytes() != expected.canonical_bytes():
        raise SchedulerDomainError("submitted manifest differs from complete canonical enumeration")
    return expected


def policy_branch(
    policy: Literal["SKIP", "COALESCE", "MATERIALIZE_EACH"],
    member_count: int,
) -> IntervalBranch:
    if type(member_count) is not int or member_count < 0:
        raise SchedulerDomainError("member count must be a nonnegative integer")
    if policy not in ("SKIP", "COALESCE", "MATERIALIZE_EACH"):
        raise SchedulerDomainError("unknown missed-occurrence policy")
    if member_count == 0:
        return "BOUNDARY_ONLY_NO_WORK"
    if policy == "SKIP":
        return "SKIP_ALL"
    if policy == "COALESCE":
        return "COALESCE_SINGLE" if member_count == 1 else "COALESCE_MULTI"
    return "MATERIALIZE_EACH_SINGLE" if member_count == 1 else "MATERIALIZE_EACH_MULTI"


class EligibilityMeasurement(RecoveryDTO):
    member_count: int = Field(ge=0)
    canonical_manifest_bytes: int = Field(ge=0)
    manifest_fingerprint: Digest
    # This is deliberately never a complete interval admission result.
    whole_batch_admission: Literal["PENDING_FINALIZATION"] = "PENDING_FINALIZATION"


class EligibilityOverflow(RecoveryDTO):
    dimension: Literal["MEMBER_COUNT", "MANIFEST_BYTES"]
    actual: int = Field(ge=0)
    limit: int = Field(gt=0)


def measure_eligibility(manifest: SchedulerEligibilityManifest) -> EligibilityMeasurement:
    return EligibilityMeasurement(
        member_count=len(manifest.members),
        canonical_manifest_bytes=len(manifest.canonical_bytes()),
        manifest_fingerprint=manifest.fingerprint,
    )


def eligibility_overflows(
    manifest: SchedulerEligibilityManifest,
    bound: SchedulerIntervalBound,
) -> tuple[EligibilityOverflow, ...]:
    measured = measure_eligibility(manifest)
    result: list[EligibilityOverflow] = []
    if measured.member_count > bound.max_member_count:
        result.append(
            EligibilityOverflow(
                dimension="MEMBER_COUNT",
                actual=measured.member_count,
                limit=bound.max_member_count,
            )
        )
    if measured.canonical_manifest_bytes > bound.max_manifest_bytes:
        result.append(
            EligibilityOverflow(
                dimension="MANIFEST_BYTES",
                actual=measured.canonical_manifest_bytes,
                limit=bound.max_manifest_bytes,
            )
        )
    return tuple(result)


def execution_subjects(
    boundary: SchedulerEligibilityBoundary,
    manifest: SchedulerEligibilityManifest,
    authoritative_occurrences: tuple[UndisposedOccurrence, ...],
) -> tuple[IndividualSubject | CoalescedSubject, ...]:
    verified = verify_manifest(boundary, manifest, authoritative_occurrences)
    branch = policy_branch(boundary.missed_occurrence_policy_head.kind, len(verified.members))
    if branch in ("BOUNDARY_ONLY_NO_WORK", "SKIP_ALL"):
        return ()
    if branch == "COALESCE_MULTI":
        aggregate_id = "scheduler-aggregate-v1:" + _digest(
            "scheduler-coalesced-aggregate-v1",
            [boundary.model_dump(mode="json"), verified.model_dump(mode="json")],
        )
        return (
            CoalescedSubject(
                aggregate_id=aggregate_id,
                manifest_fingerprint=verified.fingerprint,
            ),
        )
    return tuple(IndividualSubject(occurrence_id=item.occurrence_id) for item in verified.members)
