"""Pure complete scheduler batch construction over the real RunRecord model.

Candidates are not publications. Broker authentication, exact-current reads,
decision selection, integrity joins and atomic materialization remain mandatory.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Annotated, Literal

from pydantic import Field

from .contracts import BudgetPolicy, EndpointSelection, RunRecord, SchedulerRootReference
from .domain import validate_record
from .recovery_contracts import (
    Absent,
    Digest,
    ExecutionLineageBinding,
    ExecutionLineageSubject,
    Identity,
    PhysicalRootBinding,
    Present,
    RecoveryDTO,
)
from .scheduler_contracts import (
    CoalescedDisposition,
    DecideIntervalCommand,
    FullEligibilityEvidence,
    GenesisLease,
    IndividualDisposition,
    IntervalBranch,
    MaterializationCommitment,
    MaterializationIdentity,
    OccurrenceDisposition,
    OverflowActive,
    OverflowResolved,
    ResolveIntervalCommand,
    ScheduledIntervalDecision,
    SchedulerCommandIdentity,
    SchedulerEligibilityBoundary,
    SchedulerEligibilityManifest,
    SchedulerIntervalBoundHead,
    SchedulerIntervalResolutionDecision,
    SchedulerOverflowHold,
    SkippedDisposition,
    StreamingEligibilityEvidence,
    UndisposedOccurrence,
)
from .scheduler_domain import (
    SchedulerDomainError,
    eligibility_overflows,
    execution_subjects,
    policy_branch,
    verify_manifest,
)

INTERVAL_SCHEMA: Literal["chiplog.scheduler.interval-candidate.v1"] = (
    "chiplog.scheduler.interval-candidate.v1"
)
SCHEMA_DAG: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("parent_primitive", ()),
    ("parent_identity", ("parent_primitive",)),
    ("batch_primitive", ("parent_identity",)),
    ("genesis_lease", ("batch_primitive",)),
    ("lineage", ("batch_primitive",)),
    ("physical_epoch", ("lineage",)),
    ("physical_selector", ("physical_epoch",)),
    ("run", ("batch_primitive",)),
    ("initialization", ("run", "lineage", "physical_selector", "genesis_lease")),
    ("reciprocal", ("initialization",)),
    ("companion", ("physical_epoch", "physical_selector", "genesis_lease")),
    ("finalized_members", ("initialization", "reciprocal", "companion")),
    ("batch_envelope", ("finalized_members",)),
    ("interval_envelope", ("batch_envelope", "parent_identity")),
)


def _bytes(value: object) -> bytes:
    def order(item: object) -> object:
        if isinstance(item, dict):
            return {
                key: order(item[key]) for key in sorted(item, key=lambda key: (key != "kind", key))
            }
        if isinstance(item, (list, tuple)):
            return [order(member) for member in item]
        return item

    return json.dumps(order(value), ensure_ascii=False, separators=(",", ":")).encode()


def _hash(domain: str, value: object) -> str:
    return hashlib.sha256(_bytes([domain, value])).hexdigest()


def _reference(domain: str, value: object) -> Present:
    fingerprint = hashlib.sha256(_bytes(value)).hexdigest()
    return Present(head=domain + ":" + fingerprint, fingerprint=fingerprint)


def validate_schema_dag(graph: tuple[tuple[str, tuple[str, ...]], ...]) -> None:
    names = [name for name, _ in graph]
    if len(set(names)) != len(names):
        raise SchedulerDomainError("duplicate canonical domain")
    dependencies = dict(graph)
    active: set[str] = set()
    complete: set[str] = set()

    def visit(name: str) -> None:
        if name not in dependencies or name in active:
            raise SchedulerDomainError("unknown or cyclic canonical domain")
        if name in complete:
            return
        active.add(name)
        for dependency in dependencies[name]:
            visit(dependency)
        active.remove(name)
        complete.add(name)

    for name in names:
        visit(name)
    if graph != SCHEMA_DAG:
        raise SchedulerDomainError("canonical dependency manifest differs from registered DAG")


class SchedulerRunInputs(RecoveryDTO):
    tenant: Identity
    principal: Identity
    prompt: Identity
    policy: BudgetPolicy
    origin: EndpointSelection
    contour_head: Identity
    policy_head: Identity
    worker_session: Identity
    authority_epoch: Identity


class IntervalParentPrimitive(RecoveryDTO):
    """No child-derived identity, envelope commitment or commitment slot exists here."""

    kind: Literal["ORDINARY", "RESOLUTION"]
    command: SchedulerCommandIdentity
    boundary: SchedulerEligibilityBoundary
    current_bound: SchedulerIntervalBoundHead
    original_bound: SchedulerIntervalBoundHead
    original_hold: Annotated[Absent | Present, Field(discriminator="kind")]
    operator_proof: Annotated[Absent | Present, Field(discriminator="kind")]
    manifest: SchedulerEligibilityManifest
    branch: IntervalBranch
    run_inputs: SchedulerRunInputs


class ScheduledBatchPrimitiveDomainV1(RecoveryDTO):
    parent_decision: Present
    command: SchedulerCommandIdentity
    boundary: SchedulerEligibilityBoundary
    bound_head: SchedulerIntervalBoundHead
    manifest: SchedulerEligibilityManifest
    subject: ExecutionLineageSubject
    initial_run_id: Identity
    root_id: Identity
    root_fingerprint: Digest
    run_inputs: SchedulerRunInputs


class SchedulerCanonicalMember(RecoveryDTO):
    record_kind: Identity
    record_id: Identity
    schema_id: Identity
    canonical_base64: Identity
    fingerprint: Digest


class MaterializedOccurrence(RecoveryDTO):
    primitive: ScheduledBatchPrimitiveDomainV1
    run: RunRecord
    commitment: MaterializationCommitment
    members: tuple[SchedulerCanonicalMember, ...]


class IntervalCandidate(RecoveryDTO):
    """Complete owner bytes; exact-current journal selection has not occurred."""

    schema_id: Literal["chiplog.scheduler.interval-candidate.v1"] = INTERVAL_SCHEMA
    parent_primitive: IntervalParentPrimitive | None
    result: ScheduledIntervalDecision | SchedulerIntervalResolutionDecision | SchedulerOverflowHold
    occurrences: tuple[MaterializedOccurrence, ...]
    records: tuple[SchedulerCanonicalMember, ...]
    batch_fingerprint: Digest
    serialized_batch_bytes: int = Field(ge=0)


def _member(kind: str, reference: str, value: object) -> SchedulerCanonicalMember:
    payload = _bytes(value)
    return SchedulerCanonicalMember(
        record_kind=kind,
        record_id=reference,
        schema_id="chiplog.scheduler." + kind + ".v1",
        canonical_base64=base64.b64encode(payload).decode(),
        fingerprint=hashlib.sha256(payload).hexdigest(),
    )


def _run(inputs: SchedulerRunInputs, root: ExecutionLineageBinding) -> RunRecord:
    run = RunRecord(
        tenant=inputs.tenant,
        principal=inputs.principal,
        run_id=root.initial_run_id,
        state="CREATED",
        head="pending",
        predecessor=None,
        prompt=inputs.prompt,
        policy=inputs.policy,
        origin=inputs.origin,
        contour_head=inputs.contour_head,
        policy_head=inputs.policy_head,
        worker_session=inputs.worker_session,
        root_binding=SchedulerRootReference(
            root_id=root.root_id,
            subject_canonical_base64=base64.b64encode(root.subject.canonical_bytes()).decode(),
            subject_schema_version="chiplog.execution-lineage-subject.v1",
            root_fingerprint=root.root_fingerprint,
            initial_run_id=root.initial_run_id,
        ),
        event="RunCreated",
    )
    run = run.model_copy(update={"head": "loop:" + run.digest()})
    validate_record(None, run)
    return run


def materialize_occurrence(
    parent: IntervalParentPrimitive,
    parent_ref: Present,
    subject: ExecutionLineageSubject,
) -> MaterializedOccurrence:
    expected_parent = _reference("scheduler-parent-v1", parent.model_dump(mode="json"))
    if parent_ref != expected_parent:
        raise SchedulerDomainError("parent identity differs from primitive domain")
    if parent.branch != policy_branch(
        parent.boundary.missed_occurrence_policy_head.kind,
        len(parent.manifest.members),
    ) or subject not in execution_subjects(
        parent.boundary, parent.manifest, parent.manifest.members
    ):
        raise SchedulerDomainError("subject or branch differs from exact eligible policy algebra")
    schedule = parent.boundary.schedule_definition_head
    run_id = "scheduled-run-v1:" + _hash(
        "scheduled-run-v1",
        [
            parent_ref.model_dump(mode="json"),
            subject.model_dump(mode="json"),
        ],
    )
    root_fields = {
        "subject": subject.model_dump(mode="json"),
        "initial_run_id": run_id,
        "schedule_id": schedule.schedule_id,
        "schedule_revision": schedule.schedule_revision,
        "policy_revision": parent.boundary.missed_occurrence_policy_head.policy_revision,
    }
    root_fingerprint = _hash("execution-lineage-v1", root_fields)
    root_id = "execution-lineage-v1:" + root_fingerprint
    lineage_head = "execution-lineage-head-v1:" + _hash(
        "execution-lineage-head-v1",
        {
            **root_fields,
            "root_id": root_id,
            "root_fingerprint": root_fingerprint,
            "current_run_id": run_id,
        },
    )
    lineage = ExecutionLineageBinding(
        root_id=root_id,
        subject=subject,
        root_fingerprint=root_fingerprint,
        lineage_head=lineage_head,
        initial_run_id=run_id,
        current_run_id=run_id,
        schedule_id=schedule.schedule_id,
        schedule_revision=schedule.schedule_revision,
        policy_revision=parent.boundary.missed_occurrence_policy_head.policy_revision,
    )
    primitive = ScheduledBatchPrimitiveDomainV1(
        parent_decision=parent_ref,
        command=parent.command,
        boundary=parent.boundary,
        bound_head=parent.current_bound,
        manifest=parent.manifest,
        subject=subject,
        initial_run_id=run_id,
        root_id=root_id,
        root_fingerprint=root_fingerprint,
        run_inputs=parent.run_inputs,
    )
    primitive_ref = _reference("scheduled-batch-primitive-v1", primitive.model_dump(mode="json"))
    materialization = MaterializationIdentity(
        materialization_id=primitive_ref.head,
        primitive_domain_fingerprint=primitive_ref.fingerprint,
    )
    genesis = GenesisLease(
        lease_head="execution-root-lease-genesis-v1:"
        + _hash(
            "execution-root-lease-genesis-v1",
            primitive.model_dump(mode="json"),
        )
    )
    epoch_id = "physical-root-genesis-v1:" + _hash(
        "physical-root-genesis-v1", [root_id, parent_ref.head]
    )
    epoch_body = {
        "epoch_id": epoch_id,
        "execution_lineage_root_id": root_id,
        "execution_lineage_root_fingerprint": root_fingerprint,
        "observed_lineage_head": lineage_head,
        "observed_current_run_id": run_id,
        "predecessor_epoch": {"kind": "ABSENT"},
        "authority_epoch": parent.run_inputs.authority_epoch,
    }
    epoch_ref = _reference("physical-root-head-v1", epoch_body)
    selector_id = "physical-root-selector-v1:" + _hash("physical-root-selector-v1", root_id)
    selector_body = {
        "lineage_id": root_id,
        "selector_id": selector_id,
        "selector_version": 0,
        "current_epoch_id": epoch_id,
        "current_epoch_head": epoch_ref.head,
    }
    selector_ref = _reference("physical-root-selector-head-v1", selector_body)
    physical = PhysicalRootBinding(
        selector_id=selector_id,
        selector_head=selector_ref.head,
        selector_version=0,
        current_epoch_id=epoch_id,
        current_epoch_head=epoch_ref.head,
    )
    run = _run(parent.run_inputs, lineage)
    initialization = {
        "parent": parent_ref.model_dump(mode="json"),
        "primitive": primitive_ref.model_dump(mode="json"),
        "lineage": lineage.model_dump(mode="json"),
        "run_id": run_id,
        "run_head": run.head,
        "physical_root": physical.model_dump(mode="json"),
        "genesis_lease": genesis.model_dump(mode="json"),
        "bound_head": parent.current_bound.model_dump(mode="json"),
        "boundary": parent.boundary.model_dump(mode="json"),
        "manifest_fingerprint": parent.manifest.fingerprint,
    }
    init_ref = _reference("scheduler-run-initialization-v1", initialization)
    reciprocal = {
        "initialization": init_ref.model_dump(mode="json"),
        "run_id": run_id,
        "run_head": run.head,
        "lineage": lineage.model_dump(mode="json"),
        "physical_root": physical.model_dump(mode="json"),
        "genesis_lease": genesis.model_dump(mode="json"),
    }
    reciprocal_ref = _reference("scheduler-root-run-reciprocal-v1", reciprocal)
    dispositions: tuple[OccurrenceDisposition, ...]
    if subject.kind == "INDIVIDUAL":
        selected = tuple(
            item for item in parent.manifest.members if item.occurrence_id == subject.occurrence_id
        )
        if len(selected) != 1:
            raise SchedulerDomainError("individual subject lacks exactly one eligible occurrence")
        dispositions = (
            IndividualDisposition(occurrence=selected[0], materialization=materialization),
        )
    else:
        if (
            len(parent.manifest.members) < 2
            or subject.manifest_fingerprint != parent.manifest.fingerprint
        ):
            raise SchedulerDomainError(
                "coalesced subject differs from complete multi-member manifest"
            )
        dispositions = tuple(
            CoalescedDisposition(
                occurrence=item,
                aggregate_id=subject.aggregate_id,
                manifest_fingerprint=parent.manifest.fingerprint,
                materialization=materialization,
            )
            for item in parent.manifest.members
        )
    members = (
        _member("batch-primitive", primitive_ref.head, primitive.model_dump(mode="json")),
        _member("stable-lineage", lineage_head, lineage.model_dump(mode="json")),
        _member("physical-root", epoch_ref.head, epoch_body),
        _member("physical-selector", selector_ref.head, selector_body),
        _member(
            "root-lease-genesis",
            genesis.lease_head,
            {
                "lineage": lineage.model_dump(mode="json"),
                "physical_root": physical.model_dump(mode="json"),
                "lease": genesis.model_dump(mode="json"),
                "run_id": run_id,
            },
        ),
        SchedulerCanonicalMember(
            record_kind="agent_loop.run",
            record_id=run.head,
            schema_id="chiplog.agent-loop.record.v1",
            canonical_base64=base64.b64encode(run.canonical_bytes()).decode(),
            fingerprint=hashlib.sha256(run.canonical_bytes()).hexdigest(),
        ),
        _member("run-initialization", init_ref.head, initialization),
        _member("root-run-reciprocal", reciprocal_ref.head, reciprocal),
        *(
            _member(
                "occurrence-disposition",
                _reference(
                    "occurrence-disposition-v1",
                    item.model_dump(mode="json"),
                ).head,
                item.model_dump(mode="json"),
            )
            for item in dispositions
        ),
    )
    if subject.kind == "COALESCED":
        members = (
            *members,
            _member(
                "coalesced-aggregate",
                subject.aggregate_id,
                {
                    "subject": subject.model_dump(mode="json"),
                    "boundary": parent.boundary.model_dump(mode="json"),
                    "manifest": parent.manifest.model_dump(mode="json"),
                },
            ),
        )
    companion_data = [
        item.model_dump(mode="json")
        for item in members
        if item.record_kind in {"physical-root", "physical-selector", "root-lease-genesis"}
    ]
    companion = _reference("scheduler-epoch-companion-v1", companion_data)
    members = (*members, _member("epoch-companion", companion.head, companion_data))
    finalized_fingerprint = _hash(
        "scheduled-batch-finalized-members-v1",
        [item.model_dump(mode="json") for item in members],
    )
    commitment = MaterializationCommitment(
        kind=subject.kind,
        parent_decision=parent_ref,
        subject=subject,
        dispositions=dispositions,
        lineage=lineage,
        initial_run=Present(head=run.head, fingerprint=run.digest()),
        initialization=init_ref,
        reciprocal_run_root=reciprocal_ref,
        physical_root=physical,
        physical_epoch=epoch_ref,
        genesis_lease=genesis,
        companion_manifest=companion,
        primitive_domain_fingerprint=primitive_ref.fingerprint,
        finalized_members_fingerprint=finalized_fingerprint,
        batch_fingerprint=_hash(
            "scheduled-batch-envelope-v1",
            {
                "finalized_members": finalized_fingerprint,
                "companion": companion.model_dump(mode="json"),
                "schema_dag": _reference("scheduler-schema-dag-v1", SCHEMA_DAG).model_dump(
                    mode="json",
                ),
            },
        ),
        schema_dependency_manifest=_reference("scheduler-schema-dag-v1", SCHEMA_DAG),
    )
    return MaterializedOccurrence(
        primitive=primitive, run=run, commitment=commitment, members=members
    )


def _serialized(records: tuple[SchedulerCanonicalMember, ...]) -> bytes:
    """Registered whole owner-batch wire domain, including every full record byte."""
    return _bytes([item.model_dump(mode="json") for item in records])


def _compile(
    parent: IntervalParentPrimitive, source: tuple[UndisposedOccurrence, ...]
) -> IntervalCandidate:
    validate_schema_dag(SCHEMA_DAG)
    verify_manifest(parent.boundary, parent.manifest, source)
    parent_ref = _reference("scheduler-parent-v1", parent.model_dump(mode="json"))
    subjects = execution_subjects(parent.boundary, parent.manifest, source)
    occurrences = tuple(materialize_occurrence(parent, parent_ref, subject) for subject in subjects)
    dispositions: tuple[OccurrenceDisposition, ...] = tuple(
        disposition
        for occurrence in occurrences
        for disposition in occurrence.commitment.dispositions
    )
    if parent.branch == "SKIP_ALL":
        dispositions = tuple(
            SkippedDisposition(occurrence=item, policy_decision=parent_ref)
            for item in parent.manifest.members
        )
    common = {
        "decision": parent_ref,
        "command": parent.command,
        "branch": parent.branch,
        "boundary": parent.boundary,
        "manifest": parent.manifest,
        "dispositions": dispositions,
        "materializations": tuple(item.commitment for item in occurrences),
        "resulting_boundary": parent.boundary.cutoff_due_coordinate,
        "complete_commitment": "0" * 64,
    }
    result: ScheduledIntervalDecision | SchedulerIntervalResolutionDecision
    if parent.kind == "ORDINARY":
        result = ScheduledIntervalDecision.model_validate(
            {**common, "bound_head": parent.current_bound}
        )
    else:
        if not isinstance(parent.original_hold, Present):
            raise SchedulerDomainError("resolution lacks original hold identity")
        result = SchedulerIntervalResolutionDecision.model_validate(
            {
                **common,
                "original_hold": parent.original_hold,
                "original_bound_head": parent.original_bound,
                "successor_bound_head": parent.current_bound,
            }
        )
    records = (
        _member("interval-parent", parent_ref.head, parent.model_dump(mode="json")),
        *(member for occurrence in occurrences for member in occurrence.members),
        *(
            _member(
                "occurrence-disposition",
                _reference(
                    "occurrence-disposition-v1",
                    item.model_dump(mode="json"),
                ).head,
                item.model_dump(mode="json"),
            )
            for item in dispositions
            if item.kind == "SKIPPED"
        ),
    )
    if parent.kind == "RESOLUTION":
        records = (
            *records,
            _member(
                "overflow-resolution",
                parent_ref.head + ":hold-resolution",
                {
                    "original_hold": parent.original_hold.model_dump(mode="json"),
                    "state": OverflowResolved(resolution_decision=parent_ref).model_dump(
                        mode="json"
                    ),
                },
            ),
        )
    fingerprint = _hash(
        "scheduler-interval-envelope-v1",
        {
            "complete_non_envelope_records": [item.model_dump(mode="json") for item in records],
            "result_without_commitment": result.model_dump(
                mode="json", exclude={"complete_commitment"}
            ),
        },
    )
    result = result.model_copy(update={"complete_commitment": fingerprint})
    records = (
        *records,
        _member(
            "interval-result",
            parent_ref.head + ":result",
            result.model_dump(mode="json"),
        ),
    )
    if len({item.record_id for item in records}) != len(records):
        raise SchedulerDomainError("duplicate complete-batch record identity")
    return IntervalCandidate(
        parent_primitive=parent,
        result=result,
        occurrences=occurrences,
        records=records,
        batch_fingerprint=fingerprint,
        serialized_batch_bytes=len(_serialized(records)),
    )


def prepare_interval(
    command: DecideIntervalCommand,
    current_bound: SchedulerIntervalBoundHead,
    source: tuple[UndisposedOccurrence, ...],
    run_inputs: SchedulerRunInputs,
    enumeration_proof: Present,
    *,
    active_hold: SchedulerOverflowHold | None = None,
) -> IntervalCandidate:
    if not isinstance(command.manifest, SchedulerEligibilityManifest):
        raise SchedulerDomainError("full interval materialization requires full manifest")
    if active_hold is not None and active_hold.state.kind == "ACTIVE":
        raise SchedulerDomainError("active overflow hold blocks ordinary interval and amendment")
    if command.bound_head != current_bound:
        raise SchedulerDomainError("current interval bound head changed")
    manifest = verify_manifest(command.boundary, command.manifest, source)
    parent = IntervalParentPrimitive(
        kind="ORDINARY",
        command=command.identity,
        boundary=command.boundary,
        current_bound=current_bound,
        original_bound=current_bound,
        original_hold=Absent(),
        operator_proof=Absent(),
        manifest=manifest,
        branch=policy_branch(
            command.boundary.missed_occurrence_policy_head.kind,
            len(manifest.members),
        ),
        run_inputs=run_inputs,
    )
    exceeded = eligibility_overflows(manifest, current_bound.bound)
    dimension: Literal["MEMBER_COUNT", "MANIFEST_BYTES", "SERIALIZED_BATCH_BYTES"]
    if exceeded:
        dimension, actual, limit = exceeded[0].dimension, exceeded[0].actual, exceeded[0].limit
    else:
        candidate = _compile(parent, source)
        if candidate.serialized_batch_bytes <= current_bound.bound.max_serialized_batch_bytes:
            return candidate
        dimension, actual, limit = (
            "SERIALIZED_BATCH_BYTES",
            candidate.serialized_batch_bytes,
            current_bound.bound.max_serialized_batch_bytes,
        )
    evidence: FullEligibilityEvidence | StreamingEligibilityEvidence
    if len(manifest.canonical_bytes()) <= current_bound.bound.max_manifest_bytes:
        evidence = FullEligibilityEvidence(manifest=manifest)
    else:
        evidence = StreamingEligibilityEvidence(
            manifest_digest=manifest.fingerprint,
            member_count=len(manifest.members),
            first_member=Present(
                head=manifest.members[0].undisposed_head,
                fingerprint=manifest.members[0].undisposed_fingerprint,
            )
            if manifest.members
            else Absent(),
            last_member=Present(
                head=manifest.members[-1].undisposed_head,
                fingerprint=manifest.members[-1].undisposed_fingerprint,
            )
            if manifest.members
            else Absent(),
            order_contract_version="chiplog.scheduler.numeric-due.v1",
            enumeration_completeness_proof=enumeration_proof,
        )
    return _overflow_candidate(
        command, current_bound, evidence, dimension, actual, limit, run_inputs.principal
    )


def _overflow_candidate(
    command: DecideIntervalCommand,
    current_bound: SchedulerIntervalBoundHead,
    evidence: FullEligibilityEvidence | StreamingEligibilityEvidence,
    dimension: Literal["MEMBER_COUNT", "MANIFEST_BYTES", "SERIALIZED_BATCH_BYTES"],
    actual: int,
    limit: int,
    operator_recovery_owner: str,
) -> IntervalCandidate:
    hold_data = {
        "command": command.identity.model_dump(mode="json"),
        "boundary": command.boundary.model_dump(mode="json"),
        "bound_head": current_bound.model_dump(mode="json"),
        "dimension": dimension,
        "actual": actual,
        "limit": limit,
        "evidence": evidence.model_dump(mode="json"),
        "operator": operator_recovery_owner,
    }
    hold_ref = _reference("scheduler-overflow-hold-v1", hold_data)
    hold = SchedulerOverflowHold(
        hold=hold_ref,
        command=command.identity,
        boundary=command.boundary,
        bound_head=current_bound,
        exceeded_dimension=dimension,
        actual_value=actual,
        limit=limit,
        evidence=evidence,
        operator_recovery_owner=operator_recovery_owner,
        state=OverflowActive(),
    )
    records = (_member("overflow-hold", hold_ref.head, hold.model_dump(mode="json")),)
    return IntervalCandidate(
        parent_primitive=None,
        result=hold,
        occurrences=(),
        records=records,
        batch_fingerprint=_hash("scheduler-overflow-envelope-v1", hold.model_dump(mode="json")),
        serialized_batch_bytes=len(_serialized(records)),
    )


def prepare_streamed_overflow(
    command: DecideIntervalCommand,
    current_bound: SchedulerIntervalBoundHead,
    evidence: FullEligibilityEvidence | StreamingEligibilityEvidence,
    member_count: int,
    manifest_bytes: int,
    operator_recovery_owner: str,
) -> IntervalCandidate:
    """Complete source-derived evidence only; not an arithmetic placeholder."""
    if command.bound_head != current_bound:
        raise SchedulerDomainError("streamed overflow bound head changed")
    if member_count > current_bound.bound.max_member_count:
        dimension: Literal["MEMBER_COUNT", "MANIFEST_BYTES", "SERIALIZED_BATCH_BYTES"] = (
            "MEMBER_COUNT"
        )
        actual, limit = member_count, current_bound.bound.max_member_count
    elif manifest_bytes > current_bound.bound.max_manifest_bytes:
        dimension = "MANIFEST_BYTES"
        actual, limit = manifest_bytes, current_bound.bound.max_manifest_bytes
    else:
        raise SchedulerDomainError("streamed evidence is within bounds; ordinary branch required")
    if (manifest_bytes <= current_bound.bound.max_manifest_bytes) != (
        evidence.kind == "FULL_MANIFEST"
    ):
        raise SchedulerDomainError("overflow evidence form does not match exact manifest bound")
    return _overflow_candidate(
        command, current_bound, evidence, dimension, actual, limit, operator_recovery_owner
    )


def prepare_resolution(
    command: ResolveIntervalCommand,
    current_bound: SchedulerIntervalBoundHead,
    source: tuple[UndisposedOccurrence, ...],
    run_inputs: SchedulerRunInputs,
) -> IntervalCandidate:
    hold = command.active_hold
    if hold.state.kind != "ACTIVE" or command.boundary != hold.boundary:
        raise SchedulerDomainError("resolution requires exact active hold and unchanged boundary")
    if (
        command.successor_bound != current_bound
        or current_bound.generation <= hold.bound_head.generation
        or current_bound.head_id == hold.bound_head.head_id
    ):
        raise SchedulerDomainError("resolution requires exact current successor bound")
    manifest = verify_manifest(command.boundary, command.complete_manifest, source)
    expected_fingerprint = (
        hold.evidence.manifest.fingerprint
        if hold.evidence.kind == "FULL_MANIFEST"
        else hold.evidence.manifest_digest
    )
    if manifest.fingerprint != expected_fingerprint:
        raise SchedulerDomainError("held eligible membership changed")
    parent = IntervalParentPrimitive(
        kind="RESOLUTION",
        command=command.identity,
        boundary=command.boundary,
        current_bound=current_bound,
        original_bound=hold.bound_head,
        original_hold=hold.hold,
        operator_proof=command.operator_proof,
        manifest=manifest,
        branch=policy_branch(
            command.boundary.missed_occurrence_policy_head.kind,
            len(manifest.members),
        ),
        run_inputs=run_inputs,
    )
    candidate = _compile(parent, source)
    if (
        eligibility_overflows(manifest, current_bound.bound)
        or candidate.serialized_batch_bytes > current_bound.bound.max_serialized_batch_bytes
    ):
        raise SchedulerDomainError("successor bound does not admit complete resolution")
    return candidate
