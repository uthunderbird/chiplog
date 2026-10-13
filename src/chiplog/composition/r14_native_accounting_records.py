"""Closed physical record for the supported one-call native cancellation."""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Literal

from pydantic import Field

from chiplog.adapters.driven.loop_sqlite import OWNER
from chiplog.capabilities.agent_loop.native_one_call_accounting_contracts import (
    PreparedNativeOneCallCancelledAccountingV2,
    PrepareNativeOneCallCancelledAccountingV2,
)
from chiplog.capabilities.agent_loop.native_sealed_accounting_v3_contracts import (
    NATIVE_SEALED_ACCOUNTING_V3_REQUEST_SCHEMA,
    NATIVE_SEALED_ACCOUNTING_V3_RESULT_SCHEMA,
    PreparedNativeSealedAccountingV3,
    PrepareNativeSealedAccountingV3,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Digest, Identity, RecoveryDTO
from chiplog.composition.r14_fanout_contracts import FanOutPhysicalMember
from chiplog.composition.r14_native_execution_record_source import (
    R14NativeExecutionRecordSourceManifest,
)
from chiplog.platform._sqlite import PhysicalPublicationCommand, PhysicalRecord
from chiplog.platform.broker import PublicPortCall, PublicPortSuccess

NATIVE_ACCOUNTING_OPERATION = "agent_loop.native-one-call-cancelled-accounting.v1"
NATIVE_ACCOUNTING_SCHEMA = "chiplog.agent-loop.native-one-call-cancelled-accounting.v1"
NATIVE_SEALED_ACCOUNTING_V3_OPERATION = "agent_loop.prepare_native_sealed_accounting.v3"
NATIVE_SEALED_ACCOUNTING_V3_RECORD_SCHEMA = (
    "chiplog.agent-loop.native-sealed-accounting.v3"
)
MAX_NATIVE_ACCOUNTING_RECORD_BYTES = 1_048_576


class NativeOneCallCancelledAccountingRecord(RecoveryDTO):
    """Selected bounded accounting observation; it grants no continuation turn."""

    kind: Literal["NATIVE_ONE_CALL_CANCELLED_ACCOUNTING_V1"] = (
        "NATIVE_ONE_CALL_CANCELLED_ACCOUNTING_V1"
    )
    record_id: Identity
    source_request_fingerprint: Digest
    source_manifest_fingerprint: Digest
    selected_response_fingerprint: Digest
    original_call_id: Identity
    result: PreparedNativeOneCallCancelledAccountingV2


class RetainedNativeOneCallAccountingPublication(RecoveryDTO):
    """Broker-private exact V24 exchange and selected/physical source snapshot."""

    kind: Literal["R14_SELECTED_NATIVE_ONE_CALL_ACCOUNTING_V1"] = (
        "R14_SELECTED_NATIVE_ONE_CALL_ACCOUNTING_V1"
    )
    request: PrepareNativeOneCallCancelledAccountingV2
    response: PreparedNativeOneCallCancelledAccountingV2
    owner_request: PublicPortCall
    owner_response: PublicPortSuccess
    selected_source_bytes: bytes = Field(min_length=1)
    physical_source_bytes: bytes = Field(min_length=1)
    record: NativeOneCallCancelledAccountingRecord


class NativeOneCallAccountingPhysicalEnvelope(RecoveryDTO):
    """Closed one-member operation, separate from the retained owner exchange."""

    kind: Literal["R14_NATIVE_ONE_CALL_ACCOUNTING_PHYSICAL_V1"] = (
        "R14_NATIVE_ONE_CALL_ACCOUNTING_PHYSICAL_V1"
    )
    tenant_id: Identity
    operation_kind: Literal["agent_loop.native-one-call-cancelled-accounting.v1"] = (
        NATIVE_ACCOUNTING_OPERATION
    )
    idempotency_key: Identity
    expected_head: int = Field(ge=0)
    fence_generation: Literal["r6"] = "r6"
    expected_fence_frontier: Literal[0] = 0
    minimum_fence_frontier: Literal[0] = 0
    retained_fingerprint: Digest
    records: tuple[FanOutPhysicalMember, ...] = Field(min_length=1, max_length=1)
    request_fingerprint: Digest


class NativeSealedAccountingSourceStampV3(RecoveryDTO):
    """Writer-owned authority snapshot retained outside the inert owner request."""

    tenant_id: Identity
    database_identity: tuple[Identity, int, int]
    tenant_commit_sequence: int = Field(ge=0)
    authority_materialization_commitment: Digest


class NativeSealedAccountingV3Record(RecoveryDTO):
    """Complete inert per-seal accounting result plus the writer's source stamp."""

    kind: Literal["NATIVE_SEALED_ACCOUNTING_V3"] = "NATIVE_SEALED_ACCOUNTING_V3"
    record_id: Identity
    source_request_fingerprint: Digest
    selected_response_fingerprint: Digest
    sealed_manifest_fingerprint: Digest
    ordered_sealed_call_ids: tuple[Identity, ...] = Field(min_length=1)
    source_stamp: NativeSealedAccountingSourceStampV3
    result: PreparedNativeSealedAccountingV3


class RetainedNativeSealedAccountingV3Publication(RecoveryDTO):
    """Exact owner exchange and source snapshots awaiting sole-writer publication."""

    kind: Literal["R14_SELECTED_NATIVE_SEALED_ACCOUNTING_V3"] = (
        "R14_SELECTED_NATIVE_SEALED_ACCOUNTING_V3"
    )
    request: PrepareNativeSealedAccountingV3
    response: PreparedNativeSealedAccountingV3
    owner_request: PublicPortCall
    owner_response: PublicPortSuccess
    selected_source_bytes: bytes = Field(min_length=1)
    physical_source_bytes: bytes = Field(min_length=1)
    source_stamp: NativeSealedAccountingSourceStampV3
    record: NativeSealedAccountingV3Record


def sealed_accounting_v3_record(
    request: PrepareNativeSealedAccountingV3,
    response: PreparedNativeSealedAccountingV3,
    source_stamp: NativeSealedAccountingSourceStampV3,
) -> NativeSealedAccountingV3Record:
    """Build the exact inert per-seal record from the owner result and stamp."""
    from chiplog.capabilities.agent_loop.native_sealed_accounting_v3_preparation import (
        prepare_native_sealed_accounting_v3,
    )

    if (
        response.source_request_fingerprint
        != hashlib.sha256(request.canonical_bytes()).hexdigest()
        or response.selected_response != request.selected_response
        or response.sealed_manifest_fingerprint != request.sealed_manifest_fingerprint
        or tuple(call.original_call_id for call in response.complete_ordered_calls)
        != request.ordered_sealed_call_ids
        or prepare_native_sealed_accounting_v3(request) != response
    ):
        raise ValueError("native sealed accounting V3 owner result differs from exact request")

    record = _build_sealed_accounting_v3_record(request, response, source_stamp)
    return record


def validate_retained_sealed_accounting_v3_publication(
    evidence: RetainedNativeSealedAccountingV3Publication,
) -> NativeSealedAccountingV3Record:
    """Rebuild one per-seal record from an exact retained owner exchange."""
    sent, returned = evidence.owner_request, evidence.owner_response
    if (
        sent.operation_id != NATIVE_SEALED_ACCOUNTING_V3_OPERATION
        or sent.schema_id != NATIVE_SEALED_ACCOUNTING_V3_REQUEST_SCHEMA
        or sent.canonical_payload != evidence.request.canonical_bytes()
        or sent.caller.owner_id != "broker"
        or sent.callee.owner_id != "agent_loop"
        or not (
            sent.caller.tenant_id
            == sent.callee.tenant_id
            == evidence.source_stamp.tenant_id
        )
        or returned.request_id != sent.request_id
        or returned.responder != sent.callee
        or returned.schema_id != NATIVE_SEALED_ACCOUNTING_V3_RESULT_SCHEMA
        or returned.canonical_payload != evidence.response.canonical_bytes()
        or evidence.record
        != sealed_accounting_v3_record(
            evidence.request, evidence.response, evidence.source_stamp
        )
    ):
        raise ValueError("native sealed accounting V3 retained exchange differs")
    return evidence.record


def _build_sealed_accounting_v3_record(
    request: PrepareNativeSealedAccountingV3,
    response: PreparedNativeSealedAccountingV3,
    source_stamp: NativeSealedAccountingSourceStampV3,
) -> NativeSealedAccountingV3Record:
    record = NativeSealedAccountingV3Record(
        record_id="native-sealed-accounting:" + request.selected_response.revision.fingerprint,
        source_request_fingerprint=response.source_request_fingerprint,
        selected_response_fingerprint=request.selected_response.revision.fingerprint,
        sealed_manifest_fingerprint=request.sealed_manifest_fingerprint,
        ordered_sealed_call_ids=request.ordered_sealed_call_ids,
        source_stamp=source_stamp,
        result=response,
    )
    if len(record.canonical_bytes()) > MAX_NATIVE_ACCOUNTING_RECORD_BYTES:
        raise ValueError("native sealed accounting V3 record exceeds physical bound")
    return record


def accounting_record(
    request: PrepareNativeOneCallCancelledAccountingV2,
    response: PreparedNativeOneCallCancelledAccountingV2,
    source: R14NativeExecutionRecordSourceManifest,
) -> NativeOneCallCancelledAccountingRecord:
    record = NativeOneCallCancelledAccountingRecord(
        record_id="native-accounting:" + request.request.selected_response.revision.fingerprint,
        source_request_fingerprint=response.source_request_fingerprint,
        source_manifest_fingerprint=source.fingerprint,
        selected_response_fingerprint=request.request.selected_response.revision.fingerprint,
        original_call_id=request.request.initialized.subject_id,
        result=response,
    )
    if len(record.canonical_bytes()) > MAX_NATIVE_ACCOUNTING_RECORD_BYTES:
        raise ValueError("native accounting record exceeds physical bound")
    return record


def accounting_command(
    evidence: RetainedNativeOneCallAccountingPublication,
    source: R14NativeExecutionRecordSourceManifest,
) -> PhysicalPublicationCommand:
    """Rebuild the sole physical member from canonical retained evidence."""
    request, response, sent, returned = (
        evidence.request,
        evidence.response,
        evidence.owner_request,
        evidence.owner_response,
    )
    if (
        sent.operation_id != "agent_loop.prepare_native_one_call_cancelled_accounting.v2"
        or sent.schema_id != "chiplog.agent-loop.prepare-native-one-call-accounting.v2"
        or sent.canonical_payload != request.canonical_bytes()
        or sent.caller.owner_id != "broker"
        or sent.callee.owner_id != OWNER
        or not (sent.caller.tenant_id == sent.callee.tenant_id == request.request.tenant_id)
        or returned.request_id != sent.request_id
        or returned.responder != sent.callee
        or returned.schema_id != "chiplog.agent-loop.prepared-native-one-call-accounting.v2"
        or returned.canonical_payload != response.canonical_bytes()
        or response.source_request_fingerprint
        != hashlib.sha256(request.canonical_bytes()).hexdigest()
        or request.source_stamp != source.source_stamp
        or evidence.record != accounting_record(request, response, source)
    ):
        raise ValueError("native accounting retained exchange or source differs")
    from chiplog.capabilities.agent_loop.native_one_call_accounting_preparation import (
        prepare_native_one_call_cancelled_accounting_v2,
    )

    if prepare_native_one_call_cancelled_accounting_v2(request) != response:
        raise ValueError("native accounting owner result differs from exact request")
    raw = evidence.record.canonical_bytes()
    fingerprint = hashlib.sha256(raw).hexdigest()
    member = FanOutPhysicalMember(
        record_id=evidence.record.record_id,
        owner=OWNER,
        schema_id=NATIVE_ACCOUNTING_SCHEMA,
        canonical_payload_base64=base64.b64encode(raw).decode(),
        fingerprint=fingerprint,
    )
    envelope = NativeOneCallAccountingPhysicalEnvelope(
        tenant_id=request.request.tenant_id,
        idempotency_key=evidence.record.record_id,
        expected_head=request.source_stamp.tenant_commit_sequence,
        retained_fingerprint=evidence.digest(),
        records=(member,),
        request_fingerprint="0" * 64,
    )
    header = json.loads(envelope.canonical_bytes())
    del header["request_fingerprint"]
    request_fingerprint = hashlib.sha256(
        json.dumps(header, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    envelope = envelope.model_copy(update={"request_fingerprint": request_fingerprint})
    if len(envelope.canonical_bytes()) > MAX_NATIVE_ACCOUNTING_RECORD_BYTES:
        raise ValueError("native accounting envelope exceeds physical bound")
    return PhysicalPublicationCommand(
        tenant_id=envelope.tenant_id,
        operation_kind=envelope.operation_kind,
        idempotency_key=envelope.idempotency_key,
        request_fingerprint=envelope.request_fingerprint,
        expected_head=envelope.expected_head,
        fence_generation=envelope.fence_generation,
        expected_fence_frontier=envelope.expected_fence_frontier,
        minimum_fence_frontier=envelope.minimum_fence_frontier,
        records=(
            PhysicalRecord(
                member.record_id, member.owner, member.schema_id, raw, member.fingerprint
            ),
        ),
    )


def accounting_envelope(
    evidence: RetainedNativeOneCallAccountingPublication,
    source: R14NativeExecutionRecordSourceManifest,
) -> NativeOneCallAccountingPhysicalEnvelope:
    """Reconstruct the registered physical envelope for selected history."""
    command = accounting_command(evidence, source)
    record = command.records[0]
    return NativeOneCallAccountingPhysicalEnvelope(
        tenant_id=command.tenant_id,
        idempotency_key=command.idempotency_key,
        expected_head=command.expected_head,
        retained_fingerprint=evidence.digest(),
        records=(
            FanOutPhysicalMember(
                record_id=record.record_id,
                owner=record.owner,
                schema_id=record.schema_id,
                canonical_payload_base64=base64.b64encode(record.canonical_bytes).decode(),
                fingerprint=record.fingerprint,
            ),
        ),
        request_fingerprint=command.request_fingerprint,
    )
