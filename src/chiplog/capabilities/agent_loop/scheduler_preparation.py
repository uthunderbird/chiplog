"""Serialized owner preparation. All snapshots are reproduced by the broker.

These functions perform no I/O, authenticate no caller, and publish no records.
The broker must rerun preparation against its transaction-local exact read cut.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, TypeAdapter

from chiplog.capabilities.agent_loop.recovery_contracts import (
    Absent,
    Digest,
    Identity,
    PreRootDecisionFence,
    Present,
    RecoveryDTO,
    UInt64,
)
from chiplog.capabilities.agent_loop.scheduler_contracts import (
    DecideIntervalCommand,
    DueCoordinate,
    LeaseTransitionCommand,
    MissedOccurrencePolicyHead,
    PhysicalRootRolloverCommand,
    ReplaceIntervalBoundCommand,
    ResolveIntervalCommand,
    ScheduleDefinitionHead,
    SchedulerCommandIdentity,
    SchedulerContextRef,
    SchedulerEligibilityManifest,
    SchedulerIntervalBound,
    SchedulerLineageView,
    SchedulerOverflowHold,
    StreamingEligibilityEvidence,
)
from chiplog.capabilities.agent_loop.scheduler_leases import (
    IssuedLeaseObservation,
    LeaseCandidate,
    LeaseRuleViolation,
    evaluate_lease,
)

from .scheduler_configuration import (
    ConfigurationProposal,
    ConfigurationSnapshot,
    DisposedOccurrence,
    FixedIntervalDefinition,
    amend,
    genesis,
    policy_head,
    replace_bound,
    schedule_head,
    stream_definition,
)
from .scheduler_domain import SchedulerDomainError
from .scheduler_materialization import (
    IntervalCandidate,
    SchedulerCanonicalMember,
    prepare_interval,
    prepare_resolution,
    prepare_streamed_overflow,
)
from .scheduler_rollover import (
    IssuedRolloverObservation,
    RolloverCandidate,
    prepare_rollover,
)

PREPARATION_SCHEMA = "chiplog.scheduler.lease-preparation.v1"
PREPARED_SCHEMA = "chiplog.scheduler.prepared-lease.v1"
RECORD_SCHEMA: Literal["chiplog.scheduler.lease-transition.v1"] = (
    "chiplog.scheduler.lease-transition.v1"
)


class PreparationDTO(RecoveryDTO):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        ser_json_bytes="base64",
        val_json_bytes="base64",
    )


class LeasePreparationSnapshot(PreparationDTO):
    tenant_id: Identity
    tenant_frontier: UInt64
    materialization_commitment: Digest
    registry_head: Identity
    registry_fingerprint: Digest
    current: SchedulerLineageView
    issued: IssuedLeaseObservation | None
    submission_id: Identity


class LeasePreparationRequest(PreparationDTO):
    operation: Literal["scheduler.acquire", "scheduler.renew", "scheduler.takeover"]
    context: SchedulerContextRef
    snapshot: LeasePreparationSnapshot
    command_bytes: bytes = Field(min_length=1)


class SchedulerLeaseTransitionRecord(PreparationDTO):
    """Owner-authored proposed authoritative record, awaiting journal selection."""

    record_kind: Literal["scheduler.lease_transition"] = "scheduler.lease_transition"
    schema_id: Literal["chiplog.scheduler.lease-transition.v1"] = RECORD_SCHEMA
    tenant_id: Identity
    context: SchedulerContextRef
    expected_tenant_frontier: UInt64
    expected_materialization_commitment: Digest
    expected_registry_head: Identity
    expected_registry_fingerprint: Digest
    previous: SchedulerLineageView
    command: LeaseTransitionCommand
    issued: IssuedLeaseObservation
    candidate: LeaseCandidate


class PreparedSchedulerRecord(PreparationDTO):
    owner: Literal["agent_loop"] = "agent_loop"
    record_kind: Literal["scheduler.lease_transition"] = "scheduler.lease_transition"
    schema_id: Literal["chiplog.scheduler.lease-transition.v1"] = RECORD_SCHEMA
    record_id: Identity
    canonical_record_bytes: bytes = Field(min_length=1)
    fingerprint: Digest


def prepare_lease(request: LeasePreparationRequest) -> PreparedSchedulerRecord:
    command = LeaseTransitionCommand.model_validate_json(request.command_bytes)
    if command.canonical_bytes() != request.command_bytes:
        raise LeaseRuleViolation("INTEGRITY_HOLD", "noncanonical scheduler lease command")
    if request.operation != "scheduler." + command.kind.lower():
        raise LeaseRuleViolation("INTEGRITY_HOLD", "operation and closed lease command differ")
    snapshot = request.snapshot
    if request.context.tenant_id != snapshot.tenant_id:
        raise LeaseRuleViolation("AUTHORITY_DENIED", "context and observed tenant differ")
    if (
        request.context.service_identity != command.proposed_holder_id
        or request.context.session_id != command.proposed_holder_session_id
    ):
        raise LeaseRuleViolation(
            "AUTHORITY_DENIED",
            "context does not bind proposed holder/session",
        )
    candidate = evaluate_lease(
        snapshot.current,
        command,
        snapshot.issued,
        submission_id=snapshot.submission_id,
    )
    # evaluate_lease rejects absent proof before producing a candidate.
    if snapshot.issued is None:
        raise LeaseRuleViolation("CLOCK_UNVERIFIABLE", "issued clock observation absent")
    record = SchedulerLeaseTransitionRecord(
        tenant_id=snapshot.tenant_id,
        context=request.context,
        expected_tenant_frontier=snapshot.tenant_frontier,
        expected_materialization_commitment=snapshot.materialization_commitment,
        expected_registry_head=snapshot.registry_head,
        expected_registry_fingerprint=snapshot.registry_fingerprint,
        previous=snapshot.current,
        command=command,
        issued=snapshot.issued,
        candidate=candidate,
    )
    payload = record.canonical_bytes()
    return PreparedSchedulerRecord(
        record_id=candidate.transition_id,
        canonical_record_bytes=payload,
        fingerprint=hashlib.sha256(payload).hexdigest(),
    )


BATCH_SCHEMA = "chiplog.scheduler.prepared-batch.v1"


class BatchPreparationCut(PreparationDTO):
    """Reproduced exclusively by broker authority at the writer's exact source cut."""

    tenant_id: Identity
    tenant_frontier: UInt64
    materialization_commitment: Digest
    registry_head: Identity
    registry_fingerprint: Digest
    submission_id: Identity
    authorized_context: SchedulerContextRef
    authorized_command_fingerprint: Digest
    authority_proof: Present


