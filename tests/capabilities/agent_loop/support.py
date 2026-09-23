"""Shared scheduler test builders; simulated issuer values are not authority."""

from typing import Literal

from chiplog.capabilities.agent_loop.recovery_contracts import (
    Absent,
    ExecutionLineageBinding,
    IndividualSubject,
    LeaseBinding,
    PhysicalRootBinding,
    TrustedClockProofRef,
)
from chiplog.capabilities.agent_loop.scheduler_contracts import (
    GenesisLease,
    HeldLease,
    LeaseTransitionCommand,
    SchedulerCommandIdentity,
    SchedulerLineageView,
)
from chiplog.capabilities.agent_loop.scheduler_leases import (
    IssuedLeaseObservation,
    lease_fence_fingerprint,
    lease_payload_fingerprint,
)

DIGEST = "a" * 64


def view(generation: int | None = None) -> SchedulerLineageView:
    return SchedulerLineageView(
        lineage=ExecutionLineageBinding(
            root_id="root",
            subject=IndividualSubject(occurrence_id="occurrence"),
            root_fingerprint=DIGEST,
            lineage_head="lineage",
            initial_run_id="run",
            current_run_id="run",
            schedule_id="schedule",
            schedule_revision="revision",
            policy_revision="policy",
        ),
        physical_root=PhysicalRootBinding(
            selector_id="selector",
            selector_head="selector-head",
            selector_version=0,
            current_epoch_id="epoch",
            current_epoch_head="epoch-head",
        ),
        lease=GenesisLease(lease_head="genesis")
        if generation is None
        else HeldLease(
            binding=LeaseBinding(
                lease_head="held-head",
                holder_id="worker",
                holder_session_id="session",
                lease_id="lease",
                generation=generation,
                trusted_expiry=100,
                clock_contract_version="clock-v1",
            ),
        ),
        predecessor_rollover=Absent(),
    )


def issue(
    current: SchedulerLineageView,
    kind: Literal["ACQUIRE", "RENEW", "TAKEOVER"],
    *,
    generation: int = 1,
    now: int = 50,
    expiry: int = 120,
    lease_id: str = "fresh-lease",
    holder: str = "worker",
    session: str = "session",
    command_id: str = "command",
) -> tuple[LeaseTransitionCommand, IssuedLeaseObservation]:
    proof = TrustedClockProofRef(
        proof_id="clock-proof",
        proof_fingerprint=DIGEST,
        proof_version="1",
        clock_contract_version="clock-v1",
        fence_fingerprint=lease_fence_fingerprint(current),
        command_id=command_id,
        command_payload_fingerprint=DIGEST,
        submission_id="submission",
    )
    command = LeaseTransitionCommand(
        identity=SchedulerCommandIdentity(
            command_id=command_id,
            schema_version="1",
            canonicalization_version="1",
        ),
        kind=kind,
        lineage=current.lineage,
        physical_root=current.physical_root,
        observed_lease=current.lease,
        proposed_holder_id=holder,
        proposed_holder_session_id=session,
        proposed_lease_id=lease_id,
        proposed_expiry=expiry,
        proposed_generation=generation,
        clock_proof=proof,
    )
    proof = proof.model_copy(
        update={
            "command_payload_fingerprint": lease_payload_fingerprint(command),
        }
    )
    command = command.model_copy(update={"clock_proof": proof})
    observation = IssuedLeaseObservation(
        proof=proof,
        now=now,
        max_lease_duration=100,
        holder_id=holder,
        holder_session_id=session,
        authority_epoch="authority",
        used_lease_ids=() if current.lease.kind == "UNLEASED" else ("lease",),
    )
    return command, observation
