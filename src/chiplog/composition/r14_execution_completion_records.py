"""Retained CompleteDeliveryBatchV2 exchange and its sole physical projection.

This adapter has no writer authority.  It replays the exact accepted owner
exchange into one physical command only after the shared completion validator
reconstructs every command and record from the retained assembly.
"""

from __future__ import annotations

import hashlib
from typing import Literal

from chiplog.capabilities.agent_loop.recovery_contracts import Digest, RecoveryDTO, UInt64
from chiplog.composition.completion_publication_contracts import (
    PrepareCompleteAcceptanceAssemblyV1,
    PrepareH1CompleteAcceptanceAssemblyV1,
    validate_complete_acceptance_batch,
)
from chiplog.platform._owner_publication_contracts import CompleteDeliveryBatchV2
from chiplog.platform._sqlite import PhysicalPublicationCommand, PhysicalRecord

COMPLETE_ACCEPTANCE_OPERATION = "agent_loop.complete_acceptance.v2"


class RetainedCompleteAcceptanceExchangeV1(RecoveryDTO):
    """The selected complete-acceptance assembly and exact broker batch.

    ``predecessor_commitment`` is retained because physical commands carry only
    the numeric head.  It remains part of the selected read cut and is checked
    before any physical command can be handed to a writer.
    """

    kind: Literal["R14_RETAINED_COMPLETE_ACCEPTANCE_EXCHANGE_V1"] = (
        "R14_RETAINED_COMPLETE_ACCEPTANCE_EXCHANGE_V1"
    )
    assembly: PrepareCompleteAcceptanceAssemblyV1 | PrepareH1CompleteAcceptanceAssemblyV1
    batch: CompleteDeliveryBatchV2
    expected_head: UInt64
    predecessor_commitment: Digest


class RetainedH1ScopedV3CompleteAcceptanceExchangeV1(RecoveryDTO):
    """The exact scoped V3 issuance retained for one physical projection.

    The issuance remains opaque at this boundary. The command projector
    canonically decodes it again against ``batch`` before it can construct a
    physical command.
    """

    kind: Literal["R14_RETAINED_H1_SCOPED_V3_COMPLETE_ACCEPTANCE_EXCHANGE_V1"] = (
        "R14_RETAINED_H1_SCOPED_V3_COMPLETE_ACCEPTANCE_EXCHANGE_V1"
    )
    issuance_bytes: bytes
    batch: CompleteDeliveryBatchV2
    expected_head: UInt64
    predecessor_commitment: Digest


class RetainedH1ScopedV4CompleteAcceptanceExchangeV1(RecoveryDTO):
    """The exact dispatch-V2 V4 issuance for one physical projection."""

    kind: Literal["R14_RETAINED_H1_SCOPED_V4_COMPLETE_ACCEPTANCE_EXCHANGE_V1"] = (
        "R14_RETAINED_H1_SCOPED_V4_COMPLETE_ACCEPTANCE_EXCHANGE_V1"
    )
    issuance_bytes: bytes
    batch: CompleteDeliveryBatchV2
    expected_head: UInt64
    predecessor_commitment: Digest


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError("invalid retained completion batch: " + reason)


def complete_acceptance_command(
    evidence: (
        RetainedCompleteAcceptanceExchangeV1
        | RetainedH1ScopedV3CompleteAcceptanceExchangeV1
        | RetainedH1ScopedV4CompleteAcceptanceExchangeV1
    ),
) -> PhysicalPublicationCommand:
    """Reconstruct the one exact physical command for a retained accepted batch."""
    if type(evidence) is RetainedCompleteAcceptanceExchangeV1:
        evidence = RetainedCompleteAcceptanceExchangeV1.model_validate_json(
            evidence.canonical_bytes()
        )
        assembly = evidence.assembly
        scoped = False
    elif type(evidence) is RetainedH1ScopedV3CompleteAcceptanceExchangeV1:
        evidence = RetainedH1ScopedV3CompleteAcceptanceExchangeV1.model_validate_json(
            evidence.canonical_bytes()
        )
        _require(
            evidence.issuance_bytes == evidence.batch.authentication.applicability_bytes,
            "scoped V3 issuance differs from retained batch",
        )
        # Local import prevents a module cycle: issuance emits this DTO, while
        # this physical boundary owns its exact re-decode.
        from chiplog.composition.h1_completion_issuance import (
            H1CompletionIssuanceV3,
            decode_h1_completion_issuance,
        )

        issuance = decode_h1_completion_issuance(evidence.batch)
        _require(
            type(issuance) is H1CompletionIssuanceV3,
            "retained scoped V3 issuance has the wrong exact class",
        )
        assembly = issuance.assembly
        scoped = True
    elif type(evidence) is RetainedH1ScopedV4CompleteAcceptanceExchangeV1:
        evidence = RetainedH1ScopedV4CompleteAcceptanceExchangeV1.model_validate_json(
            evidence.canonical_bytes()
        )
        _require(
            evidence.issuance_bytes == evidence.batch.authentication.applicability_bytes,
            "scoped V4 issuance differs from retained batch",
        )
        from chiplog.composition.h1_completion_issuance import (
            H1CompletionIssuanceV4,
            decode_h1_completion_issuance,
        )

        issuance = decode_h1_completion_issuance(evidence.batch)
        _require(
            type(issuance) is H1CompletionIssuanceV4,
            "retained scoped V4 issuance has the wrong exact class",
        )
        assembly = issuance.assembly
        scoped = True
    else:
        raise TypeError("complete acceptance requires retained completion evidence")
    batch = evidence.batch
    _require(
        batch.operation == COMPLETE_ACCEPTANCE_OPERATION,
        "operation differs from complete acceptance v2",
    )
    _require(
        evidence.expected_head == batch.expected.tenant_frontier,
        "expected head differs from retained read cut",
    )
    _require(
        evidence.predecessor_commitment == batch.expected.expected_materialization_commitment,
        "predecessor commitment differs from retained read cut",
    )
    _require(
        batch.identity.tenant_id == batch.expected.tenant_id,
        "batch identity tenant differs from retained read cut",
    )
    if not scoped:
        failure = validate_complete_acceptance_batch(assembly, batch)
        if failure is not None:
            raise ValueError(
                "invalid retained completion batch: " + failure.code + " validation failed"
            )

    physical_records = tuple(
        PhysicalRecord(
            record_id=record.record_id,
            owner=record.owner,
            schema_id=record.schema_id,
            canonical_bytes=record.canonical_bytes,
            fingerprint=record.fingerprint,
        )
        for record in batch.complete_records
    )
    _require(
        all(
            hashlib.sha256(record.canonical_bytes).hexdigest() == record.fingerprint
            for record in physical_records
        ),
        "physical record fingerprint differs",
    )
    return PhysicalPublicationCommand(
        tenant_id=batch.identity.tenant_id,
        operation_kind=batch.operation,
        idempotency_key=batch.identity.command_id,
        request_fingerprint=batch.identity.command_fingerprint,
        expected_head=evidence.expected_head,
        fence_generation="r6",
        expected_fence_frontier=0,
        minimum_fence_frontier=0,
        records=physical_records,
    )
