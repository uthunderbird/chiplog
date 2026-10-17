"""Native scheduler bytes and pure occurrence preparation; no authority or Run factory."""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Annotated, Literal

from pydantic import Field

from chiplog.capabilities.agent_loop.contracts import SchedulerRootReference
from chiplog.capabilities.agent_loop.delivery_contracts import OriginSelection
from chiplog.capabilities.agent_loop.execution_client_transitions import ExecutionRun
from chiplog.capabilities.agent_loop.recovery_contracts import (
    Absent,
    ExecutionLineageBinding,
    ExecutionLineageSubject,
    PhysicalRootBinding,
    Present,
    UInt64,
)
from chiplog.capabilities.agent_loop.scheduler_client_contracts import (
    NativeOccurrenceMember,
    NativeOrdinaryInterval,
    NativeOverflowInterval,
    NativeScheduledExecution,
    NativeScheduleDraft,
    NativeSchedulerCycle,
    SchedulerClientDTO,
)
from chiplog.capabilities.agent_loop.scheduler_contracts import (
    CoalescedDisposition,
    DueCoordinate,
    FullEligibilityEvidence,
    GenesisLease,
    IndividualDisposition,
    MaterializationIdentity,
    MissedOccurrencePolicyHead,
    OverflowActive,
    ScheduleDefinitionHead,
    SchedulerCommandIdentity,
    SchedulerEligibilityBoundary,
    SchedulerEligibilityManifest,
    SchedulerIntervalBoundHead,
    SchedulerLineageView,
    SchedulerOverflowHold,
)
from chiplog.capabilities.agent_loop.scheduler_domain import (
    CANONICAL_VERSION,
    COORDINATE_VERSION,
    eligible_manifest,
    execution_subjects,
    occurrence_identity,
    undisposed_occurrence,
)
from chiplog.platform.postgres import Record


class Configuration(SchedulerClientDTO):
    schema_id: Literal["chiplog.scheduler.native-configuration.v1"] = (
        "chiplog.scheduler.native-configuration.v1"
    )
    tenant: str
    principal: str
    draft: NativeScheduleDraft
    origin: OriginSelection
    lease_duration_ns: int = Field(gt=0)
    schedule: ScheduleDefinitionHead
    policy: MissedOccurrencePolicyHead
    bound: SchedulerIntervalBoundHead


class Interval(SchedulerClientDTO):
    schema_id: Literal["chiplog.scheduler.native-interval.v1"] = (
        "chiplog.scheduler.native-interval.v1"
    )
    primitive: Primitive
    request: NativeSchedulerCycle
    resolution_bytes: bytes | None
    boundary: SchedulerEligibilityBoundary
    result: Annotated[NativeOrdinaryInterval | NativeOverflowInterval, Field(discriminator="kind")]
    lineages: tuple[SchedulerLineageView, ...]
    successor_bound: SchedulerIntervalBoundHead | None


class LeaseTransition(SchedulerClientDTO):
    schema_id: Literal["chiplog.scheduler.native-lease.v1"] = "chiplog.scheduler.native-lease.v1"
    act_id: str
    transition: Literal["ACQUIRE", "RENEW", "TAKEOVER"]
    observed: SchedulerLineageView
    selected: SchedulerLineageView
    observed_at: UInt64
    duration_ns: int = Field(gt=0)


class Primitive(SchedulerClientDTO):
    command: SchedulerCommandIdentity
    boundary: SchedulerEligibilityBoundary
    bound: SchedulerIntervalBoundHead
    original_hold: Present | Absent
    tenant: str
    principal: str
    worker: str


