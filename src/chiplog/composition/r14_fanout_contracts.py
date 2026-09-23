"""Broker-private fanout selection values; construction grants no publication authority."""

from typing import Literal

from pydantic import Field

from chiplog.capabilities.agent_loop.contracts import DurableCompanion, RunRecord
from chiplog.capabilities.agent_loop.fan_out_contracts import (
    CapturedFanOutProposal,
    CapturedFanOutRequest,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Digest, Identity, RecoveryDTO, UInt64
from chiplog.platform.broker import BrokerSession

FANOUT_OPERATION = "agent_loop.fanout.v1"
SEAL_SCHEMA = "chiplog.call.response-seal.v1"
INITIALIZED_SCHEMA = "chiplog.call.initialized.v1"


class FanOutPhysicalMember(RecoveryDTO):
    record_id: Identity
    owner: Identity
    schema_id: Identity
    canonical_payload_base64: Identity
    fingerprint: Digest


class FanOutPhysicalEnvelope(RecoveryDTO):
    """Byte bound counts this entire wire, including its fingerprint and base64 payloads.

    Fingerprint preimage is this canonical object with request_fingerprint omitted;
    tagged lexical canonicalization and member order otherwise remain identical.
    """

    kind: Literal["R14_FANOUT_PHYSICAL_V1"] = "R14_FANOUT_PHYSICAL_V1"
    tenant_id: Identity
    operation_kind: Literal["agent_loop.fanout.v1"] = "agent_loop.fanout.v1"
    idempotency_key: Identity
    expected_head: UInt64
    fence_generation: Literal["r6"] = "r6"
    expected_fence_frontier: Literal[0] = 0
    minimum_fence_frontier: Literal[0] = 0
    records: tuple[FanOutPhysicalMember, ...] = Field(min_length=2)
    request_fingerprint: Digest


class RetainedFanOutPreparation(RecoveryDTO):
    """Original canonical owner exchange and Run inputs retained at journal selection.

    Nested values use their own canonical_bytes for fingerprints. Physical envelope
    omits this evidence: it is retained in the independent journal, not a SQL record.
    """

    kind: Literal["R14_SELECTED_FANOUT_PREPARATION_V1"] = "R14_SELECTED_FANOUT_PREPARATION_V1"
    request: CapturedFanOutRequest
    proposal: CapturedFanOutProposal
    accepted_run: RunRecord
    companions: tuple[DurableCompanion, ...]
    expected_snapshot_fingerprint: Digest
    caller: BrokerSession
    callee: BrokerSession
    request_id: Identity
    deadline_ns: UInt64
    response_schema: Literal["chiplog.call.captured-fanout-result.v1"] = (
        "chiplog.call.captured-fanout-result.v1"
    )