class ConfigurationGenesisCommand(PreparationDTO):
    kind: Literal["GENESIS"] = "GENESIS"
    identity: SchedulerCommandIdentity
    schedule_id: Identity
    definition: FixedIntervalDefinition
    policy: Literal["SKIP", "COALESCE", "MATERIALIZE_EACH"]
    bound: SchedulerIntervalBound


class ConfigurationScheduleCommand(PreparationDTO):
    kind: Literal["AMEND_SCHEDULE"] = "AMEND_SCHEDULE"
    identity: SchedulerCommandIdentity
    observed_schedule: ScheduleDefinitionHead
    observed_policy: MissedOccurrencePolicyHead
    definition: FixedIntervalDefinition


class ConfigurationPolicyCommand(PreparationDTO):
    kind: Literal["AMEND_POLICY"] = "AMEND_POLICY"
    identity: SchedulerCommandIdentity
    observed_schedule: ScheduleDefinitionHead
    observed_policy: MissedOccurrencePolicyHead
    policy: Literal["SKIP", "COALESCE", "MATERIALIZE_EACH"]


class ConfigurationBoundCommand(PreparationDTO):
    kind: Literal["REPLACE_BOUND"] = "REPLACE_BOUND"
    command: ReplaceIntervalBoundCommand


ConfigurationCommand = Annotated[
    ConfigurationGenesisCommand
    | ConfigurationScheduleCommand
    | ConfigurationPolicyCommand
    | ConfigurationBoundCommand,
    Field(discriminator="kind"),
]


