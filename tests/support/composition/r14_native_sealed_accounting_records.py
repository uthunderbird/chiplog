"""Shared builders extracted without changing their behavior."""

from __future__ import annotations

from tests.support.capabilities.agent_loop.native_sealed_accounting_v3 import (
    _request,
    _two_frontiers,
)

from chiplog.capabilities.agent_loop._original_recovery_process import (
    PREPARE_SEALED_ACCOUNTING_V3_OPERATION,
)
from chiplog.capabilities.agent_loop.native_sealed_accounting_v3_contracts import (
    NATIVE_SEALED_ACCOUNTING_V3_REQUEST_SCHEMA,
    NATIVE_SEALED_ACCOUNTING_V3_RESULT_SCHEMA,
)
from chiplog.capabilities.agent_loop.native_sealed_accounting_v3_preparation import (
    prepare_native_sealed_accounting_v3,
)
from chiplog.composition.r14_native_accounting_records import (
    NativeSealedAccountingSourceStampV3,
    RetainedNativeSealedAccountingV3Publication,
    sealed_accounting_v3_record,
)
from chiplog.platform.broker import (
    BrokerSession,
    CallBudget,
    PublicPortCall,
    PublicPortSuccess,
)


def _retained():
    request = _request(_two_frontiers())
    response = prepare_native_sealed_accounting_v3(request)
    stamp = NativeSealedAccountingSourceStampV3(
        tenant_id="tenant",
        database_identity=("device", 10, 20),
        tenant_commit_sequence=12,
        authority_materialization_commitment="c" * 64,
    )
    broker = BrokerSession(
        tenant_id="tenant",
        broker_epoch=1,
        generation_id="generation",
        owner_id="broker",
        session_id="broker-session",
    )
    owner = BrokerSession(
        tenant_id="tenant",
        broker_epoch=1,
        generation_id="generation",
        owner_id="agent_loop",
        session_id="owner-session",
    )
    sent = PublicPortCall(
        operation_id=PREPARE_SEALED_ACCOUNTING_V3_OPERATION,
        request_id="request-1",
        caller=broker,
        callee=owner,
        schema_id=NATIVE_SEALED_ACCOUNTING_V3_REQUEST_SCHEMA,
        canonical_payload=request.canonical_bytes(),
        budget=CallBudget(
            remaining_calls=1,
            remaining_depth=1,
            absolute_deadline_ns=100,
            policy_version=1,
        ),
    )
    returned = PublicPortSuccess(
        request_id=sent.request_id,
        responder=owner,
        schema_id=NATIVE_SEALED_ACCOUNTING_V3_RESULT_SCHEMA,
        canonical_payload=response.canonical_bytes(),
    )
    record = sealed_accounting_v3_record(request, response, stamp)
    return RetainedNativeSealedAccountingV3Publication(
        request=request,
        response=response,
        owner_request=sent,
        owner_response=returned,
        selected_source_bytes=b"selected-source",
        physical_source_bytes=b"physical-source",
        source_stamp=stamp,
        record=record,
    )
