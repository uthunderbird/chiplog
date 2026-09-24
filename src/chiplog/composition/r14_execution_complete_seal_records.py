"""Versioned durable companion for the first native zero-call Complete seal."""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Literal

from chiplog.adapters.driven.loop_sqlite import OWNER
from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.capabilities.agent_loop.recovery_contracts import Digest, Identity, RecoveryDTO, UInt64
from chiplog.capabilities.agent_loop.recovery_frontier_registry_contracts import (
    RECOVERY_FRONTIER_REGISTRY_SCHEMA,
    decode_frontier_registry,
    execution_zero_call_frontier_registry,
    frontier_registry_reference,
)
from chiplog.composition.r14_execution_fanout_contracts import (
    EXECUTION_RUN_SCHEMA,
    RetainedExecutionFanOutPreparation,
)
from chiplog.composition.r14_execution_fanout_records import build_envelope
from chiplog.composition.r14_fanout_contracts import SEAL_SCHEMA, FanOutPhysicalMember
from chiplog.platform._sqlite import PhysicalPublicationCommand, PhysicalRecord

EXECUTION_COMPLETE_SEAL_OPERATION = "agent_loop.execution-complete-seal.v1"


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def _decode(encoded: str) -> bytes:
    raw = base64.b64decode(encoded, validate=True)
    _require(base64.b64encode(raw).decode() == encoded, "noncanonical payload base64")
    return raw


def _without_fingerprint(value: RecoveryDTO) -> str:
    wire = json.loads(value.canonical_bytes())
    del wire["request_fingerprint"]
    return hashlib.sha256(
        json.dumps(wire, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def _member(identity: str, schema: str, raw: bytes) -> FanOutPhysicalMember:
    return FanOutPhysicalMember(
        record_id=identity,
        owner=OWNER,
        schema_id=schema,
        canonical_payload_base64=base64.b64encode(raw).decode(),
        fingerprint=hashlib.sha256(raw).hexdigest(),
    )


class RetainedExecutionCompleteSeal(RecoveryDTO):
    """Exact old owner exchange plus the fixed profile selected with it."""

    kind: Literal["R14_SELECTED_EXECUTION_COMPLETE_SEAL_V1"] = (
        "R14_SELECTED_EXECUTION_COMPLETE_SEAL_V1"
    )
    exchange: RetainedExecutionFanOutPreparation
    canonical_registry_base64: Identity
    registry_reference: CallSubjectHead


class ExecutionCompleteSealPhysicalEnvelope(RecoveryDTO):
    kind: Literal["R14_EXECUTION_COMPLETE_SEAL_PHYSICAL_V1"] = (
        "R14_EXECUTION_COMPLETE_SEAL_PHYSICAL_V1"
    )
    tenant_id: Identity
    operation_kind: Literal["agent_loop.execution-complete-seal.v1"] = (
        "agent_loop.execution-complete-seal.v1"
    )
    idempotency_key: Identity
    expected_head: UInt64
    fence_generation: Literal["r6"] = "r6"
    expected_fence_frontier: Literal[0] = 0
    minimum_fence_frontier: Literal[0] = 0
    records: tuple[FanOutPhysicalMember, FanOutPhysicalMember, FanOutPhysicalMember]
    request_fingerprint: Digest


def retained_execution_complete_seal(
    exchange: RetainedExecutionFanOutPreparation,
) -> RetainedExecutionCompleteSeal:
    registry = execution_zero_call_frontier_registry()
    return RetainedExecutionCompleteSeal(
        exchange=exchange,
        canonical_registry_base64=base64.b64encode(registry.canonical_bytes()).decode(),
        registry_reference=frontier_registry_reference(registry),
    )


def _verified(retained: RetainedExecutionCompleteSeal) -> tuple[ExecutionRunRecord, bytes]:
    exchange = RetainedExecutionFanOutPreparation.model_validate_json(
        retained.exchange.canonical_bytes()
    )
    old = build_envelope(exchange)
    run = exchange.proposal.sealed_run
    _require(
        isinstance(run, ExecutionRunRecord)
        and run.event == "ModelCompletionPrepared"
        and exchange.proposal.fan_out.initialized_records == ()
        and len(old.records) == 2,
        "complete seal is not the eligible zero-call Complete branch",
    )
    raw = _decode(retained.canonical_registry_base64)
    registry = execution_zero_call_frontier_registry()
    expected_reference = frontier_registry_reference(registry)
    _require(retained.registry_reference == expected_reference, "registry reference differs")
    _require(raw == registry.canonical_bytes(), "registry body differs from fixed profile")
    _require(
        decode_frontier_registry(
            RECOVERY_FRONTIER_REGISTRY_SCHEMA, raw, expected_reference=expected_reference
        )
        == registry,
        "registry decoder differs from fixed profile",
    )
    return run, raw


def build_complete_seal_envelope(
    retained: RetainedExecutionCompleteSeal,
) -> ExecutionCompleteSealPhysicalEnvelope:
    retained = RetainedExecutionCompleteSeal.model_validate_json(retained.canonical_bytes())
    run, registry_raw = _verified(retained)
    seal = retained.exchange.proposal.fan_out.response_seal
    digest = hashlib.sha256(registry_raw).hexdigest()
    envelope = ExecutionCompleteSealPhysicalEnvelope(
        tenant_id=run.tenant,
        idempotency_key=run.head,
        expected_head=retained.exchange.request.request.cut.tenant_commit_sequence,
        records=(
            _member(run.head, EXECUTION_RUN_SCHEMA, run.canonical_bytes()),
            _member("record:" + seal.digest(), SEAL_SCHEMA, seal.canonical_bytes()),
            _member(
                "recovery-frontier-registry:" + run.head + ":" + digest,
                RECOVERY_FRONTIER_REGISTRY_SCHEMA,
                registry_raw,
            ),
        ),
        request_fingerprint="0" * 64,
    )
    envelope = envelope.model_copy(update={"request_fingerprint": _without_fingerprint(envelope)})
    _require(
        len(envelope.canonical_bytes())
        <= retained.exchange.request.request.bound.max_serialized_batch_bytes,
        "complete seal physical envelope exceeds bound",
    )
    complete_seal_physical_command(envelope)
    return envelope


def complete_seal_physical_command(
    envelope: ExecutionCompleteSealPhysicalEnvelope,
) -> PhysicalPublicationCommand:
    envelope = ExecutionCompleteSealPhysicalEnvelope.model_validate_json(envelope.canonical_bytes())
    _require(
        envelope.request_fingerprint == _without_fingerprint(envelope),
        "envelope fingerprint differs",
    )
    ids = tuple(member.record_id for member in envelope.records)
    _require(len(ids) == len(set(ids)), "physical member identities duplicate")
    records = tuple(
        PhysicalRecord(
            member.record_id,
            member.owner,
            member.schema_id,
            _decode(member.canonical_payload_base64),
            member.fingerprint,
        )
        for member in envelope.records
    )
    for record in records:
        _require(
            hashlib.sha256(record.canonical_bytes).hexdigest() == record.fingerprint,
            "physical member fingerprint differs",
        )
    return PhysicalPublicationCommand(
        envelope.tenant_id,
        envelope.operation_kind,
        envelope.idempotency_key,
        envelope.request_fingerprint,
        envelope.expected_head,
        envelope.fence_generation,
        envelope.expected_fence_frontier,
        envelope.minimum_fence_frontier,
        records,
    )
