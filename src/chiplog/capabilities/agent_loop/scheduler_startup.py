"""Immutable selected-record validation; this module never prepares a command.

The broker authenticates the complete journal and physical-store cuts and selects
the admitted historical validator. These DTOs alone establish no authority.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from pydantic import Field

from .contracts import RunRecord, SchedulerRootReference
from .domain import validate_record
from .recovery_contracts import (
    Absent,
    Identity,
    LeaseBinding,
    PhysicalRootBinding,
    Present,
    RecoveryDTO,
    RolloverPredecessor,
)
from .scheduler_configuration import (
    ConfigurationSnapshot,
    PolicyRevision,
    ScheduleRevision,
    enumerate_definition,
    policy_head,
    schedule_head,
    stream_definition,
)
from .scheduler_contracts import (
    DueCoordinate,
    ExhaustedLease,
    GenesisLease,
    HeldLease,
    MaterializationCommitment,
    OverflowResolved,
    PhysicalRootRolloverCommand,
    ScheduledIntervalDecision,
    SchedulerContextRef,
    SchedulerIntervalBoundHead,
    SchedulerIntervalResolutionDecision,
    SchedulerLineageView,
    SchedulerOverflowHold,
)
from .scheduler_domain import COORDINATE_VERSION, execution_subjects, policy_branch, verify_manifest
from .scheduler_leases import lease_fence_fingerprint, lease_payload_fingerprint
from .scheduler_materialization import (
    SCHEMA_DAG,
    IntervalParentPrimitive,
    ScheduledBatchPrimitiveDomainV1,
    SchedulerCanonicalMember,
    _compile,
)
from .scheduler_preparation import SchedulerLeaseTransitionRecord
from .scheduler_rollover import rollover_payload_fingerprint


class SchedulerStartupHold(ValueError):
    def __init__(self, tenant: str, identity: str, reason: str) -> None:
        super().__init__(f"scheduler.startup tenant={tenant} record={identity}: {reason}")
        self.tenant = tenant
        self.identity = identity
        self.reason = reason


class SelectedSchedulerBatch(RecoveryDTO):
    """Original protected selection, not a new request or current authorization."""

    tenant_id: Identity
    physical_database_id: Identity
    publication_ordinal: int = Field(ge=0)
    decision: Present
    owner: Literal["agent_loop"] = "agent_loop"
    historical_validator: Literal["chiplog.scheduler.selected-records.v1"]
    context: SchedulerContextRef
    command_id: Identity
    operation: Identity
    records: tuple[SchedulerCanonicalMember, ...] = Field(min_length=1)


class MaterializedSchedulerRow(RecoveryDTO):
    tenant_id: Identity
    physical_database_id: Identity
    publication_ordinal: int = Field(ge=0)
    member_ordinal: int = Field(ge=0)
    decision: Present
    owner: Literal["agent_loop"] = "agent_loop"
    record: SchedulerCanonicalMember


@dataclass(frozen=True)
class SchedulerStartupIndex:
    schedules: tuple[ScheduleRevision, ...]
    policies: tuple[PolicyRevision, ...]
    bounds: tuple[tuple[str, SchedulerIntervalBoundHead], ...]
    selected_record_ids: tuple[str, ...]
    registered_genesis: tuple[tuple[str, DueCoordinate, Present], ...]
    lineages: tuple[SchedulerLineageView, ...]
    active_holds: tuple[SchedulerOverflowHold, ...]


def _hold(batch: SelectedSchedulerBatch, identity: str, reason: str) -> SchedulerStartupHold:
    return SchedulerStartupHold(batch.tenant_id, identity, reason)


def _payload(batch: SelectedSchedulerBatch, member: SchedulerCanonicalMember) -> bytes:
    try:
        raw = base64.b64decode(member.canonical_base64, validate=True)
        if base64.b64encode(raw).decode() != member.canonical_base64:
            raise ValueError("base64 alias")
        if hashlib.sha256(raw).hexdigest() != member.fingerprint:
            raise ValueError("record fingerprint mismatch")
        return raw
    except ValueError as error:
        raise _hold(batch, member.record_id, "invalid selected bytes") from error


def _reference(member: SchedulerCanonicalMember) -> Present:
    return Present(head=member.record_id, fingerprint=member.fingerprint)


def _canonical(value: object) -> bytes:
    def ordered(item: object) -> object:
        if isinstance(item, dict):
            return {
                key: ordered(item[key])
                for key in sorted(item, key=lambda key: (key != "kind", key))
            }
        if isinstance(item, (list, tuple)):
            return [ordered(child) for child in item]
        return item

    return json.dumps(ordered(value), ensure_ascii=False, separators=(",", ":")).encode()


def _hash(domain: str, value: object) -> str:
    return hashlib.sha256(_canonical([domain, value])).hexdigest()


def _body_ref(domain: str, body: object) -> Present:
    fingerprint = hashlib.sha256(_canonical(body)).hexdigest()
    return Present(head=domain + ":" + fingerprint, fingerprint=fingerprint)


def _overflow(
    batch: SelectedSchedulerBatch,
    schedules: dict[str, tuple[ScheduleRevision, SchedulerCanonicalMember]],
    policies: dict[str, tuple[PolicyRevision, SchedulerCanonicalMember]],
    bounds: dict[str, tuple[SchedulerIntervalBoundHead, SchedulerCanonicalMember]],
    intervals: dict[
        str, tuple[ScheduledIntervalDecision | SchedulerIntervalResolutionDecision, Present]
    ],
    genesis: dict[str, tuple[DueCoordinate, Present]],
    holds: dict[str, SchedulerOverflowHold],
) -> None:
    if len(batch.records) != 1 or batch.records[0].record_kind != "overflow-hold":
        raise _hold(batch, batch.decision.head, "overflow publishes exactly one hold")
    member = batch.records[0]
    try:
        raw = _payload(batch, member)
        hold = SchedulerOverflowHold.model_validate_json(raw)
        if (
            member.schema_id != "chiplog.scheduler.overflow-hold.v1"
            or _canonical(hold.model_dump(mode="json")) != raw
            or hold.state.kind != "ACTIVE"
            or hold.command.command_id != batch.command_id
        ):
            raise ValueError("overflow schema/state/command mismatch")
        schedule_id = hold.boundary.schedule_definition_head.schedule_id
        if (
            schedule_id in holds
            or schedule_id not in schedules
            or schedule_id not in policies
            or schedule_id not in bounds
        ):
            raise ValueError("active rival hold or missing configuration")
        schedule, policy, bound = (
            schedules[schedule_id][0],
            policies[schedule_id][0],
            bounds[schedule_id][0],
        )
        if (
            hold.boundary.schedule_definition_head != schedule_head(schedule)
            or hold.boundary.missed_occurrence_policy_head != policy_head(policy)
            or hold.bound_head != bound
            or hold.operator_recovery_owner != schedule.definition.run_inputs.principal
        ):
            raise ValueError("overflow historical configuration mismatch")
        previous = intervals.get(schedule_id)
        if previous is None:
            if (
                not isinstance(hold.boundary.predecessor_interval, Absent)
                or schedule_id not in genesis
                or hold.boundary.previous_due_boundary != genesis[schedule_id][0]
            ):
                raise ValueError("overflow first boundary differs from registered genesis")
        elif (
            hold.boundary.predecessor_interval != previous[1]
            or hold.boundary.previous_due_boundary != previous[0].resulting_boundary
        ):
            raise ValueError("overflow preceding terminal interval mismatch")
        proof = (
            hold.evidence.enumeration_completeness_proof
            if hold.evidence.kind == "STREAMING_MANIFEST"
            else Present(head="unused-full-evidence-proof", fingerprint="0" * 64)
        )
        streamed = stream_definition(
            ConfigurationSnapshot(
                schedule=schedule, policy=policy, bound=bound, active_hold=Absent()
            ),
            hold.boundary,
            (),
            proof,
        )
        if streamed.evidence != hold.evidence:
            raise ValueError("overflow complete historical enumeration mismatch")
        if streamed.member_count > bound.bound.max_member_count:
            dimension, actual, limit = (
                "MEMBER_COUNT",
                streamed.member_count,
                bound.bound.max_member_count,
            )
        elif streamed.manifest_bytes > bound.bound.max_manifest_bytes:
            dimension, actual, limit = (
                "MANIFEST_BYTES",
                streamed.manifest_bytes,
                bound.bound.max_manifest_bytes,
            )
        else:
            if hold.evidence.kind != "FULL_MANIFEST":
                raise ValueError("whole-batch sizing requires complete full manifest")
            # Pinned historical compiler measures bytes only. Its hypothetical
            # records never escape or replace the selected immutable output.
            parent = IntervalParentPrimitive(
                kind="ORDINARY",
                command=hold.command,
                boundary=hold.boundary,
                current_bound=bound,
                original_bound=bound,
                original_hold=Absent(),
                operator_proof=Absent(),
                manifest=hold.evidence.manifest,
                branch=policy_branch(policy.policy, streamed.member_count),
                run_inputs=schedule.definition.run_inputs,
            )
            measured = _compile(parent, hold.evidence.manifest.members).serialized_batch_bytes
            dimension, actual, limit = (
                "SERIALIZED_BATCH_BYTES",
                measured,
                bound.bound.max_serialized_batch_bytes,
            )
        if actual <= limit or (hold.exceeded_dimension, hold.actual_value, hold.limit) != (
            dimension,
            actual,
            limit,
        ):
            raise ValueError("overflow dimension/precedence/actual/limit mismatch")
        primitive = {
            "command": hold.command.model_dump(mode="json"),
            "boundary": hold.boundary.model_dump(mode="json"),
            "bound_head": bound.model_dump(mode="json"),
            "dimension": dimension,
            "actual": actual,
            "limit": limit,
            "evidence": hold.evidence.model_dump(mode="json"),
            "operator": hold.operator_recovery_owner,
        }
        if (
            hold.hold != _body_ref("scheduler-overflow-hold-v1", primitive)
            or member.record_id != hold.hold.head
        ):
            raise ValueError("overflow immutable identity mismatch")
        holds[schedule_id] = hold
    except ValueError as error:
        raise _hold(batch, member.record_id, "invalid immutable overflow chain") from error


def _rollover(
    batch: SelectedSchedulerBatch,
    lineages: dict[str, SchedulerLineageView],
    known_epochs: set[str],
    known_lease_heads: set[str],
) -> None:
    kinds = (
        "rollover-decision",
        "physical-epoch-terminal",
        "rollover-physical-root",
        "rollover-root-lease-genesis",
        "rollover-edge",
        "rollover-physical-selector",
    )
    if tuple(member.record_kind for member in batch.records) != tuple(
        "scheduler." + kind for kind in kinds
    ):
        raise _hold(batch, batch.decision.head, "incomplete/reordered rollover six-record batch")

    def encode(value: object) -> bytes:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()

    def digest(value: object) -> str:
        return hashlib.sha256(encode(value)).hexdigest()

    try:
        decoded = [json.loads(_payload(batch, member)) for member in batch.records]
        for member, body in zip(batch.records, decoded, strict=True):
            if member.schema_id != "chiplog." + member.record_kind + ".v1" or encode(
                body
            ) != _payload(batch, member):
                raise ValueError("rollover schema/canonical bytes mismatch")
        decision = decoded[0]
        primitive = decision["primitive"]
        current = SchedulerLineageView.model_validate(primitive["current"])
        command = PhysicalRootRolloverCommand.model_validate(primitive["command"])
        if (
            primitive
            != {
                "schema": "chiplog.scheduler.rollover-primitive.v1",
                "command": command.model_dump(mode="json"),
                "current": current.model_dump(mode="json"),
            }
            or lineages.get(current.lineage.root_id) != current
        ):
            raise ValueError("rollover primitive/current lineage mismatch")
        lease, physical = current.lease, current.physical_root
        if (
            lease.kind != "GENERATION_EXHAUSTED_HOLD"
            or physical.selector_version == 2**64 - 1
            or lease.preceding_held_lease.generation != 2**64 - 1
        ):
            raise ValueError("rollover lacks exhausted predecessor epoch")
        fence = command.fence
        if (
            command.identity.command_id != batch.command_id
            or fence.authority.command_id != batch.command_id
            or fence.authority.command_payload_fingerprint != rollover_payload_fingerprint(command)
            or fence.authority.predecessor_rollover != current.predecessor_rollover
            or fence.lineage != current.lineage
            or fence.physical_root != physical
        ):
            raise ValueError("rollover immutable authority/fence mismatch")
        # Exact exhaustion tuple remains an immutable causal prerequisite; no
        # current operator/session authorization is rerun during startup.
        if (
            fence.exhaustion.hold_head != lease.lease_head
            or fence.exhaustion.exhausted_command_id != lease.exhausted_command_id
            or fence.exhaustion.lease != lease.preceding_held_lease
            or fence.exhaustion.authority_epoch != lease.authority_epoch
            or lease.trusted_expiry != lease.preceding_held_lease.trusted_expiry
        ):
            raise ValueError("rollover exhaustion binding differs")
        fingerprint = digest(primitive)
        decision_id = "scheduler-rollover-v1:" + fingerprint
        edge_id = decision_id + "/edge"
        epoch_id = "scheduler-physical-epoch-v1:" + digest([fingerprint, "epoch"])
        genesis_head = "scheduler-genesis-v1:" + digest([fingerprint, "UNLEASED", 0])
        if epoch_id in known_epochs or genesis_head in known_lease_heads:
            raise ValueError("rollover aliases physical history")
        if decision != {
            "primitive": primitive,
            "primitive_fingerprint": fingerprint,
            "successor_epoch_id": epoch_id,
            "genesis_lease_head": genesis_head,
            "reciprocal_edge_id": edge_id,
        }:
            raise ValueError("rollover decision primitive hash mismatch")
        decision_ref = _reference(batch.records[0])
        common = {
            "lineage": current.lineage.model_dump(mode="json"),
            "decision": decision_ref.model_dump(mode="json"),
        }
        genesis = GenesisLease(lease_head=genesis_head)
        pair = RolloverPredecessor(decision=decision_ref, edge=_reference(batch.records[4]))
        expected = (
            (decision_id, decision),
            (
                decision_id + "/old-terminal",
                {
                    **common,
                    "old_physical_root": physical.model_dump(mode="json"),
                    "exhaustion": lease.model_dump(mode="json"),
                    "state": "TERMINALLY_FENCED",
                    "successor_epoch_id": epoch_id,
                    "edge_id": edge_id,
                },
            ),
            (
                epoch_id,
                {
                    **common,
                    "epoch_id": epoch_id,
                    "predecessor_epoch_id": physical.current_epoch_id,
                    "genesis": genesis.model_dump(mode="json"),
                    "edge_id": edge_id,
                },
            ),
            (
                genesis_head,
                {
                    **common,
                    "epoch": _reference(batch.records[2]).model_dump(mode="json"),
                    "lease": genesis.model_dump(mode="json"),
                },
            ),
            (
                edge_id,
                {
                    **common,
                    "old_epoch_id": physical.current_epoch_id,
                    "old_terminal": _reference(batch.records[1]).model_dump(mode="json"),
                    "new_epoch": _reference(batch.records[2]).model_dump(mode="json"),
                    "genesis": _reference(batch.records[3]).model_dump(mode="json"),
                    "predecessor_rollover": current.predecessor_rollover.model_dump(mode="json"),
                },
            ),
            (
                decision_id + "/selector",
                {
                    **common,
                    "selector_id": physical.selector_id,
                    "previous": physical.model_dump(mode="json"),
                    "selector_version": physical.selector_version + 1,
                    "selected_epoch": _reference(batch.records[2]).model_dump(mode="json"),
                    "rollover": pair.model_dump(mode="json"),
                },
            ),
        )
        for member, (identity, body) in zip(batch.records, expected, strict=True):
            if member.record_id != identity or _payload(batch, member) != encode(body):
                raise ValueError("rollover decision/edge/selector reciprocal mismatch")
        lineages[current.lineage.root_id] = current.model_copy(
            update={
                "physical_root": PhysicalRootBinding(
                    selector_id=physical.selector_id,
                    selector_head=batch.records[5].record_id,
                    selector_version=physical.selector_version + 1,
                    current_epoch_id=epoch_id,
                    current_epoch_head=batch.records[2].record_id,
                ),
                "lease": genesis,
                "predecessor_rollover": pair,
            }
        )
        known_epochs.add(epoch_id)
        known_lease_heads.add(genesis_head)
    except (ValueError, KeyError, TypeError) as error:
        raise _hold(
            batch, batch.records[0].record_id, "invalid immutable rollover chain"
        ) from error


def _lease_transition(
    batch: SelectedSchedulerBatch,
    lineages: dict[str, SchedulerLineageView],
    used_leases: dict[str, set[str]],
) -> None:
    if len(batch.records) != 1:
        raise _hold(batch, batch.decision.head, "incomplete lease transition")
    member = batch.records[0]
    try:
        if (
            member.record_kind != "scheduler.lease_transition"
            or member.schema_id != "chiplog.scheduler.lease-transition.v1"
        ):
            raise ValueError("unknown lease record schema")
        raw = _payload(batch, member)
        record = SchedulerLeaseTransitionRecord.model_validate_json(raw)
        if (
            record.canonical_bytes() != raw
            or record.tenant_id != batch.tenant_id
            or record.context != batch.context
        ):
            raise ValueError("lease record context/canonical mismatch")
        command, issued, candidate = record.command, record.issued, record.candidate
        current = lineages.get(record.previous.lineage.root_id)
        if (
            current is None
            or record.previous != current
            or command.lineage != current.lineage
            or command.physical_root != current.physical_root
            or command.observed_lease != current.lease
        ):
            raise ValueError("lease transition does not consume exact lineage/epoch/lease")
        if (
            batch.operation != "scheduler." + command.kind.lower()
            or command.identity.command_id != batch.command_id
            or command.proposed_holder_id != batch.context.service_identity
            or command.proposed_holder_session_id != batch.context.session_id
        ):
            raise ValueError("lease operation/command/session mismatch")
        proof = command.clock_proof
        if (
            proof != issued.proof
            or proof.command_id != command.identity.command_id
            or proof.command_payload_fingerprint != lease_payload_fingerprint(command)
            or proof.fence_fingerprint != lease_fence_fingerprint(current)
            or issued.holder_id != command.proposed_holder_id
            or issued.holder_session_id != command.proposed_holder_session_id
        ):
            raise ValueError("historical proof immutable scope mismatch")
        fingerprint = hashlib.sha256(
            json.dumps(
                ["scheduler-lease-transition-v1", command.model_dump(mode="json")],
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        if (
            candidate.command_fingerprint != fingerprint
            or candidate.transition_id != "scheduler-lease-transition-v1:" + fingerprint
            or member.record_id != candidate.transition_id
            or candidate.observed_fence_fingerprint != lease_fence_fingerprint(current)
        ):
            raise ValueError("lease transition identity mismatch")
        used = used_leases[current.lineage.root_id]
        if (
            len(set(issued.used_lease_ids)) != len(issued.used_lease_ids)
            or set(issued.used_lease_ids) != used
        ):
            raise ValueError("lease identity history differs from selected chain")
        if not issued.now < command.proposed_expiry <= issued.now + issued.max_lease_duration:
            raise ValueError("historical expiry horizon mismatch")
        old = current.lease
        head = "scheduler-lease-head-v1:" + fingerprint
        if old.kind == "GENERATION_EXHAUSTED_HOLD":
            raise ValueError("exhausted epoch requires rollover")
        if old.kind == "HELD" and (
            old.binding.generation == 0
            or old.binding.lease_id not in used
            or old.binding.clock_contract_version != proof.clock_contract_version
        ):
            raise ValueError("historical held lease unavailable")
        proposed: HeldLease | ExhaustedLease
        if command.kind == "ACQUIRE":
            if (
                old.kind != "UNLEASED"
                or command.proposed_generation != 1
                or command.proposed_lease_id in used
            ):
                raise ValueError("acquire does not consume fresh genesis")
            proposed = HeldLease(
                binding=LeaseBinding(
                    lease_head=head,
                    holder_id=issued.holder_id,
                    holder_session_id=issued.holder_session_id,
                    lease_id=command.proposed_lease_id,
                    generation=1,
                    trusted_expiry=command.proposed_expiry,
                    clock_contract_version=proof.clock_contract_version,
                )
            )
        elif command.kind == "RENEW":
            if (
                old.kind != "HELD"
                or issued.now >= old.binding.trusted_expiry
                or issued.holder_id != old.binding.holder_id
                or issued.holder_session_id != old.binding.holder_session_id
                or command.proposed_lease_id != old.binding.lease_id
                or command.proposed_generation != old.binding.generation
                or command.proposed_expiry <= old.binding.trusted_expiry
            ):
                raise ValueError("renew is not same live historical lease")
            proposed = HeldLease(
                binding=old.binding.model_copy(
                    update={"lease_head": head, "trusted_expiry": command.proposed_expiry}
                )
            )
        else:
            if (
                old.kind != "HELD"
                or issued.now < old.binding.trusted_expiry
                or command.proposed_lease_id in used
            ):
                raise ValueError("takeover lacks historical expiry/fresh identity")
            if old.binding.generation == 2**64 - 1:
                if command.proposed_generation != 2**64 - 1:
                    raise ValueError("lease generation wrapped")
                proposed = ExhaustedLease(
                    lease_head=head,
                    exhausted_command_id=command.identity.command_id,
                    trusted_expiry=old.binding.trusted_expiry,
                    authority_epoch=issued.authority_epoch,
                    preceding_held_lease=old.binding,
                )
            else:
                if command.proposed_generation != old.binding.generation + 1:
                    raise ValueError("takeover generation mismatch")
                proposed = HeldLease(
                    binding=LeaseBinding(
                        lease_head=head,
                        holder_id=issued.holder_id,
                        holder_session_id=issued.holder_session_id,
                        lease_id=command.proposed_lease_id,
                        generation=command.proposed_generation,
                        trusted_expiry=command.proposed_expiry,
                        clock_contract_version=proof.clock_contract_version,
                    )
                )
        if candidate.state != proposed:
            raise ValueError("selected lease state differs from immutable transition")
        if proposed.kind == "HELD":
            used.add(proposed.binding.lease_id)
        lineages[current.lineage.root_id] = current.model_copy(update={"lease": candidate.state})
    except ValueError as error:
        raise _hold(batch, member.record_id, "invalid immutable lease chain") from error


def _materialization_members(
    batch: SelectedSchedulerBatch,
    parent: IntervalParentPrimitive,
    parent_ref: Present,
    commitment: MaterializationCommitment,
    members: tuple[SchedulerCanonicalMember, ...],
) -> None:
    """Check selected physical identities and reciprocal bodies without preparing."""
    if commitment.kind != commitment.subject.kind:
        raise ValueError("materialization subject tag mismatch")
    if commitment.subject.kind == "INDIVIDUAL":
        if (
            len(commitment.dispositions) != 1
            or commitment.dispositions[0].kind != "CLAIMED_INDIVIDUALLY"
            or commitment.dispositions[0].occurrence.occurrence_id
            != commitment.subject.occurrence_id
        ):
            raise ValueError("individual disposition belongs to another subject")
    elif tuple(
        item.occurrence for item in commitment.dispositions
    ) != parent.manifest.members or any(
        item.kind != "COALESCED_INTO"
        or item.aggregate_id != commitment.subject.aggregate_id
        or item.manifest_fingerprint != parent.manifest.fingerprint
        for item in commitment.dispositions
    ):
        raise ValueError("coalesced dispositions do not cover exact aggregate")
    if commitment.schema_dependency_manifest != _body_ref("scheduler-schema-dag-v1", SCHEMA_DAG):
        raise ValueError("historical schema manifest reference differs")
    expected_kinds = (
        "batch-primitive",
        "stable-lineage",
        "physical-root",
        "physical-selector",
        "root-lease-genesis",
        "agent_loop.run",
        "run-initialization",
        "root-run-reciprocal",
        *("occurrence-disposition" for _ in commitment.dispositions),
        *(("coalesced-aggregate",) if commitment.subject.kind == "COALESCED" else ()),
        "epoch-companion",
    )
    if tuple(member.record_kind for member in members) != expected_kinds:
        raise ValueError("missing/reordered root initialization members")
    primitive = ScheduledBatchPrimitiveDomainV1.model_validate_json(_payload(batch, members[0]))
    if _canonical(primitive.model_dump(mode="json")) != _payload(batch, members[0]):
        raise ValueError("noncanonical batch primitive")
    if (
        primitive.parent_decision != parent_ref
        or primitive.command != parent.command
        or primitive.boundary != parent.boundary
        or primitive.bound_head != parent.current_bound
        or primitive.manifest != parent.manifest
        or primitive.run_inputs != parent.run_inputs
        or primitive.subject != commitment.subject
    ):
        raise ValueError("batch primitive differs from exact interval")
    primitive_ref = _body_ref("scheduled-batch-primitive-v1", primitive.model_dump(mode="json"))
    if (
        _reference(members[0]) != primitive_ref
        or commitment.primitive_domain_fingerprint != primitive_ref.fingerprint
    ):
        raise ValueError("batch primitive identity mismatch")
    run_id = "scheduled-run-v1:" + _hash(
        "scheduled-run-v1",
        [parent_ref.model_dump(mode="json"), commitment.subject.model_dump(mode="json")],
    )
    fields = {
        "subject": commitment.subject.model_dump(mode="json"),
        "initial_run_id": run_id,
        "schedule_id": parent.boundary.schedule_definition_head.schedule_id,
        "schedule_revision": parent.boundary.schedule_definition_head.schedule_revision,
        "policy_revision": parent.boundary.missed_occurrence_policy_head.policy_revision,
    }
    root_fingerprint = _hash("execution-lineage-v1", fields)
    root_id = "execution-lineage-v1:" + root_fingerprint
    lineage = commitment.lineage
    expected_lineage = {
        **fields,
        "root_id": root_id,
        "root_fingerprint": root_fingerprint,
        "current_run_id": run_id,
    }
    lineage_head = "execution-lineage-head-v1:" + _hash(
        "execution-lineage-head-v1", expected_lineage
    )
    if (
        lineage.model_dump(mode="json") != {**expected_lineage, "lineage_head": lineage_head}
        or primitive.root_id != root_id
        or primitive.root_fingerprint != root_fingerprint
        or primitive.initial_run_id != run_id
    ):
        raise ValueError("stable lineage initial identity mismatch")
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
    epoch_ref = _body_ref("physical-root-head-v1", epoch_body)
    physical = commitment.physical_root
    selector_body = {
        "lineage_id": root_id,
        "selector_id": "physical-root-selector-v1:" + _hash("physical-root-selector-v1", root_id),
        "selector_version": 0,
        "current_epoch_id": epoch_id,
        "current_epoch_head": epoch_ref.head,
    }
    selector_ref = _body_ref("physical-root-selector-head-v1", selector_body)
    if (
        physical.model_dump(mode="json")
        != {key: value for key, value in selector_body.items() if key != "lineage_id"}
        | {"selector_head": selector_ref.head}
        or commitment.physical_epoch != epoch_ref
    ):
        raise ValueError("physical root/selector mismatch")
    genesis = commitment.genesis_lease
    if genesis.lease_head != "execution-root-lease-genesis-v1:" + _hash(
        "execution-root-lease-genesis-v1", primitive.model_dump(mode="json")
    ):
        raise ValueError("genesis lease primitive mismatch")
    run = RunRecord.model_validate_json(_payload(batch, members[5]))
    validate_record(None, run)
    inputs = parent.run_inputs
    expected_run_fields = {
        "tenant": inputs.tenant,
        "principal": inputs.principal,
        "run_id": run_id,
        "state": "CREATED",
        "predecessor": None,
        "prompt": inputs.prompt,
        "policy": inputs.policy,
        "origin": inputs.origin,
        "contour_head": inputs.contour_head,
        "policy_head": inputs.policy_head,
        "worker_session": inputs.worker_session,
        "event": "RunCreated",
    }
    if (
        any(getattr(run, key) != value for key, value in expected_run_fields.items())
        or run.canonical_bytes() != _payload(batch, members[5])
        or members[5].record_id != run.head
        or commitment.initial_run != Present(head=run.head, fingerprint=run.digest())
    ):
        raise ValueError("initial Run differs from immutable initializer")
    if (
        not isinstance(run.root_binding, SchedulerRootReference)
        or run.root_binding.root_id != root_id
        or run.root_binding.root_fingerprint != root_fingerprint
        or run.root_binding.initial_run_id != run_id
        or base64.b64decode(run.root_binding.subject_canonical_base64, validate=True)
        != commitment.subject.canonical_bytes()
    ):
        raise ValueError("initial Run reciprocal stable root mismatch")
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
    init_ref = _body_ref("scheduler-run-initialization-v1", initialization)
    reciprocal = {
        "initialization": init_ref.model_dump(mode="json"),
        "run_id": run_id,
        "run_head": run.head,
        "lineage": lineage.model_dump(mode="json"),
        "physical_root": physical.model_dump(mode="json"),
        "genesis_lease": genesis.model_dump(mode="json"),
    }
    reciprocal_ref = _body_ref("scheduler-root-run-reciprocal-v1", reciprocal)
    if (
        commitment.initialization != init_ref
        or commitment.reciprocal_run_root != reciprocal_ref
        or commitment.parent_decision != parent_ref
    ):
        raise ValueError("initialization/reciprocal references mismatch")
    bodies = (
        (1, lineage_head, lineage.model_dump(mode="json")),
        (2, epoch_ref.head, epoch_body),
        (3, selector_ref.head, selector_body),
        (
            4,
            genesis.lease_head,
            {
                "lineage": lineage.model_dump(mode="json"),
                "physical_root": physical.model_dump(mode="json"),
                "lease": genesis.model_dump(mode="json"),
                "run_id": run_id,
            },
        ),
        (6, init_ref.head, initialization),
        (7, reciprocal_ref.head, reciprocal),
    )
    for index, identity, body in bodies:
        if members[index].record_id != identity or _payload(batch, members[index]) != _canonical(
            body
        ):
            raise ValueError("root/Run companion body mismatch")
    for offset, disposition in enumerate(commitment.dispositions, 8):
        if (
            disposition.kind == "SKIPPED"
            or disposition.materialization.materialization_id != primitive_ref.head
            or disposition.materialization.primitive_domain_fingerprint != primitive_ref.fingerprint
        ):
            raise ValueError("materializing disposition primitive mismatch")
        body = disposition.model_dump(mode="json")
        if _reference(members[offset]) != _body_ref("occurrence-disposition-v1", body) or _payload(
            batch, members[offset]
        ) != _canonical(body):
            raise ValueError("materializing disposition bytes mismatch")
    if commitment.subject.kind == "COALESCED":
        aggregate = members[-2]
        if aggregate.record_id != commitment.subject.aggregate_id or _payload(
            batch, aggregate
        ) != _canonical(
            {
                "subject": commitment.subject.model_dump(mode="json"),
                "boundary": parent.boundary.model_dump(mode="json"),
                "manifest": parent.manifest.model_dump(mode="json"),
            }
        ):
            raise ValueError("coalesced aggregate manifest mismatch")
    companion = [
        member.model_dump(mode="json")
        for member in members
        if member.record_kind in {"physical-root", "physical-selector", "root-lease-genesis"}
    ]
    if (
        commitment.companion_manifest != _body_ref("scheduler-epoch-companion-v1", companion)
        or _reference(members[-1]) != commitment.companion_manifest
        or _payload(batch, members[-1]) != _canonical(companion)
    ):
        raise ValueError("complete epoch companion manifest mismatch")
    finalized = _hash(
        "scheduled-batch-finalized-members-v1",
        [member.model_dump(mode="json") for member in members],
    )
    envelope = _hash(
        "scheduled-batch-envelope-v1",
        {
            "finalized_members": finalized,
            "companion": commitment.companion_manifest.model_dump(mode="json"),
            "schema_dag": commitment.schema_dependency_manifest.model_dump(mode="json"),
        },
    )
    if (
        commitment.finalized_members_fingerprint != finalized
        or commitment.batch_fingerprint != envelope
    ):
        raise ValueError("finalized initialization envelope mismatch")


def _ordinary_interval(
    batch: SelectedSchedulerBatch,
    schedules: dict[str, tuple[ScheduleRevision, SchedulerCanonicalMember]],
    policies: dict[str, tuple[PolicyRevision, SchedulerCanonicalMember]],
    bounds: dict[str, tuple[SchedulerIntervalBoundHead, SchedulerCanonicalMember]],
    intervals: dict[
        str, tuple[ScheduledIntervalDecision | SchedulerIntervalResolutionDecision, Present]
    ],
    genesis: dict[str, tuple[DueCoordinate, Present]],
    lineages: dict[str, SchedulerLineageView],
    used_leases: dict[str, set[str]],
    holds: dict[str, SchedulerOverflowHold],
) -> None:
    """Validate recorded algebra and envelope; never choose or prepare an interval."""
    if (
        len(batch.records) < 2
        or batch.records[0].record_kind != "interval-parent"
        or batch.records[-1].record_kind != "interval-result"
    ):
        raise _hold(batch, batch.decision.head, "missing interval parent/result")
    parent_member, result_member = batch.records[0], batch.records[-1]
    try:
        parent_raw, result_raw = _payload(batch, parent_member), _payload(batch, result_member)
        parent = IntervalParentPrimitive.model_validate_json(parent_raw)
        resolving = batch.operation == "scheduler.resolve_interval"
        result = (
            SchedulerIntervalResolutionDecision.model_validate_json(result_raw)
            if resolving
            else ScheduledIntervalDecision.model_validate_json(result_raw)
        )
        if (
            _canonical(parent.model_dump(mode="json")) != parent_raw
            or _canonical(result.model_dump(mode="json")) != result_raw
        ):
            raise ValueError("noncanonical interval record")
        if (
            parent.kind != ("RESOLUTION" if resolving else "ORDINARY")
            or parent.command.command_id != batch.command_id
            or parent.run_inputs.tenant != batch.tenant_id
        ):
            raise ValueError("wrong immutable interval identity")
        if (
            parent_member.record_id != "scheduler-parent-v1:" + parent_member.fingerprint
            or result_member.record_id != parent_member.record_id + ":result"
        ):
            raise ValueError("interval primitive identity mismatch")
        schedule_id = parent.boundary.schedule_definition_head.schedule_id
        active_hold = holds.get(schedule_id)
        if resolving != (active_hold is not None):
            raise ValueError("ordinary/resolution active hold mismatch")
        if schedule_id not in schedules or schedule_id not in policies or schedule_id not in bounds:
            raise ValueError("missing historical configuration")
        schedule, policy, bound = (
            schedules[schedule_id][0],
            policies[schedule_id][0],
            bounds[schedule_id][0],
        )
        if (
            parent.boundary.schedule_definition_head != schedule_head(schedule)
            or parent.boundary.missed_occurrence_policy_head != policy_head(policy)
            or parent.current_bound != bound
        ):
            raise ValueError("interval configuration heads differ")
        if parent.run_inputs != schedule.definition.run_inputs:
            raise ValueError("interval original Run inputs differ")
        if active_hold is None:
            if (
                parent.original_bound != bound
                or not isinstance(parent.original_hold, Absent)
                or not isinstance(parent.operator_proof, Absent)
            ):
                raise ValueError("ordinary interval has resolution inputs")
        else:
            if (
                parent.original_hold != active_hold.hold
                or parent.original_bound != active_hold.bound_head
                or parent.boundary != active_hold.boundary
                or not isinstance(parent.operator_proof, Present)
                or bound.generation <= active_hold.bound_head.generation
                or bound.head_id == active_hold.bound_head.head_id
            ):
                raise ValueError("resolution does not consume exact active hold/successor bound")
            held_digest = (
                active_hold.evidence.manifest.fingerprint
                if active_hold.evidence.kind == "FULL_MANIFEST"
                else active_hold.evidence.manifest_digest
            )
            if parent.manifest.fingerprint != held_digest:
                raise ValueError("resolution changes held membership")
        previous = intervals.get(schedule_id)
        if previous is None:
            if (
                not isinstance(parent.boundary.predecessor_interval, Absent)
                or schedule_id not in genesis
                or parent.boundary.previous_due_boundary != genesis[schedule_id][0]
            ):
                raise ValueError(
                    "first interval differs from registered original revision0 genesis"
                )
        elif (
            parent.boundary.predecessor_interval != previous[1]
            or parent.boundary.previous_due_boundary != previous[0].resulting_boundary
        ):
            raise ValueError("interval predecessor/boundary mismatch")
        verify_manifest(parent.boundary, parent.manifest, parent.manifest.members)
        if parent.manifest != enumerate_definition(
            ConfigurationSnapshot(
                schedule=schedule, policy=policy, bound=bound, active_hold=Absent()
            ),
            parent.boundary,
            (),
        ):
            raise ValueError("recorded manifest omits historical eligible occurrences")
        if parent.branch != policy_branch(policy.policy, len(parent.manifest.members)):
            raise ValueError("recorded policy branch mismatch")
        if (
            result.decision != _reference(parent_member)
            or result.command != parent.command
            or result.branch != parent.branch
            or result.boundary != parent.boundary
            or result.manifest != parent.manifest
            or result.resulting_boundary != parent.boundary.cutoff_due_coordinate
        ):
            raise ValueError("interval result differs from primitive")
        if isinstance(result, ScheduledIntervalDecision):
            if result.bound_head != bound:
                raise ValueError("ordinary result bound differs")
        elif (
            active_hold is None
            or result.original_hold != active_hold.hold
            or result.original_bound_head != active_hold.bound_head
            or result.successor_bound_head != bound
        ):
            raise ValueError("resolution result hold/bounds differ")
        member_end = len(batch.records) - 1
        if resolving:
            marker = batch.records[-2]
            marker_body = {
                "original_hold": parent.original_hold.model_dump(mode="json"),
                "state": OverflowResolved(resolution_decision=_reference(parent_member)).model_dump(
                    mode="json"
                ),
            }
            if (
                marker.record_kind != "overflow-resolution"
                or marker.record_id != parent_member.record_id + ":hold-resolution"
                or _payload(batch, marker) != _canonical(marker_body)
            ):
                raise ValueError("resolution terminal marker differs")
            member_end -= 1
        dispositions = result.dispositions
        if tuple(item.occurrence for item in dispositions) != parent.manifest.members:
            raise ValueError("incomplete recorded disposition manifest")
        subjects = execution_subjects(parent.boundary, parent.manifest, parent.manifest.members)
        if tuple(item.subject for item in result.materializations) != subjects:
            raise ValueError("materialization subjects differ from recorded policy")
        if result.materializations:
            if (
                tuple(
                    item
                    for materialization in result.materializations
                    for item in materialization.dispositions
                )
                != dispositions
            ):
                raise ValueError("materialization disposition coverage differs")
            offset = 1
            for commitment in result.materializations:
                count = (
                    9
                    + len(commitment.dispositions)
                    + (1 if commitment.subject.kind == "COALESCED" else 0)
                )
                _materialization_members(
                    batch,
                    parent,
                    _reference(parent_member),
                    commitment,
                    batch.records[offset : offset + count],
                )
                offset += count
            if offset != member_end:
                raise ValueError("extra materialization members")
        else:
            if member_end != len(dispositions) + 1:
                raise ValueError("extra or missing interval members")
            for disposition, member in zip(dispositions, batch.records[1:member_end], strict=True):
                if (
                    disposition.kind != "SKIPPED"
                    or disposition.policy_decision != _reference(parent_member)
                    or member.record_kind != "occurrence-disposition"
                    or _payload(batch, member) != _canonical(disposition.model_dump(mode="json"))
                    or member.record_id != "occurrence-disposition-v1:" + member.fingerprint
                ):
                    raise ValueError("disposition causal join mismatch")
        for member in batch.records:
            schema = (
                "chiplog.agent-loop.record.v1"
                if member.record_kind == "agent_loop.run"
                else "chiplog.scheduler." + member.record_kind + ".v1"
            )
            if member.schema_id != schema:
                raise ValueError("unsupported historical interval schema")
        expected = _hash(
            "scheduler-interval-envelope-v1",
            {
                "complete_non_envelope_records": [
                    member.model_dump(mode="json") for member in batch.records[:-1]
                ],
                "result_without_commitment": result.model_dump(
                    mode="json", exclude={"complete_commitment"}
                ),
            },
        )
        if result.complete_commitment != expected:
            raise ValueError("interval whole-batch commitment mismatch")
        if (
            len(_canonical([member.model_dump(mode="json") for member in batch.records]))
            > bound.bound.max_serialized_batch_bytes
            or len(parent.manifest.members) > bound.bound.max_member_count
            or len(parent.manifest.canonical_bytes()) > bound.bound.max_manifest_bytes
        ):
            raise ValueError("selected ordinary interval exceeds historical bound")
        intervals[schedule_id] = (result, _reference(parent_member))
        if resolving:
            del holds[schedule_id]
        for commitment in result.materializations:
            root_id = commitment.lineage.root_id
            if root_id in lineages:
                raise ValueError("duplicate stable lineage initialization")
            lineages[root_id] = SchedulerLineageView(
                lineage=commitment.lineage,
                physical_root=commitment.physical_root,
                lease=commitment.genesis_lease,
                predecessor_rollover=Absent(),
            )
            used_leases[root_id] = set()
    except ValueError as error:
        raise _hold(batch, parent_member.record_id, "invalid immutable interval chain") from error


def _configuration(
    batch: SelectedSchedulerBatch,
    schedules: dict[str, tuple[ScheduleRevision, SchedulerCanonicalMember]],
    policies: dict[str, tuple[PolicyRevision, SchedulerCanonicalMember]],
    bounds: dict[str, tuple[SchedulerIntervalBoundHead, SchedulerCanonicalMember]],
) -> None:
    expected = {
        "scheduler.genesis": (
            "scheduler.schedule-definition",
            "scheduler.missed-policy",
            "scheduler.interval-bound",
        ),
        "scheduler.amend_schedule": ("scheduler.schedule-definition",),
        "scheduler.amend_policy": ("scheduler.missed-policy",),
        "scheduler.replace_bound": ("scheduler.interval-bound",),
    }.get(batch.operation)
    if expected is None:
        raise _hold(batch, batch.decision.head, "unsupported historical operation mapper")
    if tuple(record.record_kind for record in batch.records) != expected:
        raise _hold(batch, batch.decision.head, "incomplete or reordered configuration batch")
    schedule_id: str | None = None
    for member in batch.records:
        if member.schema_id != "chiplog." + member.record_kind + ".v1":
            raise _hold(batch, member.record_id, "unsupported historical record schema")
        raw = _payload(batch, member)
        try:
            if member.record_kind == "scheduler.schedule-definition":
                schedule = ScheduleRevision.model_validate_json(raw)
                if (
                    schedule.canonical_bytes() != raw
                    or schedule_head(schedule).head != member.record_id
                ):
                    raise ValueError("noncanonical schedule identity")
                if schedule.context != batch.context or schedule.command_id != batch.command_id:
                    raise ValueError("historical schedule context mismatch")
                if schedule.definition.run_inputs.tenant != batch.tenant_id:
                    raise ValueError("foreign Run initializer")
                schedule_id = schedule.schedule_id
                prior_schedule = schedules.get(schedule_id)
                if prior_schedule is None:
                    if (
                        batch.operation != "scheduler.genesis"
                        or schedule.revision != 0
                        or not isinstance(schedule.previous, Absent)
                    ):
                        raise ValueError("schedule genesis missing")
                elif (
                    batch.operation == "scheduler.genesis"
                    or schedule.revision != prior_schedule[0].revision + 1
                    or schedule.previous != _reference(prior_schedule[1])
                ):
                    raise ValueError("schedule predecessor mismatch")
                schedules[schedule_id] = (schedule, member)
            elif member.record_kind == "scheduler.missed-policy":
                policy = PolicyRevision.model_validate_json(raw)
                if policy.canonical_bytes() != raw or policy_head(policy).head != member.record_id:
                    raise ValueError("noncanonical policy identity")
                if policy.context != batch.context or policy.command_id != batch.command_id:
                    raise ValueError("historical policy context mismatch")
                if schedule_id is not None and policy.schedule_id != schedule_id:
                    raise ValueError("genesis streams differ")
                schedule_id = policy.schedule_id
                if schedule_id not in schedules:
                    raise ValueError("policy without schedule")
                prior_policy = policies.get(schedule_id)
                if prior_policy is None:
                    if (
                        batch.operation != "scheduler.genesis"
                        or policy.revision != 0
                        or not isinstance(policy.previous, Absent)
                    ):
                        raise ValueError("policy genesis missing")
                elif (
                    batch.operation == "scheduler.genesis"
                    or policy.revision != prior_policy[0].revision + 1
                    or policy.previous != _reference(prior_policy[1])
                ):
                    raise ValueError("policy predecessor mismatch")
                policies[schedule_id] = (policy, member)
            else:
                bound = SchedulerIntervalBoundHead.model_validate_json(raw)
                if bound.canonical_bytes() != raw or bound.head_id != member.record_id:
                    raise ValueError("noncanonical bound identity")
                pending = bound.model_copy(update={"head_id": "pending"})
                digest = hashlib.sha256(
                    pending.canonical_bytes()
                    + batch.context.canonical_bytes()
                    + batch.command_id.encode()
                ).hexdigest()
                if (
                    bound.head_id != "scheduler-bound-v1:" + digest
                    or bound.owner_id != batch.context.service_identity
                ):
                    raise ValueError("bound identity preimage mismatch")
                if schedule_id is None:
                    matches = [
                        key
                        for key, (_, previous) in bounds.items()
                        if bound.predecessor == _reference(previous)
                    ]
                    if len(matches) != 1:
                        raise ValueError("bound predecessor missing or ambiguous")
                    schedule_id = matches[0]
                prior_bound = bounds.get(schedule_id)
                if prior_bound is None:
                    if (
                        batch.operation != "scheduler.genesis"
                        or bound.generation != 0
                        or not isinstance(bound.predecessor, Absent)
                    ):
                        raise ValueError("bound genesis missing")
                elif (
                    batch.operation == "scheduler.genesis"
                    or bound.generation != prior_bound[0].generation + 1
                    or bound.predecessor != _reference(prior_bound[1])
                ):
                    raise ValueError("bound predecessor mismatch")
                bounds[schedule_id] = (bound, member)
        except ValueError as error:
            raise _hold(batch, member.record_id, "invalid immutable configuration chain") from error


def validate_selected_scheduler_records(
    *,
    tenant_id: str,
    physical_database_id: str,
    selected: tuple[SelectedSchedulerBatch, ...],
    materialized: tuple[MaterializedSchedulerRow, ...],
) -> SchedulerStartupIndex:
    """Validate a complete protected cut and return indexes, never new records.

    Supports configuration, ordinary/resolution interval, overflow, lease and
    rollover records. Unknown external Run-successor/lineage-advance records hold
    until the R14 registered mapper is integrated. The caller must authenticate
    exhaustive selected/physical cuts and original clock/operator/enumeration
    evidence, and choose the exact admitted historical validator/compiler closure.
    No supplied DTO, source-version string or internally matching proof grants
    that independent provenance. These checks perform no live authorization.
    """
    schedules: dict[str, tuple[ScheduleRevision, SchedulerCanonicalMember]] = {}
    policies: dict[str, tuple[PolicyRevision, SchedulerCanonicalMember]] = {}
    bounds: dict[str, tuple[SchedulerIntervalBoundHead, SchedulerCanonicalMember]] = {}
    intervals: dict[
        str, tuple[ScheduledIntervalDecision | SchedulerIntervalResolutionDecision, Present]
    ] = {}
    genesis: dict[str, tuple[DueCoordinate, Present]] = {}
    lineages: dict[str, SchedulerLineageView] = {}
    used_leases: dict[str, set[str]] = {}
    known_epochs: set[str] = set()
    known_lease_heads: set[str] = set()
    holds: dict[str, SchedulerOverflowHold] = {}
    expected_rows: list[MaterializedSchedulerRow] = []
    identities: set[str] = set()
    decisions: set[str] = set()
    previous_ordinal = -1
    for batch in selected:
        if (
            batch.tenant_id != tenant_id
            or batch.physical_database_id != physical_database_id
            or batch.context.tenant_id != tenant_id
        ):
            raise _hold(batch, batch.decision.head, "foreign tenant or physical database")
        if batch.publication_ordinal <= previous_ordinal or batch.decision.head in decisions:
            raise _hold(batch, batch.decision.head, "duplicate or reordered selected publication")
        previous_ordinal = batch.publication_ordinal
        decisions.add(batch.decision.head)
        for ordinal, record in enumerate(batch.records):
            if record.record_id in identities:
                raise _hold(batch, record.record_id, "duplicate selected record identity")
            identities.add(record.record_id)
            _payload(batch, record)
            expected_rows.append(
                MaterializedSchedulerRow(
                    tenant_id=tenant_id,
                    physical_database_id=physical_database_id,
                    publication_ordinal=batch.publication_ordinal,
                    member_ordinal=ordinal,
                    decision=batch.decision,
                    record=record,
                )
            )
        if batch.operation in {"scheduler.decide_interval", "scheduler.resolve_interval"}:
            if (
                batch.operation == "scheduler.decide_interval"
                and batch.records[0].record_kind == "overflow-hold"
            ):
                _overflow(batch, schedules, policies, bounds, intervals, genesis, holds)
            else:
                _ordinary_interval(
                    batch,
                    schedules,
                    policies,
                    bounds,
                    intervals,
                    genesis,
                    lineages,
                    used_leases,
                    holds,
                )
        elif batch.operation in {"scheduler.acquire", "scheduler.renew", "scheduler.takeover"}:
            _lease_transition(batch, lineages, used_leases)
        elif batch.operation == "scheduler.rollover":
            _rollover(batch, lineages, known_epochs, known_lease_heads)
        else:
            if batch.operation in {"scheduler.amend_schedule", "scheduler.amend_policy"}:
                model = (
                    ScheduleRevision
                    if batch.operation == "scheduler.amend_schedule"
                    else PolicyRevision
                )
                try:
                    amended = model.model_validate_json(_payload(batch, batch.records[0]))
                except ValueError as error:
                    raise _hold(
                        batch, batch.records[0].record_id, "invalid amendment record"
                    ) from error
                if amended.schedule_id in holds:
                    raise _hold(
                        batch, amended.schedule_id, "active hold forbids schedule/policy amendment"
                    )
            _configuration(batch, schedules, policies, bounds)
            if batch.operation == "scheduler.genesis":
                original = ScheduleRevision.model_validate_json(_payload(batch, batch.records[0]))
                if original.schedule_id in genesis:
                    raise _hold(
                        batch, original.schedule_id, "duplicate registered boundary genesis"
                    )
                genesis[original.schedule_id] = (
                    DueCoordinate(
                        coordinate_policy_version=COORDINATE_VERSION,
                        canonical_coordinate=str(original.definition.start_ns),
                    ),
                    _reference(batch.records[0]),
                )
        for view in lineages.values():
            known_epochs.add(view.physical_root.current_epoch_id)
            known_lease_heads.add(
                view.lease.binding.lease_head
                if view.lease.kind == "HELD"
                else view.lease.lease_head
            )
    if tuple(expected_rows) != materialized:
        raise SchedulerStartupHold(
            tenant_id, "materialized-cut", "selected rows differ from complete physical store cut"
        )
    return SchedulerStartupIndex(
        schedules=tuple(value[0] for _, value in sorted(schedules.items())),
        policies=tuple(value[0] for _, value in sorted(policies.items())),
        bounds=tuple((key, value[0]) for key, value in sorted(bounds.items())),
        selected_record_ids=tuple(row.record.record_id for row in expected_rows),
        registered_genesis=tuple(
            (key, coordinate, reference) for key, (coordinate, reference) in sorted(genesis.items())
        ),
        lineages=tuple(value for _, value in sorted(lineages.items())),
        active_holds=tuple(value for _, value in sorted(holds.items())),
    )