def reference(domain: str, value: object) -> Present:
    raw = json.dumps([domain, value], sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(raw.encode()).hexdigest()
    return Present(head=domain + ":" + digest, fingerprint=digest)


def record(value: Configuration | Interval | LeaseTransition) -> Record:
    return Record(
        "scheduler:" + value.digest(), "agent_loop", value.schema_id, value.canonical_bytes()
    )


def envelope_bytes(records: tuple[Record, ...]) -> bytes:
    """The exact bound domain: ordered complete native Record transport, not SQL overhead."""
    return json.dumps(
        [
            [r.record_id, r.owner, r.schema_id, base64.b64encode(r.canonical_bytes).decode()]
            for r in records
        ],
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()


def configuration(
    tenant: str,
    principal: str,
    draft: NativeScheduleDraft,
    origin: OriginSelection,
    lease_duration_ns: int,
) -> Configuration:
    schedule_ref = reference("native-scheduler-definition-v1", draft.model_dump(mode="json"))
    policy_ref = reference("native-scheduler-policy-v1", [draft.schedule_id, draft.missed_policy])
    bound_ref = reference(
        "native-scheduler-bound-v1", [draft.schedule_id, draft.bound.model_dump()]
    )
    return Configuration(
        tenant=tenant,
        principal=principal,
        draft=draft,
        origin=origin,
        lease_duration_ns=lease_duration_ns,
        schedule=ScheduleDefinitionHead(
            schedule_id=draft.schedule_id,
            schedule_revision="0",
            head=schedule_ref.head,
            fingerprint=schedule_ref.fingerprint,
        ),
        policy=MissedOccurrencePolicyHead(
            policy_id=draft.schedule_id + "/missed-policy",
            policy_revision="0",
            head=policy_ref.head,
            fingerprint=policy_ref.fingerprint,
            kind=draft.missed_policy,
        ),
        bound=SchedulerIntervalBoundHead(
            head_id=bound_ref.head,
            predecessor=Absent(),
            generation=0,
            bound=draft.bound,
            owner_id=principal,
            authority_epoch="native-pg:" + tenant,
            broker_generation="native-pg-scheduler.v1",
            runtime_graph_generation="native-pg.v1",
            canonicalization_version=CANONICAL_VERSION,
        ),
    )


def coordinate(value: int) -> DueCoordinate:
    return DueCoordinate(
        coordinate_policy_version=COORDINATE_VERSION, canonical_coordinate=str(value)
    )


def prepare_members(
    config: Configuration, boundary: SchedulerEligibilityBoundary
) -> SchedulerEligibilityManifest:
    """Finite half-open fixed interval; identities/manifests use existing pure contracts."""
    draft = config.draft
    start = int(boundary.previous_due_boundary.canonical_coordinate)
    cutoff = int(boundary.cutoff_due_coordinate.canonical_coordinate)
    first = max(0, (start - draft.start_ns + draft.period_ns - 1) // draft.period_ns)
    end = max(first, (cutoff - draft.start_ns + draft.period_ns - 1) // draft.period_ns)
    members = []
    for index in range(first, end):
        due = coordinate(draft.start_ns + index * draft.period_ns)
        identity = occurrence_identity(config.schedule, due)
        members.append(undisposed_occurrence(config.schedule, due, identity + "/UNDISPOSED"))
    return eligible_manifest(boundary, tuple(members))


def lineage(
    primitive: Primitive, subject: ExecutionLineageSubject
) -> tuple[SchedulerLineageView, SchedulerRootReference]:
    parent = reference("native-scheduler-parent-v1", primitive.model_dump(mode="json"))
    run_id = reference("native-scheduled-run-v1", [parent.model_dump(), subject.model_dump()]).head
    fields = {
        "subject": subject.model_dump(mode="json"),
        "initial_run_id": run_id,
        "schedule_id": primitive.boundary.schedule_definition_head.schedule_id,
        "schedule_revision": primitive.boundary.schedule_definition_head.schedule_revision,
        "policy_revision": primitive.boundary.missed_occurrence_policy_head.policy_revision,
    }
    root_ref = reference("execution-lineage-v1", fields)
    binding = ExecutionLineageBinding(
        initial_run_id=run_id,
        schedule_id=primitive.boundary.schedule_definition_head.schedule_id,
        schedule_revision=primitive.boundary.schedule_definition_head.schedule_revision,
        policy_revision=primitive.boundary.missed_occurrence_policy_head.policy_revision,
        subject=subject,
        root_id=root_ref.head,
        root_fingerprint=root_ref.fingerprint,
        lineage_head=reference("native-lineage-head-v1", fields).head,
        current_run_id=run_id,
    )
    epoch = reference("native-physical-root-v1", root_ref.model_dump())
    view = SchedulerLineageView(
        lineage=binding,
        physical_root=PhysicalRootBinding(
            selector_id=root_ref.head + "/selector",
            selector_head=epoch.head,
            selector_version=0,
            current_epoch_id=epoch.head,
            current_epoch_head=epoch.head,
        ),
        lease=GenesisLease(lease_head=root_ref.head + "/unleased"),
        predecessor_rollover=Absent(),
    )
    root = SchedulerRootReference(
        root_id=binding.root_id,
        root_fingerprint=binding.root_fingerprint,
        initial_run_id=run_id,
        subject_schema_version="chiplog.execution-lineage-subject.v1",
        subject_canonical_base64=base64.b64encode(subject.canonical_bytes()).decode(),
    )
    return view, root


def execution_members(
    primitive: Primitive,
    manifest: SchedulerEligibilityManifest,
    runs: list[tuple[SchedulerLineageView, SchedulerRootReference, ExecutionRun]],
) -> tuple[NativeOccurrenceMember, ...]:
    parent = reference("native-scheduler-parent-v1", primitive.model_dump(mode="json"))
    result: list[NativeOccurrenceMember] = []
    subjects = execution_subjects(primitive.boundary, manifest, manifest.members)
    for subject, (view, root, run) in zip(subjects, runs, strict=True):
        materialization = MaterializationIdentity(
            materialization_id=view.lineage.root_id,
            primitive_domain_fingerprint=reference(
                "native-materialization-v1", [parent.model_dump(), subject.model_dump()]
            ).fingerprint,
        )
        members = (
            manifest.members
            if subject.kind == "COALESCED"
            else tuple(m for m in manifest.members if m.occurrence_id == subject.occurrence_id)
        )
        for occurrence in members:
            disposition = (
                CoalescedDisposition(
                    occurrence=occurrence,
                    aggregate_id=subject.aggregate_id,
                    manifest_fingerprint=subject.manifest_fingerprint,
                    materialization=materialization,
                )
                if subject.kind == "COALESCED"
                else IndividualDisposition(occurrence=occurrence, materialization=materialization)
            )
            result.append(
                NativeOccurrenceMember(
                    disposition=disposition,
                    execution=NativeScheduledExecution(
                        run_id=run.run_id,
                        initial_run=Present(head=run.head, fingerprint=run.digest()),
                        root=root,
                    ),
                )
            )
    return tuple(result)


def overflow(
    primitive: Primitive,
    manifest: SchedulerEligibilityManifest,
    dimension: Literal["MEMBER_COUNT", "MANIFEST_BYTES", "SERIALIZED_BATCH_BYTES"],
    actual: int,
    limit: int,
) -> NativeOverflowInterval:
    hold = reference(
        "native-scheduler-hold-v1", [primitive.model_dump(mode="json"), dimension, actual, limit]
    )
    return NativeOverflowInterval(
        hold=SchedulerOverflowHold(
            hold=hold,
            command=primitive.command,
            boundary=primitive.boundary,
            bound_head=primitive.bound,
            exceeded_dimension=dimension,
            actual_value=actual,
            limit=limit,
            evidence=FullEligibilityEvidence(manifest=manifest),
            operator_recovery_owner=primitive.principal,
            state=OverflowActive(),
        )
    )


Interval.model_rebuild()
