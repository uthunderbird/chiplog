"""Pure complete physical-epoch rollover proposals, without publication authority."""

from __future__ import annotations

import base64
import hashlib
import json

from .recovery_contracts import (
    Digest,
    Identity,
    PhysicalRootBinding,
    Present,
    RecoveryDTO,
    RolloverAuthorityRef,
    RolloverPredecessor,
)
from .scheduler_contracts import (
    GenesisLease,
    PhysicalRootRolloverCommand,
    SchedulerLineageView,
)
from .scheduler_domain import SchedulerDomainError
from .scheduler_materialization import SchedulerCanonicalMember

UINT64_MAX = 2**64 - 1


def _bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _digest(value: object) -> str:
    return hashlib.sha256(_bytes(value)).hexdigest()


def rollover_payload_fingerprint(command: PhysicalRootRolloverCommand) -> str:
    """Retain authority and predecessor semantics; omit only proof self-slots."""
    data = command.model_dump(mode="json")
    authority = data["fence"]["authority"]
    for field in ("proof_id", "proof_fingerprint", "command_payload_fingerprint"):
        del authority[field]
    return _digest(["chiplog.scheduler.rollover-payload.v1", data])


def rollover_snapshot_fingerprint(current: SchedulerLineageView) -> str:
    return hashlib.sha256(current.canonical_bytes()).hexdigest()


class IssuedRolloverObservation(RecoveryDTO):
    """Exact broker-issued proof and reproduced history; construction grants nothing."""

    authority: RolloverAuthorityRef
    snapshot_fingerprint: Digest
    submission_id: Identity
    authority_epoch: Identity
    used_epoch_ids: tuple[Identity, ...]
    used_lease_heads: tuple[Identity, ...]


class RolloverCandidate(RecoveryDTO):
    command_fingerprint: Digest
    decision: Present
    edge: Present
    view: SchedulerLineageView
    records: tuple[SchedulerCanonicalMember, ...]
    complete_commitment: Digest


def _record(kind: str, identity: str, value: object) -> SchedulerCanonicalMember:
    raw = _bytes(value)
    return SchedulerCanonicalMember(
        record_kind="scheduler." + kind,
        record_id=identity,
        schema_id="chiplog.scheduler." + kind + ".v1",
        canonical_base64=base64.b64encode(raw).decode(),
        fingerprint=hashlib.sha256(raw).hexdigest(),
    )


def _ref(record: SchedulerCanonicalMember) -> Present:
    return Present(head=record.record_id, fingerprint=record.fingerprint)