class ConfigurationPreparationSnapshot(PreparationDTO):
    cut: BatchPreparationCut
    current: ConfigurationSnapshot
    authority_epoch: Identity
    broker_generation: Identity
    runtime_graph_generation: Identity
    scheduler_authority_head: Identity


class ConfigurationPreparationRequest(PreparationDTO):
    operation: Literal[
        "scheduler.genesis",
        "scheduler.amend_schedule",
        "scheduler.amend_policy",
        "scheduler.replace_bound",
    ]
    context: SchedulerContextRef
    snapshot: ConfigurationPreparationSnapshot
    command_bytes: bytes = Field(min_length=1)


class IntervalPreparationSnapshot(PreparationDTO):
    cut: BatchPreparationCut
    configuration: ConfigurationSnapshot
    disposed: tuple[DisposedOccurrence, ...]
    previous_due_boundary: DueCoordinate
    predecessor_interval: Absent | Present
    enumeration_frontier: Identity
    enumeration_proof: Present
    active_hold: SchedulerOverflowHold | None
    publication_fence: PreRootDecisionFence
    operator_proof: Present | None


class IntervalPreparationRequest(PreparationDTO):
    operation: Literal["scheduler.decide_interval", "scheduler.resolve_interval"]
    context: SchedulerContextRef
    snapshot: IntervalPreparationSnapshot
    command_bytes: bytes = Field(min_length=1)


class RolloverPreparationSnapshot(PreparationDTO):
    cut: BatchPreparationCut
    current: SchedulerLineageView
    issued: IssuedRolloverObservation | None


class RolloverPreparationRequest(PreparationDTO):
    operation: Literal["scheduler.rollover"]
    context: SchedulerContextRef
    snapshot: RolloverPreparationSnapshot
    command_bytes: bytes = Field(min_length=1)


class PreparedSchedulerBatch(PreparationDTO):
    kind: Literal["PREPARED"] = "PREPARED"
    owner: Literal["agent_loop"] = "agent_loop"
    source_request_fingerprint: Digest
    cut: BatchPreparationCut
    records: tuple[SchedulerCanonicalMember, ...]
    result: ConfigurationProposal | IntervalCandidate | RolloverCandidate


def _verify_cut(
    context: SchedulerContextRef, command_bytes: bytes, cut: BatchPreparationCut
) -> None:
    if (
        context != cut.authorized_context
        or context.tenant_id != cut.tenant_id
        or hashlib.sha256(command_bytes).hexdigest() != cut.authorized_command_fingerprint
    ):
        raise SchedulerDomainError("command or context differs from exact broker-authorized cut")


def _batch(
    request: ConfigurationPreparationRequest
    | IntervalPreparationRequest
    | RolloverPreparationRequest,
    result: ConfigurationProposal | IntervalCandidate | RolloverCandidate,
    records: tuple[SchedulerCanonicalMember, ...],
) -> PreparedSchedulerBatch:
    return PreparedSchedulerBatch(
        source_request_fingerprint=hashlib.sha256(request.canonical_bytes()).hexdigest(),
        cut=request.snapshot.cut,
        result=result,
        records=records,
    )


def _configuration_member(
    kind: str, identity: str, record: RecoveryDTO
) -> SchedulerCanonicalMember:
    payload = record.canonical_bytes()
    return SchedulerCanonicalMember(
        record_kind="scheduler." + kind,
        record_id=identity,
        schema_id="chiplog.scheduler." + kind + ".v1",
        canonical_base64=base64.b64encode(payload).decode(),
        fingerprint=hashlib.sha256(payload).hexdigest(),
    )


