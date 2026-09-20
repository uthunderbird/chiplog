"""Pure scheduler revision streams; the broker authenticates and atomically publishes.

Canonical records, rather than adjacent claimed fingerprints, determine all CAS
heads. Enumerations are bounded before allocating occurrence objects.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from typing import Literal

from pydantic import Field

from .recovery_contracts import Absent, Identity, Present, RecoveryDTO
from .scheduler_contracts import (
    DueCoordinate,
    FullEligibilityEvidence,
    MissedOccurrencePolicyHead,
    ScheduleDefinitionHead,
    SchedulerContextRef,
    SchedulerEligibilityBoundary,
    SchedulerEligibilityManifest,
    SchedulerIntervalBound,
    SchedulerIntervalBoundHead,
    StreamingEligibilityEvidence,
    UndisposedOccurrence,
)
from .scheduler_domain import (
    CANONICAL_VERSION,
    COORDINATE_VERSION,
    SchedulerDomainError,
    coordinate_value,
    eligible_manifest,
    occurrence_identity,
    undisposed_occurrence,
)
from .scheduler_materialization import SchedulerRunInputs


class FixedIntervalDefinition(RecoveryDTO):
    kind: Literal["FIXED_INTERVAL_V1"] = "FIXED_INTERVAL_V1"
    start_ns: int = Field(ge=0)
    period_ns: int = Field(gt=0)
    end_exclusive_ns: int | Literal["NO_END"]
    run_inputs: SchedulerRunInputs


class ScheduleRevision(RecoveryDTO):
    schedule_id: Identity
    revision: int = Field(ge=0)
    previous: Present | Absent
    definition: FixedIntervalDefinition
    context: SchedulerContextRef
    command_id: Identity


class PolicyRevision(RecoveryDTO):
    schedule_id: Identity
    revision: int = Field(ge=0)
    previous: Present | Absent
    policy: Literal["SKIP", "COALESCE", "MATERIALIZE_EACH"]
    context: SchedulerContextRef
    command_id: Identity


class ConfigurationSnapshot(RecoveryDTO):
    schedule: ScheduleRevision | None
    policy: PolicyRevision | None
    bound: SchedulerIntervalBoundHead | None
    active_hold: Present | Absent


class ConfigurationProposal(RecoveryDTO):
    operation: Literal[
        "scheduler.genesis",
        "scheduler.amend_schedule",
        "scheduler.amend_policy",
        "scheduler.replace_bound",
    ]
    expected: ConfigurationSnapshot
    proposed: ConfigurationSnapshot
    context: SchedulerContextRef
    command_id: Identity


def record_ref(record: RecoveryDTO, prefix: str) -> Present:
    fingerprint = hashlib.sha256(record.canonical_bytes()).hexdigest()
    return Present(head=prefix + ":" + fingerprint, fingerprint=fingerprint)


def schedule_head(record: ScheduleRevision) -> ScheduleDefinitionHead:
    ref = record_ref(record, "scheduler-definition-v1")
    return ScheduleDefinitionHead(
        schedule_id=record.schedule_id,
        schedule_revision=str(record.revision),
        head=ref.head,
        fingerprint=ref.fingerprint,
    )


def policy_head(record: PolicyRevision) -> MissedOccurrencePolicyHead:
    ref = record_ref(record, "scheduler-policy-v1")
    return MissedOccurrencePolicyHead(
        policy_id=record.schedule_id + "/missed-policy",
        policy_revision=str(record.revision),
        head=ref.head,
        fingerprint=ref.fingerprint,
        kind=record.policy,
    )


def _validate_definition(definition: FixedIntervalDefinition, context: SchedulerContextRef) -> None:
    if definition.run_inputs.tenant != context.tenant_id:
        raise SchedulerDomainError("definition belongs to another tenant")
    end = definition.end_exclusive_ns
    if isinstance(end, int) and end <= definition.start_ns:
        raise SchedulerDomainError("exclusive end must follow start")


def _current(
    snapshot: ConfigurationSnapshot,
) -> tuple[ScheduleRevision, PolicyRevision, SchedulerIntervalBoundHead]:
    if snapshot.schedule is None or snapshot.policy is None or snapshot.bound is None:
        raise SchedulerDomainError("configuration is absent or incompletely initialized")
    if snapshot.schedule.schedule_id != snapshot.policy.schedule_id:
        raise SchedulerDomainError("configuration streams belong to different schedules")
    return snapshot.schedule, snapshot.policy, snapshot.bound


def _bound(
    context: SchedulerContextRef,
    command_id: str,
    value: SchedulerIntervalBound,
    previous: SchedulerIntervalBoundHead | None,
    authority_epoch: str,
    broker_generation: str,
    runtime_graph_generation: str,
) -> SchedulerIntervalBoundHead:
    if previous is not None and previous.generation == 2**64 - 1:
        raise SchedulerDomainError("bound generation exhausted")
    body = SchedulerIntervalBoundHead(
        head_id="pending",
        predecessor=Absent()
        if previous is None
        else Present(
            head=previous.head_id,
            fingerprint=hashlib.sha256(previous.canonical_bytes()).hexdigest(),
        ),
        generation=0 if previous is None else previous.generation + 1,
        bound=value,
        owner_id=context.service_identity,
        authority_epoch=authority_epoch,
        broker_generation=broker_generation,
        runtime_graph_generation=runtime_graph_generation,
        canonicalization_version=CANONICAL_VERSION,
    )
    digest = hashlib.sha256(
        body.canonical_bytes() + context.canonical_bytes() + command_id.encode()
    ).hexdigest()
    return body.model_copy(update={"head_id": "scheduler-bound-v1:" + digest})


def genesis(
    snapshot: ConfigurationSnapshot,
    *,
    schedule_id: str,
    definition: FixedIntervalDefinition,
    policy: Literal["SKIP", "COALESCE", "MATERIALIZE_EACH"],
    bound: SchedulerIntervalBound,
    context: SchedulerContextRef,
    command_id: str,
    authority_epoch: str,
    broker_generation: str,
    runtime_graph_generation: str,
) -> ConfigurationProposal:
    if any(item is not None for item in (snapshot.schedule, snapshot.policy, snapshot.bound)) or (
        snapshot.active_hold.kind != "ABSENT"
    ):
        raise SchedulerDomainError("genesis requires all three streams absent and no hold")
    _validate_definition(definition, context)
    proposed = ConfigurationSnapshot(
        schedule=ScheduleRevision(
            schedule_id=schedule_id,
            revision=0,
            previous=Absent(),
            definition=definition,
            context=context,
            command_id=command_id,
        ),
        policy=PolicyRevision(
            schedule_id=schedule_id,
            revision=0,
            previous=Absent(),
            policy=policy,
            context=context,
            command_id=command_id,
        ),
        bound=_bound(
            context,
            command_id,
            bound,
            None,
            authority_epoch,
            broker_generation,
            runtime_graph_generation,
        ),
        active_hold=Absent(),
    )
    return ConfigurationProposal(
        operation="scheduler.genesis",
        expected=snapshot,
        proposed=proposed,
        context=context,
        command_id=command_id,
    )


def amend(
    snapshot: ConfigurationSnapshot,
    *,
    observed_schedule: ScheduleDefinitionHead,
    observed_policy: MissedOccurrencePolicyHead,
    context: SchedulerContextRef,
    command_id: str,
    definition: FixedIntervalDefinition | None = None,
    policy: Literal["SKIP", "COALESCE", "MATERIALIZE_EACH"] | None = None,
) -> ConfigurationProposal:
    schedule, missed, _ = _current(snapshot)
    if observed_schedule != schedule_head(schedule) or observed_policy != policy_head(missed):
        raise SchedulerDomainError("schedule or policy exact head changed")
    if snapshot.active_hold.kind != "ABSENT":
        raise SchedulerDomainError("active overflow hold forbids definition or policy amendment")
    if context.tenant_id != schedule.context.tenant_id:
        raise SchedulerDomainError("foreign configuration tenant")
    if (definition is None) == (policy is None):
        raise SchedulerDomainError("amend exactly one revision stream")
    if definition is not None:
        _validate_definition(definition, context)
        next_schedule = ScheduleRevision(
            schedule_id=schedule.schedule_id,
            revision=schedule.revision + 1,
            previous=record_ref(schedule, "scheduler-definition-v1"),
            definition=definition,
            context=context,
            command_id=command_id,
        )
        proposed = snapshot.model_copy(update={"schedule": next_schedule})
        operation: Literal["scheduler.amend_schedule", "scheduler.amend_policy"] = (
            "scheduler.amend_schedule"
        )
    else:
        assert policy is not None
        next_policy = PolicyRevision(
            schedule_id=missed.schedule_id,
            revision=missed.revision + 1,
            previous=record_ref(missed, "scheduler-policy-v1"),
            policy=policy,
            context=context,
            command_id=command_id,
        )
        proposed = snapshot.model_copy(update={"policy": next_policy})
        operation = "scheduler.amend_policy"
    return ConfigurationProposal(
        operation=operation,
        expected=snapshot,
        proposed=proposed,
        context=context,
        command_id=command_id,
    )


def replace_bound(
    snapshot: ConfigurationSnapshot,
    *,
    observed: SchedulerIntervalBoundHead,
    proposed_bound: SchedulerIntervalBound,
    context: SchedulerContextRef,
    command_id: str,
    authority_epoch: str,
    broker_generation: str,
    runtime_graph_generation: str,
) -> ConfigurationProposal:
    schedule, _, bound = _current(snapshot)
    if observed != bound or context.tenant_id != schedule.context.tenant_id:
        raise SchedulerDomainError("bound head changed or foreign tenant")
    successor = _bound(
        context,
        command_id,
        proposed_bound,
        bound,
        authority_epoch,
        broker_generation,
        runtime_graph_generation,
    )
    return ConfigurationProposal(
        operation="scheduler.replace_bound",
        expected=snapshot,
        proposed=snapshot.model_copy(update={"bound": successor}),
        context=context,
        command_id=command_id,
    )


class EnumerationOverflow(RecoveryDTO):
    """Arithmetic witness; complete manifest digest remains a streaming follow-up."""

    kind: Literal["ENUMERATION_OVERFLOW"] = "ENUMERATION_OVERFLOW"
    boundary: SchedulerEligibilityBoundary
    eligible_count: int = Field(ge=0)
    first_index: int = Field(ge=0)
    end_index: int = Field(ge=0)
    excluded_indices: tuple[int, ...]
    limit: int = Field(gt=0)


class DisposedOccurrence(RecoveryDTO):
    occurrence_id: Identity
    due_coordinate: DueCoordinate
    disposition: Present


class DefinitionEnumeration(RecoveryDTO):
    definition: FixedIntervalDefinition
    boundary: SchedulerEligibilityBoundary
    first_index: int
    end_index: int
    excluded_indices: tuple[int, ...]
    eligible_count: int


def definition_enumeration(
    snapshot: ConfigurationSnapshot,
    boundary: SchedulerEligibilityBoundary,
    disposed: tuple[DisposedOccurrence, ...],
) -> DefinitionEnumeration:
    """Broker supplies complete exact dispositions; caller supplies no eligible list."""
    schedule, policy, _ = _current(snapshot)
    if boundary.schedule_definition_head != schedule_head(
        schedule
    ) or boundary.missed_occurrence_policy_head != policy_head(policy):
        raise SchedulerDomainError("boundary does not bind actual configuration records")
    definition = schedule.definition
    _validate_definition(definition, schedule.context)
    start = coordinate_value(boundary.previous_due_boundary)
    cutoff = coordinate_value(boundary.cutoff_due_coordinate)
    if cutoff < start or boundary.canonicalization_version != CANONICAL_VERSION:
        raise SchedulerDomainError("invalid finite interval boundary")
    end = definition.end_exclusive_ns
    if isinstance(end, int):
        cutoff = min(cutoff, end)
    first = max(0, (start - definition.start_ns + definition.period_ns - 1) // definition.period_ns)
    last = max(
        first, (cutoff - definition.start_ns + definition.period_ns - 1) // definition.period_ns
    )
    disposed_indices: list[int] = []
    for row in disposed:
        due = coordinate_value(row.due_coordinate)
        offset = due - definition.start_ns
        if (
            offset < 0
            or offset % definition.period_ns
            or (isinstance(end, int) and due >= end)
            or row.occurrence_id
            != occurrence_identity(boundary.schedule_definition_head, row.due_coordinate)
        ):
            raise SchedulerDomainError("disposed identity is not in exact definition revision")
        disposed_indices.append(offset // definition.period_ns)
    if len(set(disposed_indices)) != len(disposed_indices):
        raise SchedulerDomainError("disposed identities contain duplicate indices")
    excluded = tuple(sorted(index for index in disposed_indices if first <= index < last))
    count = last - first - len(excluded)
    return DefinitionEnumeration(
        definition=definition,
        boundary=boundary,
        first_index=first,
        end_index=last,
        excluded_indices=excluded,
        eligible_count=count,
    )


def definition_members(plan: DefinitionEnumeration) -> Iterator[UndisposedOccurrence]:
    excluded = iter(plan.excluded_indices)
    next_excluded = next(excluded, None)
    for index in range(plan.first_index, plan.end_index):
        if index == next_excluded:
            next_excluded = next(excluded, None)
            continue
        coordinate = DueCoordinate(
            coordinate_policy_version=COORDINATE_VERSION,
            canonical_coordinate=str(plan.definition.start_ns + index * plan.definition.period_ns),
        )
        identity = occurrence_identity(plan.boundary.schedule_definition_head, coordinate)
        yield undisposed_occurrence(
            plan.boundary.schedule_definition_head, coordinate, identity + "/UNDISPOSED"
        )


def enumerate_definition(
    snapshot: ConfigurationSnapshot,
    boundary: SchedulerEligibilityBoundary,
    disposed: tuple[DisposedOccurrence, ...],
) -> SchedulerEligibilityManifest | EnumerationOverflow:
    plan = definition_enumeration(snapshot, boundary, disposed)
    assert snapshot.bound is not None
    if plan.eligible_count > snapshot.bound.bound.max_member_count:
        return EnumerationOverflow(
            boundary=boundary,
            eligible_count=plan.eligible_count,
            first_index=plan.first_index,
            end_index=plan.end_index,
            excluded_indices=plan.excluded_indices,
            limit=snapshot.bound.bound.max_member_count,
        )
    return eligible_manifest(boundary, tuple(definition_members(plan)))


class StreamedEligibility(RecoveryDTO):
    member_count: int
    manifest_bytes: int
    manifest_fingerprint: str
    evidence: FullEligibilityEvidence | StreamingEligibilityEvidence


def stream_definition(
    snapshot: ConfigurationSnapshot,
    boundary: SchedulerEligibilityBoundary,
    disposed: tuple[DisposedOccurrence, ...],
    enumeration_proof: Present,
) -> StreamedEligibility:
    """Exact one-pass SHA, retaining at most the registered full-manifest budget.

    Time is O(N); interruption returns no result. No arithmetic approximation is
    treated as a digest or complete hold. The iterator has no external I/O.
    """
    plan = definition_enumeration(snapshot, boundary, disposed)
    assert snapshot.bound is not None
    limit = snapshot.bound.bound.max_manifest_bytes

    def canonical(value: object) -> bytes:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()

    digest = hashlib.sha256()
    digest.update(b'["scheduler-eligibility-manifest-v1",[')
    digest.update(canonical(boundary.model_dump(mode="json")))
    digest.update(b",[")
    # Fixed-length digest slot has exactly the same canonical size as the final SHA.
    measured = len(canonical({"fingerprint": "0" * 64, "members": []}))
    retained: list[UndisposedOccurrence] | None = [] if measured <= limit else None
    first: Absent | Present = Absent()
    last: Absent | Present = Absent()
    count = 0
    previous_index = plan.first_index - 1
    excluded_indices = set(plan.excluded_indices)
    for member in definition_members(plan):
        due = coordinate_value(member.due_coordinate)
        offset = due - plan.definition.start_ns
        index = offset // plan.definition.period_ns
        expected_id = occurrence_identity(boundary.schedule_definition_head, member.due_coordinate)
        if (
            offset % plan.definition.period_ns
            or not plan.first_index <= index < plan.end_index
            or index <= previous_index
            or index in excluded_indices
            or member
            != undisposed_occurrence(
                boundary.schedule_definition_head,
                member.due_coordinate,
                expected_id + "/UNDISPOSED",
            )
        ):
            raise SchedulerDomainError(
                "stream member is noncanonical, duplicate or out of definition order"
            )
        previous_index = index
        raw = canonical(member.model_dump(mode="json"))
        if count:
            digest.update(b",")
            measured += 1
        digest.update(raw)
        measured += len(raw)
        reference = Present(head=member.undisposed_head, fingerprint=member.undisposed_fingerprint)
        if count == 0:
            first = reference
        last = reference
        count += 1
        if retained is not None:
            if measured <= limit:
                retained.append(member)
            else:
                retained = None
    digest.update(b"]]]")
    if count != plan.eligible_count:
        raise SchedulerDomainError("stream differs from exact arithmetic membership")
    fingerprint = digest.hexdigest()
    evidence: FullEligibilityEvidence | StreamingEligibilityEvidence
    if retained is not None:
        manifest = SchedulerEligibilityManifest(members=tuple(retained), fingerprint=fingerprint)
        if len(manifest.canonical_bytes()) != measured:
            raise SchedulerDomainError("stream canonical manifest byte domain differs")
        evidence = FullEligibilityEvidence(manifest=manifest)
    else:
        evidence = StreamingEligibilityEvidence(
            manifest_digest=fingerprint,
            member_count=count,
            first_member=first,
            last_member=last,
            order_contract_version="chiplog.scheduler.numeric-due.v1",
            enumeration_completeness_proof=enumeration_proof,
        )
    return StreamedEligibility(
        member_count=count,
        manifest_bytes=measured,
        manifest_fingerprint=fingerprint,
        evidence=evidence,
    )
