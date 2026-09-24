"""Closed record bodies and pure joins for execution-successor publication.

These consumers authenticate neither the recovery cut nor a selected decision.
They only make the member order, content-derived identities, and inherited
preimages explicit before an owner publication is attempted.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Literal, Protocol

from pydantic import Field, ValidationError, model_validator

from .call_acceptance_contracts import CallSubjectHead
from .execution_recovery_contracts import (
    ExecutionSuccessorEdge,
    NonSchedulerSuccessor,
    PreparedExecutionSuccessor,
    PrepareExecutionSuccessor,
    SchedulerSuccessorAdvance,
)
from .execution_recovery_observations import ExecutionSuspensionBaseline, ExecutionSuspensionPair
from .recovery_contracts import (
    Absent,
    ExecutionLineageBinding,
    Identity,
    LeaseBinding,
    PhysicalRootBinding,
    Present,
    RecoveryDTO,
    RolloverPredecessor,
    RunExecutionFence,
    SchedulerExecutionFence,
    UInt64,
)
from .recovery_frontier_contracts import (
    FrozenRunBindings,
    ObligationObservation,
    PendingCallFrontier,
)
from .recovery_record_contracts import (
    AGENT_LOOP_OWNER,
    RecoveryRecordIntegrityError,
    RecoveryRecordMember,
    decode_recovery_record_member,
    decode_successor_edge,
)
from .scheduler_contracts import GenesisLease
from .successor_ancestry_contracts import (
    ReadSuccessorRunAncestryResultV1,
    ReadSuccessorRunAncestryV1,
    SuccessorAncestryIntegrityError,
    validate_successor_run_ancestry_exchange,
)

SUCCESSOR_INITIALIZATION_SCHEMA: Literal["chiplog.execution.successor-initialization.v1"] = (
    "chiplog.execution.successor-initialization.v1"
)
SCHEDULER_LINEAGE_ADVANCE_SCHEMA: Literal["chiplog.scheduler.successor-lineage-advance.v1"] = (
    "chiplog.scheduler.successor-lineage-advance.v1"
)
SCHEDULER_EPOCH_OBSERVATION_SCHEMA: Literal["chiplog.scheduler.successor-epoch-observation.v1"] = (
    "chiplog.scheduler.successor-epoch-observation.v1"
)


class ExecutionSuccessorInitializationRecord(RecoveryDTO):
    schema_id: Literal["chiplog.execution.successor-initialization.v1"] = (
        SUCCESSOR_INITIALIZATION_SCHEMA
    )
    command_id: Identity
    predecessor_run: CallSubjectHead
    successor_run: CallSubjectHead
    source_cut_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    disposition_version: Identity
    observation_frontier: UInt64
    current_bindings: FrozenRunBindings
    execution_fence: RunExecutionFence
    original_obligations: tuple[ObligationObservation, ...]
    inherited_no_retry_boundaries: tuple[CallSubjectHead, ...]
    inherited_pending_branches: tuple[PendingCallFrontier, ...]


class SchedulerSuccessorLineageAdvanceRecord(RecoveryDTO):
    schema_id: Literal["chiplog.scheduler.successor-lineage-advance.v1"] = (
        SCHEDULER_LINEAGE_ADVANCE_SCHEMA
    )
    command_id: Identity
    predecessor_run: CallSubjectHead
    successor_run: CallSubjectHead
    previous_lineage: ExecutionLineageBinding
    next_current_run_id: Identity
    physical_root: PhysicalRootBinding
    lease: LeaseBinding
    source_cut_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class SchedulerSuccessorEpochObservationRecord(RecoveryDTO):
    schema_id: Literal["chiplog.scheduler.successor-epoch-observation.v1"] = (
        SCHEDULER_EPOCH_OBSERVATION_SCHEMA
    )
    command_id: Identity
    predecessor_run: CallSubjectHead
    successor_run: CallSubjectHead
    physical_root: PhysicalRootBinding
    lease: LeaseBinding
    previous_lineage: CallSubjectHead
    advanced_lineage: CallSubjectHead
    source_cut_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class SuccessorRecordRow:
    owner: str
    record_kind: str
    schema_id: str
    record_type: type[RecoveryDTO]


SUCCESSOR_RECORD_ROWS = (
    SuccessorRecordRow(
        AGENT_LOOP_OWNER,
        "SUCCESSOR_INITIALIZATION",
        SUCCESSOR_INITIALIZATION_SCHEMA,
        ExecutionSuccessorInitializationRecord,
    ),
    SuccessorRecordRow(
        AGENT_LOOP_OWNER,
        "SCHEDULER_LINEAGE_ADVANCE",
        SCHEDULER_LINEAGE_ADVANCE_SCHEMA,
        SchedulerSuccessorLineageAdvanceRecord,
    ),
    SuccessorRecordRow(
        AGENT_LOOP_OWNER,
        "SCHEDULER_EPOCH_LINEAGE_OBSERVATION",
        SCHEDULER_EPOCH_OBSERVATION_SCHEMA,
        SchedulerSuccessorEpochObservationRecord,
    ),
)
_ROWS = {(row.owner, row.record_kind, row.schema_id): row for row in SUCCESSOR_RECORD_ROWS}


@dataclass(frozen=True)
class DecodedSuccessorRecordMember:
    member: RecoveryRecordMember
    record: RecoveryDTO


def _require(actual: object, expected: object, reason: str) -> None:
    if actual != expected:
        raise RecoveryRecordIntegrityError(reason)


def _reference(member: RecoveryRecordMember) -> Present:
    return Present(head=member.record_id, fingerprint=member.fingerprint)


def _head(reference: CallSubjectHead) -> Present:
    return reference.revision


def _content_id(schema_id: str, canonical_bytes: bytes) -> str:
    return schema_id + ":" + hashlib.sha256(canonical_bytes).hexdigest()


class _ExecutionRun(Protocol):
    run_id: str
    head: str

    def canonical_bytes(self) -> bytes: ...

    def digest(self) -> str: ...

    def model_copy(self, *, update: dict[str, str]) -> _ExecutionRun: ...


def _run_head(run: _ExecutionRun) -> CallSubjectHead:
    run_id = run.run_id
    head = run.head
    canonical = run.canonical_bytes
    pending = run.model_copy(update={"head": "pending"})
    _require(head, "loop:" + pending.digest(), "execution Run native head differs")
    return CallSubjectHead(
        subject_id=run_id,
        revision=Present(head=head, fingerprint=hashlib.sha256(canonical()).hexdigest()),
    )


def decode_successor_record_member(member: RecoveryRecordMember) -> DecodedSuccessorRecordMember:
    """Decode only one of the three closed successor rows."""
    row = _ROWS.get((member.owner, member.record_kind, member.schema_id))
    if row is None:
        raise RecoveryRecordIntegrityError("unregistered successor record owner, kind, or schema")
    try:
        record = row.record_type.model_validate_json(member.canonical_record_bytes)
    except ValidationError as error:
        raise RecoveryRecordIntegrityError("invalid successor record bytes") from error
    if record.canonical_bytes() != member.canonical_record_bytes:
        raise RecoveryRecordIntegrityError("successor record bytes are not canonical")
    fingerprint = hashlib.sha256(member.canonical_record_bytes).hexdigest()
    _require(member.fingerprint, fingerprint, "successor record fingerprint mismatch")
    _require(
        record.model_dump(mode="json").get("schema_id"),
        row.schema_id,
        "embedded successor schema mismatch",
    )
    _require(
        member.record_id,
        _content_id(row.schema_id, member.canonical_record_bytes),
        "successor record physical ID mismatch",
    )
    return DecodedSuccessorRecordMember(member=member, record=record)


class RetainedSuccessorInput(RecoveryDTO):
    """An independently retained selected input preimage, never an output row."""

    reference: CallSubjectHead
    schema_id: Identity
    canonical_record_bytes: bytes = Field(min_length=1)

    @model_validator(mode="after")
    def exact_preimage_hash(self) -> RetainedSuccessorInput:
        _require(
            self.reference.revision.fingerprint,
            hashlib.sha256(self.canonical_record_bytes).hexdigest(),
            "retained successor input fingerprint mismatch",
        )
        return self


class EpochCreationLineageWitness(RecoveryDTO):
    kind: Literal["EPOCH_CREATION"] = "EPOCH_CREATION"


class PriorSuccessorObservationLineageWitness(RecoveryDTO):
    kind: Literal["PRIOR_SUCCESSOR_OBSERVATION"] = "PRIOR_SUCCESSOR_OBSERVATION"
    observation: RecoveryRecordMember
    ancestry_request: ReadSuccessorRunAncestryV1
    ancestry: ReadSuccessorRunAncestryResultV1


SchedulerLineageContinuityWitness = Annotated[
    EpochCreationLineageWitness | PriorSuccessorObservationLineageWitness,
    Field(discriminator="kind"),
]


class SchedulerSuccessorInputs(RecoveryDTO):
    original_lineage: RetainedSuccessorInput
    current_selector: RetainedSuccessorInput
    selected_epoch: RetainedSuccessorInput
    continuity: SchedulerLineageContinuityWitness


STABLE_LINEAGE_SCHEMA = "chiplog.scheduler.stable-lineage.v1"
PHYSICAL_SELECTOR_SCHEMA = "chiplog.scheduler.physical-selector.v1"
PHYSICAL_ROOT_SCHEMA = "chiplog.scheduler.physical-root.v1"
ROLLOVER_PHYSICAL_SELECTOR_SCHEMA = "chiplog.scheduler.rollover-physical-selector.v1"
ROLLOVER_PHYSICAL_ROOT_SCHEMA = "chiplog.scheduler.rollover-physical-root.v1"


class _GenesisPhysicalRoot(RecoveryDTO):
    epoch_id: Identity
    execution_lineage_root_id: Identity
    execution_lineage_root_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_lineage_head: Identity
    observed_current_run_id: Identity
    predecessor_epoch: Absent
    authority_epoch: Identity


class _GenesisPhysicalSelector(RecoveryDTO):
    lineage_id: Identity
    selector_id: Identity
    selector_version: UInt64
    current_epoch_id: Identity
    current_epoch_head: Identity


class _RolloverPhysicalRoot(RecoveryDTO):
    lineage: ExecutionLineageBinding
    decision: Present
    epoch_id: Identity
    predecessor_epoch_id: Identity
    genesis: GenesisLease
    edge_id: Identity


class _RolloverPhysicalSelector(RecoveryDTO):
    lineage: ExecutionLineageBinding
    decision: Present
    selector_id: Identity
    previous: PhysicalRootBinding
    selector_version: UInt64
    selected_epoch: Present
    rollover: RolloverPredecessor


@dataclass(frozen=True)
class DecodedRetainedSuccessorInput:
    retained: RetainedSuccessorInput
    record: RecoveryDTO
    lineage: ExecutionLineageBinding | None = None
    physical_root: PhysicalRootBinding | None = None
    epoch_root_id: str | None = None
    epoch_root_fingerprint: str | None = None
    observed_lineage_head: str | None = None
    observed_current_run_id: str | None = None
    selected_epoch: Present | None = None


def _scheduler_native_bytes(value: object) -> bytes:
    """The genesis scheduler codec orders `kind` before the other JSON keys."""

    def ordered(item: object) -> object:
        if isinstance(item, dict):
            return {
                key: ordered(item[key])
                for key in sorted(item, key=lambda key: (key != "kind", key))
            }
        if isinstance(item, (list, tuple)):
            return [ordered(member) for member in item]
        return item

    return json.dumps(ordered(value), ensure_ascii=False, separators=(",", ":")).encode()


def _rollover_native_bytes(value: object) -> bytes:
    """Rollover records retain their independently established lexical codec."""

    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def _native_hash(domain: str, value: object) -> str:
    return hashlib.sha256(_scheduler_native_bytes([domain, value])).hexdigest()


def _native_reference(domain: str, value: object) -> Present:
    raw = _scheduler_native_bytes(value)
    fingerprint = hashlib.sha256(raw).hexdigest()
    return Present(head=domain + ":" + fingerprint, fingerprint=fingerprint)


def _decode_native(
    retained: RetainedSuccessorInput,
    record_type: type[RecoveryDTO],
    canonicalizer: Callable[[object], bytes],
) -> RecoveryDTO:
    try:
        record = record_type.model_validate_json(retained.canonical_record_bytes)
    except ValidationError as error:
        raise RecoveryRecordIntegrityError("invalid retained scheduler source bytes") from error
    if canonicalizer(record.model_dump(mode="json")) != retained.canonical_record_bytes:
        raise RecoveryRecordIntegrityError("retained scheduler source bytes are not canonical")
    _require(
        retained.reference.revision.fingerprint,
        hashlib.sha256(retained.canonical_record_bytes).hexdigest(),
        "retained scheduler source fingerprint mismatch",
    )
    return record


def _stable_lineage_id(lineage: ExecutionLineageBinding) -> str:
    fields = {
        "subject": lineage.subject.model_dump(mode="json"),
        "initial_run_id": lineage.initial_run_id,
        "schedule_id": lineage.schedule_id,
        "schedule_revision": lineage.schedule_revision,
        "policy_revision": lineage.policy_revision,
    }
    root_fingerprint = _native_hash("execution-lineage-v1", fields)
    root_id = "execution-lineage-v1:" + root_fingerprint
    expected = {
        **fields,
        "root_id": root_id,
        "root_fingerprint": root_fingerprint,
        "current_run_id": lineage.current_run_id,
    }
    _require(lineage.root_id, root_id, "stable lineage root ID mismatch")
    _require(lineage.root_fingerprint, root_fingerprint, "stable lineage root fingerprint mismatch")
    return "execution-lineage-head-v1:" + _native_hash("execution-lineage-head-v1", expected)


def _expected_reference(retained: RetainedSuccessorInput, subject_id: str, head: str) -> None:
    _require(
        retained.reference.subject_id, subject_id, "retained scheduler source subject mismatch"
    )
    _require(
        retained.reference.revision.head, head, "retained scheduler source physical ID mismatch"
    )


def _project_successor_lineage(
    record: SchedulerSuccessorLineageAdvanceRecord, reference: CallSubjectHead
) -> ExecutionLineageBinding:
    previous = record.previous_lineage
    _require(reference.subject_id, previous.root_id, "successor lineage source subject mismatch")
    return previous.model_copy(
        update={
            "lineage_head": reference.revision.head,
            "current_run_id": record.next_current_run_id,
        }
    )


def decode_retained_successor_input(
    retained: RetainedSuccessorInput,
    expected_role: Literal["original_lineage", "current_selector", "selected_epoch"],
) -> DecodedRetainedSuccessorInput:
    """Decode a fixed scheduler source schema and its native physical identity.

    Rollover root IDs originate in a rollover decision primitive that is outside
    this three-source input.  Here we bind its explicit `epoch_id` and the
    selector's decision-derived shape, then require the retained cross-links.
    """

    schema = retained.schema_id
    if expected_role == "original_lineage" and schema == STABLE_LINEAGE_SCHEMA:
        lineage = _decode_native(retained, ExecutionLineageBinding, _scheduler_native_bytes)
        assert isinstance(lineage, ExecutionLineageBinding)
        _expected_reference(retained, lineage.root_id, _stable_lineage_id(lineage))
        _require(
            lineage.lineage_head, retained.reference.revision.head, "stable lineage head mismatch"
        )
        return DecodedRetainedSuccessorInput(retained, lineage, lineage=lineage)
    if expected_role == "original_lineage" and schema == SCHEDULER_LINEAGE_ADVANCE_SCHEMA:
        record = _decode_native(
            retained, SchedulerSuccessorLineageAdvanceRecord, _scheduler_native_bytes
        )
        assert isinstance(record, SchedulerSuccessorLineageAdvanceRecord)
        expected_head = _content_id(schema, retained.canonical_record_bytes)
        _expected_reference(retained, record.previous_lineage.root_id, expected_head)
        return DecodedRetainedSuccessorInput(
            retained, record, lineage=_project_successor_lineage(record, retained.reference)
        )
    if expected_role == "current_selector" and schema == PHYSICAL_SELECTOR_SCHEMA:
        selector = _decode_native(retained, _GenesisPhysicalSelector, _scheduler_native_bytes)
        assert isinstance(selector, _GenesisPhysicalSelector)
        expected = _native_reference(
            "physical-root-selector-head-v1", selector.model_dump(mode="json")
        )
        _expected_reference(retained, selector.selector_id, expected.head)
        return DecodedRetainedSuccessorInput(
            retained,
            selector,
            physical_root=PhysicalRootBinding(
                selector_id=selector.selector_id,
                selector_head=expected.head,
                selector_version=selector.selector_version,
                current_epoch_id=selector.current_epoch_id,
                current_epoch_head=selector.current_epoch_head,
            ),
            epoch_root_id=selector.lineage_id,
        )
    if expected_role == "current_selector" and schema == ROLLOVER_PHYSICAL_SELECTOR_SCHEMA:
        selector = _decode_native(retained, _RolloverPhysicalSelector, _rollover_native_bytes)
        assert isinstance(selector, _RolloverPhysicalSelector)
        _expected_reference(retained, selector.selector_id, selector.decision.head + "/selector")
        return DecodedRetainedSuccessorInput(
            retained,
            selector,
            lineage=selector.lineage,
            physical_root=PhysicalRootBinding(
                selector_id=selector.selector_id,
                selector_head=retained.reference.revision.head,
                selector_version=selector.selector_version,
                current_epoch_id=selector.selected_epoch.head,
                current_epoch_head=selector.selected_epoch.head,
            ),
            selected_epoch=selector.selected_epoch,
        )
    if expected_role == "selected_epoch" and schema == PHYSICAL_ROOT_SCHEMA:
        epoch = _decode_native(retained, _GenesisPhysicalRoot, _scheduler_native_bytes)
        assert isinstance(epoch, _GenesisPhysicalRoot)
        expected = _native_reference("physical-root-head-v1", epoch.model_dump(mode="json"))
        _expected_reference(retained, epoch.epoch_id, expected.head)
        return DecodedRetainedSuccessorInput(
            retained,
            epoch,
            epoch_root_id=epoch.execution_lineage_root_id,
            epoch_root_fingerprint=epoch.execution_lineage_root_fingerprint,
            observed_lineage_head=epoch.observed_lineage_head,
            observed_current_run_id=epoch.observed_current_run_id,
        )
    if expected_role == "selected_epoch" and schema == ROLLOVER_PHYSICAL_ROOT_SCHEMA:
        epoch = _decode_native(retained, _RolloverPhysicalRoot, _rollover_native_bytes)
        assert isinstance(epoch, _RolloverPhysicalRoot)
        _expected_reference(retained, epoch.epoch_id, epoch.epoch_id)
        return DecodedRetainedSuccessorInput(
            retained,
            epoch,
            lineage=epoch.lineage,
            epoch_root_id=epoch.lineage.root_id,
            epoch_root_fingerprint=epoch.lineage.root_fingerprint,
            observed_lineage_head=epoch.lineage.lineage_head,
            observed_current_run_id=epoch.lineage.current_run_id,
        )
    raise RecoveryRecordIntegrityError("unsupported retained scheduler source schema for role")


def _pending_head(pending: PendingCallFrontier) -> CallSubjectHead:
    return CallSubjectHead(subject_id=pending.original_call_id, revision=pending.pending)


def _decoded(
    values: tuple[RecoveryRecordMember, ...], expected_type: type[RecoveryDTO], reason: str
) -> DecodedSuccessorRecordMember:
    if len(values) != 1:
        raise RecoveryRecordIntegrityError(reason)
    decoded = decode_successor_record_member(values[0])
    if not isinstance(decoded.record, expected_type):
        raise RecoveryRecordIntegrityError(reason)
    return decoded


def validate_execution_successor_records(
    request: PrepareExecutionSuccessor,
    result: PreparedExecutionSuccessor,
    edge_member: RecoveryRecordMember,
    selected_baseline_member: RecoveryRecordMember,
    selected_pair_member: RecoveryRecordMember,
    initialization_members: tuple[RecoveryRecordMember, ...],
    scheduler_members: tuple[RecoveryRecordMember, ...],
    scheduler_inputs: SchedulerSuccessorInputs | None,
) -> None:
    """Validate the acyclic Runs → records → edge successor publication order."""
    try:
        request = PrepareExecutionSuccessor.model_validate_json(request.canonical_bytes())
        result = PreparedExecutionSuccessor.model_validate_json(result.canonical_bytes())
    except ValidationError as error:
        raise RecoveryRecordIntegrityError("invalid successor request or result") from error
    edge = decode_successor_edge(edge_member)
    if not isinstance(edge.record, ExecutionSuccessorEdge):
        raise RecoveryRecordIntegrityError("successor edge row mismatch")
    _require(result.edge, edge.record, "result successor edge differs")
    _require(
        result.source_request_fingerprint,
        hashlib.sha256(request.canonical_bytes()).hexdigest(),
        "result source request fingerprint differs",
    )
    baseline = decode_recovery_record_member(selected_baseline_member)
    if not isinstance(baseline.record, ExecutionSuspensionBaseline):
        raise RecoveryRecordIntegrityError("selected suspension baseline row mismatch")
    pair = decode_recovery_record_member(selected_pair_member)
    if not isinstance(pair.record, ExecutionSuspensionPair):
        raise RecoveryRecordIntegrityError("selected suspension pair row mismatch")
    _require(
        baseline.record,
        request.original_suspension.baseline,
        "request selected baseline differs",
    )
    baseline_ref = CallSubjectHead(
        subject_id=baseline.record.baseline_id, revision=_reference(baseline.member)
    )
    _require(pair.record.baseline, baseline_ref, "pair baseline physical ref differs")
    initialization = _decoded(
        initialization_members,
        ExecutionSuccessorInitializationRecord,
        "successor requires exactly one initialization record",
    )
    init = initialization.record
    assert isinstance(init, ExecutionSuccessorInitializationRecord)

    _require(
        request.original_suspension.canonical_pair_bytes,
        pair.member.canonical_record_bytes,
        "request selected pair bytes differ",
    )
    _require(request.original_suspension.pair, pair.record, "request suspension pair differs")
    _require(
        pair.record.source_cut_fingerprint,
        request.original_suspension.baseline.source_cut_fingerprint,
        "pair historical source cut differs",
    )
    _require(
        pair.record.predecessor_run,
        request.original_suspension.baseline.predecessor_run,
        "pair historical predecessor differs",
    )
    _require(
        pair.record.suspension_command_id,
        request.original_suspension.baseline.suspension_command_id,
        "pair historical command differs",
    )
    _require(
        request.run.suspension_baseline,
        pair.record.baseline,
        "suspended Run baseline differs",
    )
    _require(
        request.original_suspension.baseline.run_id,
        request.run.run_id,
        "baseline Run differs",
    )
    _require(
        request.run.predecessor,
        pair.record.predecessor_run.revision.head,
        "suspended Run historical predecessor differs",
    )
    _require(request.cut.current_run, _run_head(request.run), "current cut Run differs")
    _require(request.cut.tenant_id, request.run.tenant, "current cut tenant differs")
    _require(
        request.cut.complete_ordered_run_lineage[-1], request.run, "current cut Run tail differs"
    )
    _require(request.cut.frontier.tenant_id, request.cut.tenant_id, "frontier tenant differs")
    _require(request.cut.frontier.run_id, request.run.run_id, "frontier Run differs")
    _require(
        request.cut.frontier.tenant_commit_sequence,
        request.cut.tenant_commit_sequence,
        "frontier sequence differs",
    )
    if isinstance(request.fence, SchedulerExecutionFence):
        _require(request.fence.run_head, request.run.head, "scheduler fence Run head differs")
        _require(
            request.fence.lineage.current_run_id,
            request.run.run_id,
            "scheduler fence lineage Run differs",
        )
    else:
        _require(request.fence.run_id, request.run.run_id, "non-scheduler fence Run differs")
        _require(request.fence.run_head, request.run.head, "non-scheduler fence Run head differs")
    _require(pair.record.suspended_run, _run_head(request.run), "pair suspended Run differs")
    _require(
        edge.record.original_pair,
        request.original_suspension.selected_pair,
        "edge original pair differs",
    )
    _require(
        _head(edge.record.original_pair),
        _reference(pair.member),
        "edge original pair physical ref differs",
    )

    predecessor = _run_head(request.run)
    successor = _run_head(result.successor_run)
    _require(request.run.state, "SUSPENDED", "request Run is not suspended")
    _require(result.superseded_run.state, "SUPERSEDED", "result predecessor is not superseded")
    _require(result.successor_run.state, "CREATED", "result successor is not created")
    _require(result.superseded_run.run_id, request.run.run_id, "superseded Run ID differs")
    _require(
        result.superseded_run.predecessor,
        request.run.head,
        "superseded Run predecessor differs",
    )
    _require(
        result.successor_run.predecessor,
        request.run.head,
        "successor Run predecessor differs",
    )
    _require(init.command_id, request.command_id, "initialization command differs")
    _require(init.predecessor_run, predecessor, "initialization predecessor differs")
    _require(init.successor_run, successor, "initialization successor differs")
    _require(init.source_cut_fingerprint, request.cut.digest(), "initialization source cut differs")
    _require(
        init.disposition_version,
        request.disposition_version,
        "initialization disposition differs",
    )
    _require(init.current_bindings, request.cut.current_bindings, "initialization bindings differ")
    _require(init.execution_fence, request.fence, "initialization fence differs")
    _require(
        init.original_obligations,
        edge.record.original_obligations,
        "initialization obligations differ",
    )
    _require(
        init.inherited_no_retry_boundaries,
        edge.record.inherited_no_retry_boundaries,
        "initialization no-retry boundaries differ",
    )
    _require(
        tuple(_pending_head(value) for value in init.inherited_pending_branches),
        edge.record.inherited_pending_branches,
        "initialization pending branch heads differ",
    )
    _require(
        init.observation_frontier,
        edge.record.observation_frontier,
        "initialization frontier differs",
    )
    _require(edge.record.command_id, request.command_id, "edge command differs")
    _require(edge.record.predecessor_before, predecessor, "edge predecessor differs")
    _require(
        edge.record.predecessor_superseded,
        _run_head(result.superseded_run),
        "edge superseded run differs",
    )
    _require(edge.record.successor_created, successor, "edge successor differs")
    _require(edge.record.source_cut_fingerprint, request.cut.digest(), "edge source cut differs")
    _require(
        edge.record.disposition_version,
        request.disposition_version,
        "edge disposition differs",
    )
    _require(edge.record.fence, request.fence, "edge fence differs")
    initialization_ref = CallSubjectHead(
        subject_id=result.successor_run.run_id,
        revision=_reference(initialization.member),
    )
    _require(
        edge.record.complete_initialization,
        (initialization_ref,),
        "edge initialization refs differ",
    )
    _require(
        result.complete_initialization_bytes,
        (initialization.member.canonical_record_bytes,),
        "result initialization bytes differ",
    )

    if isinstance(result.lineage_advance, NonSchedulerSuccessor):
        if scheduler_members or scheduler_inputs is not None:
            raise RecoveryRecordIntegrityError(
                "non-scheduler successor carries scheduler companions"
            )
        return
    if not isinstance(result.lineage_advance, SchedulerSuccessorAdvance):
        raise RecoveryRecordIntegrityError("unknown successor lineage branch")
    if scheduler_inputs is None or len(scheduler_members) != 2:
        raise RecoveryRecordIntegrityError(
            "scheduler successor requires exactly two companions and inputs"
        )
    lineage = decode_successor_record_member(scheduler_members[0])
    epoch = decode_successor_record_member(scheduler_members[1])
    if not isinstance(lineage.record, SchedulerSuccessorLineageAdvanceRecord) or not isinstance(
        epoch.record, SchedulerSuccessorEpochObservationRecord
    ):
        raise RecoveryRecordIntegrityError("scheduler companion order differs")
    lineage_record = lineage.record
    epoch_record = epoch.record
    advance = result.lineage_advance
    _require(advance.lineage_schema, lineage.member.schema_id, "lineage schema differs")
    _require(
        advance.canonical_lineage_bytes,
        lineage.member.canonical_record_bytes,
        "lineage bytes differ",
    )
    _require(advance.epoch_observation_schema, epoch.member.schema_id, "epoch schema differs")
    _require(
        advance.canonical_epoch_observation_bytes,
        epoch.member.canonical_record_bytes,
        "epoch bytes differ",
    )
    _require(
        scheduler_inputs.original_lineage.reference,
        advance.original_lineage,
        "original lineage input differs",
    )
    _require(
        scheduler_inputs.current_selector.reference,
        advance.current_selector,
        "selector input differs",
    )
    _require(
        scheduler_inputs.selected_epoch.reference,
        advance.selected_epoch,
        "epoch input differs",
    )
    if not isinstance(request.fence, SchedulerExecutionFence):
        raise RecoveryRecordIntegrityError("scheduler successor lacks scheduler fence")
    fence = request.fence
    original_source = decode_retained_successor_input(
        scheduler_inputs.original_lineage, "original_lineage"
    )
    selector_source = decode_retained_successor_input(
        scheduler_inputs.current_selector, "current_selector"
    )
    epoch_source = decode_retained_successor_input(
        scheduler_inputs.selected_epoch, "selected_epoch"
    )
    assert original_source.lineage is not None
    assert selector_source.physical_root is not None
    selector_lineage_id = selector_source.epoch_root_id
    if selector_lineage_id is None:
        assert selector_source.lineage is not None
        selector_lineage_id = selector_source.lineage.root_id
    _require(
        selector_source.physical_root,
        fence.physical_root,
        "selected physical root differs from fence",
    )
    _require(
        selector_lineage_id,
        fence.lineage.root_id,
        "selected selector lineage differs from fence",
    )
    _require(epoch_source.epoch_root_id, fence.lineage.root_id, "selected epoch root differs")
    _require(
        epoch_source.epoch_root_fingerprint,
        fence.lineage.root_fingerprint,
        "selected epoch root fingerprint differs",
    )
    _require(
        selector_source.physical_root.current_epoch_id,
        epoch_source.retained.reference.subject_id,
        "selected selector epoch ID differs",
    )
    _require(
        selector_source.physical_root.current_epoch_head,
        epoch_source.retained.reference.revision.head,
        "selected selector epoch head differs",
    )
    if selector_source.selected_epoch is not None:
        _require(
            selector_source.selected_epoch,
            epoch_source.retained.reference.revision,
            "rollover selector epoch reference differs",
        )
    if isinstance(scheduler_inputs.continuity, EpochCreationLineageWitness):
        if selector_source.lineage is not None:
            _require(selector_source.lineage, fence.lineage, "rollover selector lineage differs")
        if epoch_source.lineage is not None:
            _require(epoch_source.lineage, fence.lineage, "rollover epoch lineage differs")
        _require(
            epoch_source.observed_lineage_head,
            fence.lineage.lineage_head,
            "selected epoch lineage head differs",
        )
        _require(
            epoch_source.observed_current_run_id,
            fence.lineage.current_run_id,
            "selected epoch current run differs",
        )
    elif isinstance(scheduler_inputs.continuity, PriorSuccessorObservationLineageWitness):
        prior = decode_successor_record_member(scheduler_inputs.continuity.observation)
        if not isinstance(prior.record, SchedulerSuccessorEpochObservationRecord):
            raise RecoveryRecordIntegrityError(
                "prior continuity witness is not an epoch observation"
            )
        if not isinstance(original_source.record, SchedulerSuccessorLineageAdvanceRecord):
            raise RecoveryRecordIntegrityError(
                "prior continuity requires a retained lineage advance"
            )
        prior_observation = prior.record
        prior_advance = original_source.record
        _require(
            prior_observation.advanced_lineage,
            original_source.retained.reference,
            "prior observation advanced lineage differs",
        )
        _require(
            prior_observation.predecessor_run,
            prior_advance.predecessor_run,
            "prior observation predecessor differs",
        )
        _require(
            prior_observation.successor_run,
            prior_advance.successor_run,
            "prior observation successor differs",
        )
        _require(
            prior_observation.successor_run.subject_id,
            request.run.run_id,
            "prior observation successor run differs",
        )
        _require(
            prior_advance.next_current_run_id,
            request.run.run_id,
            "prior advance current run differs",
        )
        ancestry_request = scheduler_inputs.continuity.ancestry_request
        _require(ancestry_request.command_id, request.command_id, "ancestry command differs")
        _require(ancestry_request.cut.tenant_id, request.cut.tenant_id, "ancestry tenant differs")
        _require(
            ancestry_request.cut.database_id,
            request.cut.database_id,
            "ancestry database differs",
        )
        _require(
            ancestry_request.cut.tenant_commit_sequence,
            request.cut.tenant_commit_sequence,
            "ancestry frontier differs",
        )
        _require(
            ancestry_request.cut.materialization_commitment,
            request.cut.materialization_commitment,
            "ancestry commitment differs",
        )
        _require(
            ancestry_request.prior_epoch_observation,
            CallSubjectHead(
                subject_id=prior.member.record_id,
                revision=_reference(prior.member),
            ),
            "ancestry prior observation differs",
        )
        _require(
            ancestry_request.prior_lineage_advance,
            original_source.retained.reference,
            "ancestry prior advance differs",
        )
        _require(
            ancestry_request.start_run, prior_observation.successor_run, "ancestry start differs"
        )
        _require(ancestry_request.end_run, _run_head(request.run), "ancestry end differs")
        try:
            ancestry = validate_successor_run_ancestry_exchange(
                ancestry_request, scheduler_inputs.continuity.ancestry
            )
        except SuccessorAncestryIntegrityError as error:
            raise RecoveryRecordIntegrityError("invalid retained successor ancestry") from error
        _require(ancestry[-1].run, request.run, "ancestry final Run differs")
        _require(
            _run_head(ancestry[-1].run),
            pair.record.suspended_run,
            "ancestry final suspension Run differs",
        )
        _require(
            prior_observation.physical_root,
            fence.physical_root,
            "prior observation physical root differs",
        )
        _require(
            prior_observation.previous_lineage.revision.head,
            prior_advance.previous_lineage.lineage_head,
            "prior observation previous lineage head differs",
        )
        _require(
            prior_observation.previous_lineage.subject_id,
            prior_advance.previous_lineage.root_id,
            "prior observation previous lineage subject differs",
        )
        _require(
            prior_observation.command_id,
            prior_advance.command_id,
            "prior observation command differs",
        )
        _require(
            prior_observation.source_cut_fingerprint,
            prior_advance.source_cut_fingerprint,
            "prior observation source cut differs",
        )
        _require(prior_observation.lease, prior_advance.lease, "prior observation lease differs")
    else:
        raise RecoveryRecordIntegrityError("unknown scheduler lineage continuity witness")
    _require(original_source.lineage, fence.lineage, "selected lineage differs from fence")
    _require(lineage_record.command_id, request.command_id, "lineage command differs")
    _require(lineage_record.predecessor_run, predecessor, "lineage predecessor differs")
    _require(lineage_record.successor_run, successor, "lineage successor differs")
    _require(
        lineage_record.previous_lineage,
        original_source.lineage,
        "lineage predecessor source differs",
    )
    _require(
        advance.original_lineage.subject_id,
        lineage_record.previous_lineage.root_id,
        "lineage predecessor subject differs",
    )
    _require(
        lineage_record.previous_lineage.current_run_id,
        request.run.run_id,
        "lineage current run differs",
    )
    _require(
        lineage_record.next_current_run_id,
        result.successor_run.run_id,
        "lineage next run differs",
    )
    projected_lineage = _project_successor_lineage(
        lineage_record,
        CallSubjectHead(
            subject_id=lineage_record.previous_lineage.root_id,
            revision=_reference(lineage.member),
        ),
    )
    _require(
        projected_lineage.current_run_id,
        result.successor_run.run_id,
        "projected lineage current run differs",
    )
    _require(
        projected_lineage.lineage_head,
        lineage.member.record_id,
        "projected lineage head differs",
    )
    _require(lineage_record.physical_root, fence.physical_root, "lineage root differs")
    _require(lineage_record.lease, fence.lease, "lineage lease differs")
    _require(
        lineage_record.source_cut_fingerprint,
        request.cut.digest(),
        "lineage source cut differs",
    )
    _require(epoch_record.command_id, request.command_id, "epoch command differs")
    _require(epoch_record.predecessor_run, predecessor, "epoch predecessor differs")
    _require(epoch_record.successor_run, successor, "epoch successor differs")
    _require(epoch_record.physical_root, fence.physical_root, "epoch root differs")
    _require(epoch_record.lease, fence.lease, "epoch lease differs")
    _require(
        epoch_record.previous_lineage,
        advance.original_lineage,
        "epoch previous lineage differs",
    )
    _require(
        _head(epoch_record.advanced_lineage),
        _reference(lineage.member),
        "epoch advanced lineage output differs",
    )
    _require(
        epoch_record.advanced_lineage.subject_id,
        lineage_record.previous_lineage.root_id,
        "epoch advanced lineage subject differs",
    )
    _require(
        epoch_record.source_cut_fingerprint,
        request.cut.digest(),
        "epoch source cut differs",
    )
    _require(
        advance.current_selector.subject_id,
        fence.physical_root.selector_id,
        "selector root differs",
    )
    _require(
        advance.current_selector.revision.head,
        fence.physical_root.selector_head,
        "selector head differs",
    )
    _require(
        advance.selected_epoch.subject_id,
        fence.physical_root.current_epoch_id,
        "epoch root differs",
    )
    _require(
        advance.selected_epoch.revision.head,
        fence.physical_root.current_epoch_head,
        "epoch head differs",
    )
