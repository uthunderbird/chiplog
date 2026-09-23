"""Cancellation ingress and retained evidence; constructing values grants no authority.

The public submission contains requested identities only. Composition privately
authenticates its actual invocation and retains the original exchange at selection.
Historical evidence is interpreted under its original version, never refreshed.
"""

from typing import Literal, Protocol

from pydantic import ConfigDict, Field

from chiplog.capabilities.agent_loop.call_acceptance_contracts import (
    CallSubjectHead,
    CancelBeforeAcceptRequest,
    PreparedPreAcceptCancellation,
)
from chiplog.capabilities.agent_loop.contracts import RunRecord
from chiplog.capabilities.agent_loop.recovery_contracts import Digest, Identity, RecoveryDTO, UInt64
from chiplog.composition.r14_fanout_contracts import FanOutPhysicalMember
from chiplog.platform.broker import PublicPortCall, PublicPortSuccess

CANCELLATION_OPERATION: Literal["agent_loop.cancel-before-accept.v1"] = (
    "agent_loop.cancel-before-accept.v1"
)
CANCELLATION_SCHEMA = "chiplog.call.cancelled-before-accept.v1"
NOT_EXECUTED_SCHEMA = "chiplog.call.not-executed-result.v1"
MAX_CANCELLATION_BYTES = 1_048_576


class CancelCallSubmission(RecoveryDTO):
    """A requested action, not an authenticated act or an observed current head."""

    kind: Literal["CANCEL_CALL_SUBMISSION_V1"] = "CANCEL_CALL_SUBMISSION_V1"
    act_id: Identity
    original_call_id: Identity
    initialized: CallSubjectHead
    current_run: CallSubjectHead


class HermeticCancellationPolicy(RecoveryDTO):
    """Closed interpretation for explicit per-call acts in the offline assembly."""

    kind: Literal["HERMETIC_CALL_CANCELLATION_POLICY_V1"] = "HERMETIC_CALL_CANCELLATION_POLICY_V1"
    tenant_id: Literal["hermetic-tenant"] = "hermetic-tenant"
    principal_id: Literal["hermetic-principal"] = "hermetic-principal"
    ingress: Literal["hermetic-ingress"] = "hermetic-ingress"
    scope: Literal["OWN_ACTIVE_NON_SCHEDULER_INITIALIZED_CALL"] = (
        "OWN_ACTIVE_NON_SCHEDULER_INITIALIZED_CALL"
    )
    permits_run_termination: Literal[False] = False


class RetainedCancellationTrust(RecoveryDTO):
    """Exact original source and IPC bytes; no historical filesystem re-observation."""

    model_config = ConfigDict(ser_json_bytes="base64", val_json_bytes="base64")

    snapshot_bytes: bytes = Field(min_length=1)
    journal_head: Identity
    bundle_path: Identity
    sources: tuple[tuple[Identity, UInt64, UInt64], ...] = Field(min_length=1)
    request: PublicPortCall
    response: PublicPortSuccess
    authenticated_reference_bytes: bytes = Field(min_length=1)


class RetainedCancellationAct(RecoveryDTO):
    """Canonical act preimage retained inside the independently selected journal."""

    model_config = ConfigDict(ser_json_bytes="base64", val_json_bytes="base64")

    kind: Literal["AUTHENTICATED_CALL_CANCELLATION_ACT_V1"] = (
        "AUTHENTICATED_CALL_CANCELLATION_ACT_V1"
    )
    submission: CancelCallSubmission
    policy: HermeticCancellationPolicy
    authenticated_reference_bytes: bytes = Field(min_length=1)
    trust_evidence_fingerprint: Digest


class RetainedCancellationPreparation(RecoveryDTO):
    kind: Literal["R14_SELECTED_CANCELLATION_PREPARATION_V1"] = (
        "R14_SELECTED_CANCELLATION_PREPARATION_V1"
    )
    act: RetainedCancellationAct
    trust: RetainedCancellationTrust
    request: CancelBeforeAcceptRequest
    proposal: PreparedPreAcceptCancellation
    run_predecessor: RunRecord
    run_companion: RunRecord
    expected_snapshot_fingerprint: Digest
    owner_request: PublicPortCall
    owner_response: PublicPortSuccess


class CancellationPhysicalEnvelope(RecoveryDTO):
    """Complete ordered batch: Run companion, cancellation terminal, result.

    The request fingerprint hashes this canonical body excluding that field.
    The byte limit covers the entire envelope, including base64 and fingerprint.
    """

    kind: Literal["R14_CANCELLATION_PHYSICAL_V1"] = "R14_CANCELLATION_PHYSICAL_V1"
    tenant_id: Identity
    operation_kind: Literal["agent_loop.cancel-before-accept.v1"] = CANCELLATION_OPERATION
    idempotency_key: Identity
    expected_head: UInt64
    fence_generation: Literal["r6"] = "r6"
    expected_fence_frontier: Literal[0] = 0
    minimum_fence_frontier: Literal[0] = 0
    retained_preparation_fingerprint: Digest
    records: tuple[FanOutPhysicalMember, ...] = Field(min_length=3, max_length=3)
    request_fingerprint: Digest


class CallCancellationPort(Protocol):
    async def cancel_call(self, peer: str, submission: CancelCallSubmission) -> RunRecord:
        """Return the exact committed companion; reject stale/conflicting requests.

        A replay returns the original companion, not the current Run snapshot.
        LoopRejected reports denied, stale, unsupported or malformed ingress.
        Pending durable selection remains an explicit recoverable failure.
        """
        ...
