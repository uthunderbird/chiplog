"""Execution-call cancellation wire; constructed values supply no authority."""

import hashlib
import json
from typing import Literal, Protocol

from pydantic import Field

from chiplog.capabilities.agent_loop.call_acceptance_contracts import (
    CallSubjectHead,
    CancelBeforeAcceptRequest,
    PreparedPreAcceptCancellation,
)
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.capabilities.agent_loop.recovery_contracts import Digest, Identity, RecoveryDTO, UInt64
from chiplog.composition.r14_cancellation_contracts import (
    CancelCallSubmission,
    RetainedCancellationAct,
    RetainedCancellationTrust,
)
from chiplog.composition.r14_fanout_contracts import FanOutPhysicalMember
from chiplog.platform.broker import PublicPortCall, PublicPortSuccess

EXECUTION_CANCELLATION_OPERATION: Literal["agent_loop.cancel-execution-before-accept.v1"] = (
    "agent_loop.cancel-execution-before-accept.v1"
)
MAX_EXECUTION_CANCELLATION_BYTES = 1_048_576


class ExecutionCancelledCallReceipt(RecoveryDTO):
    """References to one selected terminal/result pair, never proof from construction.

    Publication identity and fingerprint refer to the original complete selected
    request, including retained act/preparation and ordered physical records. Replay
    returns this original receipt after Run advancement; it does not renew authority
    or assert Run termination. NOT_EXECUTED applies only to this unaccepted call.
    """

    kind: Literal["EXECUTION_CANCELLED_CALL_RECEIPT_V1"] = "EXECUTION_CANCELLED_CALL_RECEIPT_V1"
    original_call_id: Identity
    initialized: CallSubjectHead
    terminal: CallSubjectHead
    result: CallSubjectHead
    publication_id: Identity
    publication_fingerprint: Digest


class RetainedExecutionCancellationPreparation(RecoveryDTO):
    """Original owner/trust exchange and Run cut; no reconstructed Run companion.

    The owner request cut contains complete preceding inventory and the exact
    materialization commitment. Historical interpretation uses these original bytes,
    while fresh publication independently proves current authority at the writer.
    """

    kind: Literal["R14_SELECTED_EXECUTION_CANCELLATION_V1"] = (
        "R14_SELECTED_EXECUTION_CANCELLATION_V1"
    )
    act: RetainedCancellationAct
    trust: RetainedCancellationTrust
    request: CancelBeforeAcceptRequest
    proposal: PreparedPreAcceptCancellation
    run_predecessor: ExecutionRunRecord
    expected_snapshot_fingerprint: Digest
    owner_request: PublicPortCall
    owner_response: PublicPortSuccess


class RetainedExecutionCancellationPreparationV2(RetainedExecutionCancellationPreparation):
    """V4 Prepare cancellation, retaining full trust outside the physical cut.

    The ACTOR source carries an exact commitment to that original full exchange.
    Historical traversal must match original_prepare_fingerprint to its verified
    selected V4 Prepare. V1 source bytes and its physical bound remain unchanged.
    """

    kind: Literal["R14_SELECTED_EXECUTION_CANCELLATION_V2"] = (
        "R14_SELECTED_EXECUTION_CANCELLATION_V2"  # type: ignore[assignment]
    )
    original_prepare_fingerprint: Digest


class ExecutionCancellationTrustCommitmentV2(RecoveryDTO):
    """Compact source preimage; never authenticates trust by construction."""

    kind: Literal["R14_EXECUTION_CANCELLATION_TRUST_COMMITMENT_V2"] = (
        "R14_EXECUTION_CANCELLATION_TRUST_COMMITMENT_V2"
    )
    retained_trust_fingerprint: Digest
    authenticated_reference_fingerprint: Digest
    journal_head: Identity


def cancellation_trust_commitment(
    trust: RetainedCancellationTrust,
) -> ExecutionCancellationTrustCommitmentV2:
    return ExecutionCancellationTrustCommitmentV2(
        retained_trust_fingerprint=trust.digest(),
        authenticated_reference_fingerprint=hashlib.sha256(
            trust.authenticated_reference_bytes
        ).hexdigest(),
        journal_head=trust.journal_head,
    )


def decode_execution_cancellation_preparation(
    raw: str | bytes,
) -> RetainedExecutionCancellationPreparation:
    kind = json.loads(raw).get("kind")
    if kind == "R14_SELECTED_EXECUTION_CANCELLATION_V1":
        return RetainedExecutionCancellationPreparation.model_validate_json(raw)
    if kind == "R14_SELECTED_EXECUTION_CANCELLATION_V2":
        return RetainedExecutionCancellationPreparationV2.model_validate_json(raw)
    raise ValueError("unregistered retained execution cancellation")


class ExecutionCancellationPhysicalEnvelope(RecoveryDTO):
    """Exactly ordered owner terminal then NOT_EXECUTED result; no Run transition.

    Request fingerprint is SHA256 of canonical JSON excluding request_fingerprint.
    The serialized bound includes all encoding and retained preparation binding.
    Shape cardinality does not verify member identity, semantics or journal selection.
    """

    kind: Literal["R14_EXECUTION_CANCELLATION_PHYSICAL_V1"] = (
        "R14_EXECUTION_CANCELLATION_PHYSICAL_V1"
    )
    tenant_id: Identity
    operation_kind: Literal["agent_loop.cancel-execution-before-accept.v1"] = (
        EXECUTION_CANCELLATION_OPERATION
    )
    idempotency_key: Identity
    expected_head: UInt64
    fence_generation: Literal["r6"] = "r6"
    expected_fence_frontier: Literal[0] = 0
    minimum_fence_frontier: Literal[0] = 0
    retained_preparation_fingerprint: Digest
    records: tuple[FanOutPhysicalMember, ...] = Field(min_length=2, max_length=2)
    request_fingerprint: Digest


class ExecutionCallCancellationPort(Protocol):
    async def cancel_execution_call(
        self, peer: str, submission: CancelCallSubmission
    ) -> ExecutionCancelledCallReceipt:
        """Authenticate caller and return the exact original selected receipt.

        Fresh selection competes with acceptance on the same initialized branch and
        exact current Run/inventory cut. Identical replay authenticates the current
        caller and compares original submission bytes, then recovers original records
        without refreshing the act or invoking owners to regenerate history.
        Denied/stale/conflicting requests raise LoopRejected. Corrupt selected history
        raises a typed chained integrity error identifying operation/tenant/record.
        Durable publication uncertainty is recoverable; it never proves that no
        cancellation was selected. Legacy cancel_call retains its RunRecord return.
        """
        ...
