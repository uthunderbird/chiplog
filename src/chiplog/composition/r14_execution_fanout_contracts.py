"""Retained executable owner exchange; these values issue no publication authority."""

from typing import Literal

from pydantic import Field

from chiplog.capabilities.agent_loop.execution_fan_out_contracts import (
    ExecutionCapturedFanOutProposal,
    ExecutionCapturedFanOutRequest,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Digest, Identity, RecoveryDTO, UInt64
from chiplog.composition.r14_fanout_contracts import FanOutPhysicalMember
from chiplog.platform.broker import BrokerSession

EXECUTION_FANOUT_OPERATION = "agent_loop.execution-fanout.v2"
EXECUTION_RUN_SCHEMA = "chiplog.agent-loop.execution-record.v2"


class RetainedExecutionFanOutPreparation(RecoveryDTO):
    """Exact selected request and owner response, interpreted under their own schemas.

    The Run is exclusively proposal.sealed_run. No caller-provided replacement or
    arbitrary companions are admitted. Complete retains its captured attempt until
    the separate, fully validated CompleteAcceptance transaction.
    Session values describe the original exchange; constructing them authenticates
    neither that exchange nor the current write boundary.
    """

    kind: Literal["R14_SELECTED_EXECUTION_FANOUT_V2"] = "R14_SELECTED_EXECUTION_FANOUT_V2"
    request: ExecutionCapturedFanOutRequest
    proposal: ExecutionCapturedFanOutProposal
    expected_snapshot_fingerprint: Digest
    caller: BrokerSession
    callee: BrokerSession
    request_id: Identity
    deadline_ns: UInt64
    response_schema: Literal["chiplog.call.execution-captured-fanout-result.v2"] = (
        "chiplog.call.execution-captured-fanout-result.v2"
    )


class ExecutionFanOutPhysicalEnvelope(RecoveryDTO):
    """Complete ordered Run, seal and initialized set; the whole wire is byte-bounded.

    Fingerprint is SHA256 of canonical JSON with request_fingerprint omitted.
    Semantic verification must check exact members before invoking the sole writer;
    a matching self-fingerprint is not selection or authority.
    """

    kind: Literal["R14_EXECUTION_FANOUT_PHYSICAL_V2"] = "R14_EXECUTION_FANOUT_PHYSICAL_V2"
    tenant_id: Identity
    operation_kind: Literal["agent_loop.execution-fanout.v2"] = "agent_loop.execution-fanout.v2"
    idempotency_key: Identity
    expected_head: UInt64
    fence_generation: Literal["r6"] = "r6"
    expected_fence_frontier: Literal[0] = 0
    minimum_fence_frontier: Literal[0] = 0
    records: tuple[FanOutPhysicalMember, ...] = Field(min_length=2)
    request_fingerprint: Digest
