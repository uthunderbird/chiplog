"""Pure physical-epoch lease transitions; issuance and atomic CAS are broker duties."""

from __future__ import annotations

import hashlib
import json

from pydantic import Field

from chiplog.capabilities.agent_loop.recovery_contracts import (
    Digest,
    Identity,
    LeaseBinding,
    RecoveryDTO,
    TrustedClockProofRef,
    UInt64,
)
from chiplog.capabilities.agent_loop.scheduler_contracts import (
    ExecutionRootLeaseState,
    ExhaustedLease,
    HeldLease,
    LeaseTransitionCommand,
    SchedulerLineageView,
)

UINT64_MAX = 2**64 - 1


class LeaseRuleViolation(ValueError):
    def __init__(self, code: str, reason: str) -> None:
        self.code = code
        super().__init__(reason)


class IssuedLeaseObservation(RecoveryDTO):
    """Broker-reproduced proof record and current authenticated session observation.

    Public construction authenticates nothing. The caller at the registered writer
    cut must reproduce all fields from trusted issuance/session/history records.
    No boolean can substitute for the complete exact issued proof reference.
    """

    proof: TrustedClockProofRef
    now: UInt64
    max_lease_duration: int = Field(gt=0, le=UINT64_MAX)
    holder_id: Identity
    holder_session_id: Identity
    authority_epoch: Identity
    used_lease_ids: tuple[Identity, ...]


class LeaseCandidate(RecoveryDTO):
    """Unpublished owner result; no claim of acquired or renewed authority."""

    transition_id: Identity
    command_fingerprint: Digest
    observed_fence_fingerprint: Digest
    state: ExecutionRootLeaseState


def _digest(domain: str, value: object) -> str:
    payload = json.dumps([domain, value], sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def lease_fence_fingerprint(view: SchedulerLineageView) -> str:
    return _digest(
        "scheduler-lease-fence-v1",
        {
            "lineage": view.lineage.model_dump(mode="json"),
            "physical_root": view.physical_root.model_dump(mode="json"),
            "lease": view.lease.model_dump(mode="json"),
        },
    )


def lease_payload_fingerprint(command: LeaseTransitionCommand) -> str:
    return _digest(
        "scheduler-lease-transition-payload-v1",
        command.model_dump(mode="json", exclude={"clock_proof"}),
    )


def evaluate_lease(
    current: SchedulerLineageView,
    command: LeaseTransitionCommand,
    issued: IssuedLeaseObservation | None,
    *,
    submission_id: str,
) -> LeaseCandidate:
    """Validate exact immutable inputs and derive one candidate, without publishing."""
    if command.kind not in ("ACQUIRE", "RENEW", "TAKEOVER"):
        raise LeaseRuleViolation("INTEGRITY_HOLD", "unknown lease transition")
    if (
        command.lineage != current.lineage
        or command.physical_root != current.physical_root
        or command.observed_lease != current.lease
    ):
        raise LeaseRuleViolation("STALE_HEAD", "lineage, selector, epoch or lease changed")
    proof = command.clock_proof
    if (
        issued is None
        or issued.proof != proof
        or proof.command_id != command.identity.command_id
        or proof.command_payload_fingerprint != lease_payload_fingerprint(command)
        or proof.fence_fingerprint != lease_fence_fingerprint(current)
        or proof.submission_id != submission_id
    ):
        raise LeaseRuleViolation(
            "CLOCK_UNVERIFIABLE",
            "exact issued command/fence/cut proof absent",
        )
    if (
        command.proposed_holder_id != issued.holder_id
        or command.proposed_holder_session_id != issued.holder_session_id
    ):
        raise LeaseRuleViolation(
            "AUTHORITY_DENIED",
            "proposed holder/session was not authenticated",
        )
    if len(set(issued.used_lease_ids)) != len(issued.used_lease_ids):
        raise LeaseRuleViolation(
            "INTEGRITY_HOLD",
            "duplicate lease identity in authoritative history",
        )
    if not issued.now < command.proposed_expiry <= issued.now + issued.max_lease_duration:
        raise LeaseRuleViolation(
            "LEASE_NOT_LIVE",
            "proposed expiry outside registered future horizon",
        )
    observed = current.lease
    if observed.kind == "GENERATION_EXHAUSTED_HOLD":
        raise LeaseRuleViolation(
            "GENERATION_EXHAUSTED",
            "only physical rollover can leave exhaustion",
        )
    if observed.kind == "HELD":
        lease = observed.binding
        if lease.generation == 0 or lease.lease_id not in issued.used_lease_ids:
            raise LeaseRuleViolation("INTEGRITY_HOLD", "held lease absent from generation/history")
        if lease.clock_contract_version != proof.clock_contract_version:
            raise LeaseRuleViolation("CLOCK_UNVERIFIABLE", "clock contract differs from held epoch")
    command_fingerprint = _digest(
        "scheduler-lease-transition-v1",
        command.model_dump(mode="json"),
    )
    head = "scheduler-lease-head-v1:" + command_fingerprint
    new_state: ExecutionRootLeaseState
    if command.kind == "ACQUIRE":
        if observed.kind != "UNLEASED" or command.proposed_generation != 1:
            raise LeaseRuleViolation(
                "STALE_HEAD",
                "acquire requires exact genesis and generation one",
            )
        if command.proposed_lease_id in issued.used_lease_ids:
            raise LeaseRuleViolation("STALE_HEAD", "acquire lease identity is not fresh")
        new_state = HeldLease(
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
        if observed.kind != "HELD":
            raise LeaseRuleViolation("STALE_HEAD", "renew requires exact held lease")
        old = observed.binding
        if (
            issued.now >= old.trusted_expiry
            or issued.holder_id != old.holder_id
            or issued.holder_session_id != old.holder_session_id
            or command.proposed_lease_id != old.lease_id
            or command.proposed_generation != old.generation
            or command.proposed_expiry <= old.trusted_expiry
        ):
            raise LeaseRuleViolation("LEASE_NOT_LIVE", "renew holder/id/generation/expiry mismatch")
        new_state = HeldLease(
            binding=old.model_copy(
                update={
                    "lease_head": head,
                    "trusted_expiry": command.proposed_expiry,
                }
            )
        )
    else:
        if observed.kind != "HELD" or issued.now < observed.binding.trusted_expiry:
            raise LeaseRuleViolation("LEASE_NOT_LIVE", "takeover lacks positive expiry proof")
        old = observed.binding
        if command.proposed_lease_id in issued.used_lease_ids:
            raise LeaseRuleViolation("STALE_HEAD", "takeover lease identity is not fresh")
        if old.generation == UINT64_MAX:
            if command.proposed_generation != UINT64_MAX:
                raise LeaseRuleViolation(
                    "GENERATION_EXHAUSTED",
                    "exhaustion cannot reset generation",
                )
            new_state = ExhaustedLease(
                lease_head=head,
                exhausted_command_id=command.identity.command_id,
                trusted_expiry=old.trusted_expiry,
                authority_epoch=issued.authority_epoch,
                preceding_held_lease=old,
            )
        else:
            if command.proposed_generation != old.generation + 1:
                raise LeaseRuleViolation(
                    "STALE_HEAD",
                    "takeover must advance generation exactly once",
                )
            new_state = HeldLease(
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
    return LeaseCandidate(
        transition_id="scheduler-lease-transition-v1:" + command_fingerprint,
        command_fingerprint=command_fingerprint,
        observed_fence_fingerprint=lease_fence_fingerprint(current),
        state=new_state,
    )
