"""Shared builders extracted without changing their behavior."""

from __future__ import annotations

from tests.support.capabilities.agent_loop.original_call_resolution_preparation import request

from chiplog.capabilities.agent_loop.original_call_resolution_preparation import (
    prepare_original_call_resolution,
)
from chiplog.capabilities.agent_loop.original_recovery_contracts import (
    PreparedOriginalCallResolution,
)
from chiplog.capabilities.agent_loop.recovery_wire_contracts import (
    ORIGINAL_RECOVERY_REQUEST_SCHEMA,
    ORIGINAL_RECOVERY_RESULT_SCHEMA,
)
from chiplog.composition.r14_original_call_resolution_publication import (
    OWNER_OPERATION,
    RetainedOriginalCallResolutionPublication,
)
from chiplog.platform.broker import BrokerSession, PublicPortCall, PublicPortSuccess


def publication_evidence() -> RetainedOriginalCallResolutionPublication:
    source_request = request()
    prepared = prepare_original_call_resolution(source_request)
    assert isinstance(prepared, PreparedOriginalCallResolution)
    session = BrokerSession(
        tenant_id=source_request.cut.tenant_id,
        broker_epoch=1,
        generation_id="generation-1",
        owner_id="agent_loop",
        session_id="agent-loop:generation-1",
    )
    call = PublicPortCall(
        operation_id=OWNER_OPERATION,
        request_id="original-resolution:test",
        caller=session.model_copy(
            update={"owner_id": "broker", "session_id": "broker:generation-1"}
        ),
        callee=session,
        schema_id=ORIGINAL_RECOVERY_REQUEST_SCHEMA,
        canonical_payload=source_request.canonical_bytes(),
        budget={
            "remaining_calls": 1,
            "remaining_depth": 1,
            "policy_version": 1,
            "absolute_deadline_ns": 10_000_000_000,
        },
    )
    reply = PublicPortSuccess(
        request_id=call.request_id,
        responder=session,
        schema_id=ORIGINAL_RECOVERY_RESULT_SCHEMA,
        canonical_payload=prepared.canonical_bytes(),
    )
    return RetainedOriginalCallResolutionPublication(
        tenant_id=source_request.cut.tenant_id,
        database_id=source_request.cut.database_id,
        request=source_request,
        prepared=prepared,
        owner_call=call,
        owner_reply=reply,
        expected_head=source_request.cut.tenant_commit_sequence,
        predecessor=source_request.cut.materialization_commitment,
    )