def prepare_configuration(request: ConfigurationPreparationRequest) -> PreparedSchedulerBatch:
    snapshot = request.snapshot
    _verify_cut(request.context, request.command_bytes, snapshot.cut)
    command: ConfigurationCommand = TypeAdapter(ConfigurationCommand).validate_json(
        request.command_bytes
    )
    if command.canonical_bytes() != request.command_bytes:
        raise SchedulerDomainError("noncanonical configuration command")
    if request.operation != "scheduler." + command.kind.lower():
        raise SchedulerDomainError("configuration operation differs from command")
    if command.kind == "GENESIS":
        result = genesis(
            snapshot.current,
            schedule_id=command.schedule_id,
            definition=command.definition,
            policy=command.policy,
            bound=command.bound,
            context=request.context,
            command_id=command.identity.command_id,
            authority_epoch=snapshot.authority_epoch,
            broker_generation=snapshot.broker_generation,
            runtime_graph_generation=snapshot.runtime_graph_generation,
        )
    elif command.kind == "REPLACE_BOUND":
        inner = command.command
        if (
            inner.scheduler_authority_head != snapshot.scheduler_authority_head
            or inner.authority_epoch != snapshot.authority_epoch
            or inner.broker_generation != snapshot.broker_generation
            or inner.runtime_graph_generation != snapshot.runtime_graph_generation
        ):
            raise SchedulerDomainError("bound authority generations changed")
        result = replace_bound(
            snapshot.current,
            observed=inner.observed_head,
            proposed_bound=inner.proposed_bound,
            context=request.context,
            command_id=inner.identity.command_id,
            authority_epoch=snapshot.authority_epoch,
            broker_generation=snapshot.broker_generation,
            runtime_graph_generation=snapshot.runtime_graph_generation,
        )
    else:
        result = amend(
            snapshot.current,
            observed_schedule=command.observed_schedule,
            observed_policy=command.observed_policy,
            context=request.context,
            command_id=command.identity.command_id,
            definition=command.definition if command.kind == "AMEND_SCHEDULE" else None,
            policy=command.policy if command.kind == "AMEND_POLICY" else None,
        )
    records: list[SchedulerCanonicalMember] = []
    current, proposed = result.expected, result.proposed
    if proposed.schedule is not None and current.schedule != proposed.schedule:
        records.append(
            _configuration_member(
                "schedule-definition", schedule_head(proposed.schedule).head, proposed.schedule
            )
        )
    if proposed.policy is not None and current.policy != proposed.policy:
        records.append(
            _configuration_member(
                "missed-policy", policy_head(proposed.policy).head, proposed.policy
            )
        )
    if proposed.bound is not None and current.bound != proposed.bound:
        records.append(
            _configuration_member("interval-bound", proposed.bound.head_id, proposed.bound)
        )
    if len(records) != (3 if command.kind == "GENESIS" else 1):
        raise SchedulerDomainError("configuration output is not one complete closed operation")
    return _batch(request, result, tuple(records))