def prepare_rollover(
    current: SchedulerLineageView,
    command: PhysicalRootRolloverCommand,
    issued: IssuedRolloverObservation | None,
    *,
    submission_id: str,
) -> RolloverCandidate:
    """One proposal for exact exhausted snapshot; writer owns re-read/CAS/replay."""
    lease = current.lease
    fence = command.fence
    if (
        lease.kind != "GENERATION_EXHAUSTED_HOLD"
        or fence.lineage != current.lineage
        or fence.physical_root != current.physical_root
    ):
        raise SchedulerDomainError("rollover requires exact exhausted lineage/physical snapshot")
    exhaustion = fence.exhaustion
    if (
        exhaustion.hold_head != lease.lease_head
        or exhaustion.exhausted_command_id != lease.exhausted_command_id
        or exhaustion.lease != lease.preceding_held_lease
        or exhaustion.authority_epoch != lease.authority_epoch
        or lease.generation != UINT64_MAX
        or lease.preceding_held_lease.generation != UINT64_MAX
        or lease.trusted_expiry != lease.preceding_held_lease.trusted_expiry
    ):
        raise SchedulerDomainError("exhaustion tuple differs from exact hold")
    authority = fence.authority
    if (
        issued is None
        or issued.authority != authority
        or issued.snapshot_fingerprint != rollover_snapshot_fingerprint(current)
        or issued.submission_id != submission_id
        or issued.authority_epoch != lease.authority_epoch
        or authority.command_id != command.identity.command_id
        or authority.command_payload_fingerprint != rollover_payload_fingerprint(command)
        or authority.predecessor_rollover != current.predecessor_rollover
    ):
        raise SchedulerDomainError("rollover authority proof/snapshot/chain/submission differs")
    physical = current.physical_root
    if physical.selector_version == UINT64_MAX:
        raise SchedulerDomainError("physical selector version exhausted")
    if (
        len(set(issued.used_epoch_ids)) != len(issued.used_epoch_ids)
        or len(set(issued.used_lease_heads)) != len(issued.used_lease_heads)
        or physical.current_epoch_id not in issued.used_epoch_ids
        or lease.lease_head not in issued.used_lease_heads
        or lease.preceding_held_lease.lease_head not in issued.used_lease_heads
    ):
        raise SchedulerDomainError("incomplete or duplicate epoch/lease history")

    # Primitive contains no derived epoch/genesis/edge/selector or batch hash slots.
    primitive = {
        "schema": "chiplog.scheduler.rollover-primitive.v1",
        "command": command.model_dump(mode="json"),
        "current": current.model_dump(mode="json"),
    }
    primitive_fingerprint = _digest(primitive)
    decision_id = "scheduler-rollover-v1:" + primitive_fingerprint
    edge_id = decision_id + "/edge"
    epoch_id = "scheduler-physical-epoch-v1:" + _digest([primitive_fingerprint, "epoch"])
    genesis_head = "scheduler-genesis-v1:" + _digest([primitive_fingerprint, "UNLEASED", 0])
    if epoch_id in issued.used_epoch_ids or genesis_head in issued.used_lease_heads:
        raise SchedulerDomainError("rollover epoch or genesis aliases authoritative history")
    decision = _record(
        "rollover-decision",
        decision_id,
        {
            "primitive": primitive,
            "primitive_fingerprint": primitive_fingerprint,
            "successor_epoch_id": epoch_id,
            "genesis_lease_head": genesis_head,
            "reciprocal_edge_id": edge_id,
        },
    )
    decision_ref = _ref(decision)
    common = {
        "lineage": current.lineage.model_dump(mode="json"),
        "decision": decision_ref.model_dump(mode="json"),
    }
    old = _record(
        "physical-epoch-terminal",
        decision_id + "/old-terminal",
        {
            **common,
            "old_physical_root": physical.model_dump(mode="json"),
            "exhaustion": lease.model_dump(mode="json"),
            "state": "TERMINALLY_FENCED",
            "successor_epoch_id": epoch_id,
            "edge_id": edge_id,
        },
    )
    genesis = GenesisLease(lease_head=genesis_head)
    new_epoch = _record(
        "rollover-physical-root",
        epoch_id,
        {
            **common,
            "epoch_id": epoch_id,
            "predecessor_epoch_id": physical.current_epoch_id,
            "genesis": genesis.model_dump(mode="json"),
            "edge_id": edge_id,
        },
    )
    genesis_record = _record(
        "rollover-root-lease-genesis",
        genesis_head,
        {
            **common,
            "epoch": _ref(new_epoch).model_dump(mode="json"),
            "lease": genesis.model_dump(mode="json"),
        },
    )
    edge = _record(
        "rollover-edge",
        edge_id,
        {
            **common,
            "old_epoch_id": physical.current_epoch_id,
            "old_terminal": _ref(old).model_dump(mode="json"),
            "new_epoch": _ref(new_epoch).model_dump(mode="json"),
            "genesis": _ref(genesis_record).model_dump(mode="json"),
            "predecessor_rollover": current.predecessor_rollover.model_dump(mode="json"),
        },
    )
    pair = RolloverPredecessor(decision=decision_ref, edge=_ref(edge))
    selector = _record(
        "rollover-physical-selector",
        decision_id + "/selector",
        {
            **common,
            "selector_id": physical.selector_id,
            "previous": physical.model_dump(mode="json"),
            "selector_version": physical.selector_version + 1,
            "selected_epoch": _ref(new_epoch).model_dump(mode="json"),
            "rollover": pair.model_dump(mode="json"),
        },
    )
    next_view = SchedulerLineageView(
        lineage=current.lineage,
        physical_root=PhysicalRootBinding(
            selector_id=physical.selector_id,
            selector_head=selector.record_id,
            selector_version=physical.selector_version + 1,
            current_epoch_id=epoch_id,
            current_epoch_head=new_epoch.record_id,
        ),
        lease=genesis,
        predecessor_rollover=pair,
    )
    records = (decision, old, new_epoch, genesis_record, edge, selector)
    return RolloverCandidate(
        command_fingerprint=hashlib.sha256(command.canonical_bytes()).hexdigest(),
        decision=decision_ref,
        edge=_ref(edge),
        view=next_view,
        records=records,
        complete_commitment=_digest([record.model_dump(mode="json") for record in records]),
    )
