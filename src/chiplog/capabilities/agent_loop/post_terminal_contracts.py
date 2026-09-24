"""Original-stream work ownership: preparation only, never a publication credential.

The broker authenticates original terminal/obligation records, current selector and
issued observations. It selects all owner-produced members under one writer CAS.
Closing work neither closes an obligation nor rewrites the terminal manifest.
"""

from typing import Annotated, Literal, Protocol

from pydantic import ConfigDict, Field

from .call_acceptance_contracts import CallSubjectHead
from .execution_contracts import ExecutionRunRecord
from .recovery_contracts import (
    Absent,
    Digest,
    Identity,
    LeaseBinding,
    OriginalObligationBinding,
    Present,
    RecoveryDTO,
    RolloverPredecessor,
    TrustedClockProofRef,
    UInt64,
    WorkEpochBinding,
    WorkEpochRolloverFence,
    WorkSubjectBinding,
)
from .scheduler_leases import IssuedLeaseObservation
from .scheduler_rollover import IssuedRolloverObservation


class UnleasedWork(RecoveryDTO):
    kind: Literal["UNLEASED"] = "UNLEASED"
    lease_head: Identity
    generation: int = Field(default=0, strict=True, ge=0, le=0)


class ClaimedWork(RecoveryDTO):
    kind: Literal["CLAIMED"] = "CLAIMED"
    binding: LeaseBinding


class ExhaustedWork(RecoveryDTO):
    kind: Literal["GENERATION_EXHAUSTED_HOLD"] = "GENERATION_EXHAUSTED_HOLD"
    lease_head: Identity
    generation: int = Field(
        default=18446744073709551615,
        strict=True,
        ge=18446744073709551615,
        le=18446744073709551615,
    )
    exhausted_command_id: Identity
    trusted_expiry: UInt64
    authority_epoch: Identity
    preceding_claimed_lease: LeaseBinding


class ClosedWork(RecoveryDTO):
    kind: Literal["CLOSED"] = "CLOSED"
    exact_obligation_terminal_head: Present


PostTerminalWorkLeaseState = Annotated[
    UnleasedWork | ClaimedWork | ExhaustedWork | ClosedWork, Field(discriminator="kind")
]


class PostTerminalWorkView(RecoveryDTO):
    subject: WorkSubjectBinding
    work_epoch: WorkEpochBinding
    work_state_head: Present
    lease: PostTerminalWorkLeaseState
    predecessor_rollover: Annotated[Absent | RolloverPredecessor, Field(discriminator="kind")]


class WorkCommandIdentity(RecoveryDTO):
    tenant_id: Identity
    command_id: Identity
    canonicalization_version: Literal["chiplog.post-terminal-work.v1"] = (
        "chiplog.post-terminal-work.v1"
    )


class PrepareTerminalWork(RecoveryDTO):
    """Companions of the same terminal batch; input Run is not already selected.

    The owner verifies the proposed terminal Run against its original terminalization
    request before deriving the exact complete set of work subjects and genesis epochs.
    The composition must retain that original owner exchange, not just this proposal.
    """

    kind: Literal["PREPARE_TERMINAL_WORK_V1"] = "PREPARE_TERMINAL_WORK_V1"
    identity: WorkCommandIdentity
    terminal_run: ExecutionRunRecord
    original_terminalization_request: bytes = Field(min_length=1)
    terminal_manifest: CallSubjectHead
    ordered_open_obligations: tuple[OriginalObligationBinding, ...]
    model_config = ConfigDict(ser_json_bytes="base64", val_json_bytes="base64")


class PrepareWorkLease(RecoveryDTO):
    kind: Literal["PREPARE_WORK_LEASE_V1"] = "PREPARE_WORK_LEASE_V1"
    identity: WorkCommandIdentity
    operation: Literal["CLAIM", "RENEW", "TAKEOVER"]
    expected: PostTerminalWorkView
    current_original_obligation: Present
    current_original_evidence: Annotated[Absent | Present, Field(discriminator="kind")]
    proposed_holder_id: Identity
    proposed_holder_session_id: Identity
    proposed_lease_id: Identity
    proposed_generation: UInt64
    proposed_expiry: UInt64
    clock_proof: TrustedClockProofRef
    issued: IssuedLeaseObservation
    submission_id: Identity


class PrepareWorkRollover(RecoveryDTO):
    kind: Literal["PREPARE_WORK_ROLLOVER_V1"] = "PREPARE_WORK_ROLLOVER_V1"
    identity: WorkCommandIdentity
    expected: PostTerminalWorkView
    fence: WorkEpochRolloverFence
    current_original_obligation: Present
    current_original_evidence: Annotated[Absent | Present, Field(discriminator="kind")]
    issued: IssuedRolloverObservation


class PrepareWorkClose(RecoveryDTO):
    """Observed original resolver closure, not a worker request to invent closure."""

    kind: Literal["PREPARE_WORK_CLOSE_V1"] = "PREPARE_WORK_CLOSE_V1"
    identity: WorkCommandIdentity
    expected: PostTerminalWorkView
    exact_obligation_terminal_head: Present
    original_resolver_batch: Present


PostTerminalWorkRequest = Annotated[
    PrepareTerminalWork | PrepareWorkLease | PrepareWorkRollover | PrepareWorkClose,
    Field(discriminator="kind"),
]


class WorkCanonicalMember(RecoveryDTO):
    """Exact owner output, interpreted under the matching registered record schema.

    Fingerprints and refs never authenticate their own bytes. The future pure verifier
    must decode every complete member, verify its kind/schema/identity and graph, and
    the broker must compare the unchanged output with the retained owner response.
    """

    model_config = ConfigDict(ser_json_bytes="base64", val_json_bytes="base64")
    record_kind: Literal["SUBJECT", "EPOCH", "SELECTOR", "LEASE", "ROLLOVER", "EDGE"]
    record_id: Identity
    schema_id: Identity
    canonical_record_bytes: bytes = Field(min_length=1)
    fingerprint: Digest


class PreparedPostTerminalWork(RecoveryDTO):
    kind: Literal["PREPARED_POST_TERMINAL_WORK_V1"] = "PREPARED_POST_TERMINAL_WORK_V1"
    source_request_fingerprint: Digest
    ordered_work: tuple[PostTerminalWorkView, ...]
    complete_records: tuple[WorkCanonicalMember, ...]
    complete_commitment: Digest


class WorkPreparationRejected(RecoveryDTO):
    kind: Literal["POST_TERMINAL_WORK_REJECTED_V1"] = "POST_TERMINAL_WORK_REJECTED_V1"
    code: Literal[
        "STALE", "CONFLICT", "DENIED", "CLOCK_UNVERIFIABLE", "GENERATION_EXHAUSTED", "FAULT"
    ]
    reason: Identity


PostTerminalWorkResult = Annotated[
    PreparedPostTerminalWork | WorkPreparationRejected, Field(discriminator="kind")
]


class PostTerminalWorkPreparationPort(Protocol):
    async def prepare_work(self, request: PostTerminalWorkRequest) -> PostTerminalWorkResult: ...