def prepare_interval_request(
    request: IntervalPreparationRequest,
) -> PreparedSchedulerBatch:
    snapshot = request.snapshot
    _verify_cut(request.context, request.command_bytes, snapshot.cut)
    command = (
        DecideIntervalCommand.model_validate_json(request.command_bytes)
        if request.operation == "scheduler.decide_interval"
        else ResolveIntervalCommand.model_validate_json(request.command_bytes)
    )
    if command.canonical_bytes() != request.command_bytes:
        raise SchedulerDomainError("noncanonical interval command")
    boundary = command.boundary
    if (
        command.publication_fence.command_id != command.identity.command_id
        or command.publication_fence.disposition.kind != "FIRST_PUBLICATION"
    ):
        raise SchedulerDomainError(
            "fresh preparation requires exact first-publication command; "
            "journal replay owns decided bytes"
        )
    if (
        boundary.previous_due_boundary != snapshot.previous_due_boundary
        or boundary.predecessor_interval != snapshot.predecessor_interval
        or boundary.enumeration_frontier != snapshot.enumeration_frontier
        or command.publication_fence != snapshot.publication_fence
    ):
        raise SchedulerDomainError("interval boundary or publication fence changed")
    configuration = snapshot.configuration
    if configuration.schedule is None or configuration.bound is None:
        raise SchedulerDomainError("interval configuration incomplete")
    if configuration.schedule.definition.run_inputs.tenant != snapshot.cut.tenant_id:
        raise SchedulerDomainError("interval definition tenant differs from source cut")
    hold = snapshot.active_hold
    expected_hold = hold.hold if hold is not None and hold.state.kind == "ACTIVE" else Absent()
    if configuration.active_hold != expected_hold:
        raise SchedulerDomainError("configuration and current hold inventory differ")
    if isinstance(command, DecideIntervalCommand):
        if hold is not None and hold.state.kind == "ACTIVE":
            raise SchedulerDomainError("ordinary interval cannot bypass active hold")
        submitted = command.manifest
    else:
        if (
            hold is None
            or command.active_hold != hold
            or snapshot.operator_proof is None
            or command.operator_proof != snapshot.operator_proof
        ):
            raise SchedulerDomainError("resolution active hold or operator proof differs")
        submitted = command.complete_manifest
    inputs = configuration.schedule.definition.run_inputs
    observed_bound = (
        command.bound_head
        if isinstance(command, DecideIntervalCommand)
        else command.successor_bound
    )
    if observed_bound != configuration.bound:
        raise SchedulerDomainError("interval current bound changed")
    measured = stream_definition(
        configuration, boundary, snapshot.disposed, snapshot.enumeration_proof
    )
    if measured.evidence.kind == "FULL_MANIFEST":
        expected: SchedulerEligibilityManifest | StreamingEligibilityEvidence = (
            measured.evidence.manifest
        )
    else:
        expected = measured.evidence
    if submitted != expected:
        raise SchedulerDomainError(
            "submitted membership differs from complete definition enumeration"
        )
    overflow = (
        measured.member_count > configuration.bound.bound.max_member_count
        or measured.manifest_bytes > configuration.bound.bound.max_manifest_bytes
    )
    if overflow:
        if not isinstance(command, DecideIntervalCommand):
            raise SchedulerDomainError("successor bound does not admit complete resolution")
        candidate = prepare_streamed_overflow(
            command,
            configuration.bound,
            measured.evidence,
            measured.member_count,
            measured.manifest_bytes,
            inputs.principal,
        )
        return _batch(request, candidate, candidate.records)
    if measured.evidence.kind != "FULL_MANIFEST":
        raise SchedulerDomainError("ordinary admission requires full canonical manifest")
    enumerated = measured.evidence.manifest
    if isinstance(command, DecideIntervalCommand):
        candidate = prepare_interval(
            command,
            configuration.bound,
            enumerated.members,
            inputs,
            snapshot.enumeration_proof,
            active_hold=hold,
        )
    else:
        candidate = prepare_resolution(command, configuration.bound, enumerated.members, inputs)
    return _batch(request, candidate, candidate.records)


def prepare_rollover_request(request: RolloverPreparationRequest) -> PreparedSchedulerBatch:
    snapshot = request.snapshot
    _verify_cut(request.context, request.command_bytes, snapshot.cut)
    command = PhysicalRootRolloverCommand.model_validate_json(request.command_bytes)
    if command.canonical_bytes() != request.command_bytes:
        raise SchedulerDomainError("noncanonical rollover command")
    result = prepare_rollover(
        snapshot.current, command, snapshot.issued, submission_id=snapshot.cut.submission_id
    )
    return _batch(request, result, result.records)
