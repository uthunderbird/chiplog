"""Shared builders extracted without changing their behavior."""

from __future__ import annotations

from tests.support.capabilities.agent_loop.original_semantic_reduction_preparation import request

from chiplog.capabilities.agent_loop.original_recovery_contracts import (
    PreparedLoopSemanticReduction,
)
from chiplog.capabilities.agent_loop.original_semantic_reduction_preparation import (
    prepare_loop_semantic_reduction,
)
from chiplog.capabilities.agent_loop.recovery_wire_contracts import (
    ORIGINAL_RECOVERY_REQUEST_SCHEMA,
    ORIGINAL_RECOVERY_RESULT_SCHEMA,
)
from chiplog.composition.r14_loop_semantic_reduction_publication import (
    OWNER_OPERATION,
    RetainedLoopSemanticReductionPublication,
)
from chiplog.platform.broker import BrokerSession, PublicPortCall, PublicPortSuccess


def retained() -> RetainedLoopSemanticReductionPublication:
    source = request()
    prepared = prepare_loop_semantic_reduction(source)
    assert isinstance(prepared, PreparedLoopSemanticReduction)
    session = BrokerSession(
        tenant_id=source.tenant_id,
        broker_epoch=1,
        generation_id="generation-1",
        owner_id="agent_loop",
        session_id="agent-loop:generation-1",
    )
    call = PublicPortCall(
        operation_id=OWNER_OPERATION,
        request_id="semantic-reduction:test",
        caller=session.model_copy(
            update={"owner_id": "broker", "session_id": "broker:generation-1"}
        ),
        callee=session,
        schema_id=ORIGINAL_RECOVERY_REQUEST_SCHEMA,
        canonical_payload=source.canonical_bytes(),
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
    return RetainedLoopSemanticReductionPublication(
        tenant_id=source.tenant_id,
        database_id=source.database_id,
        request=source,
        prepared=prepared,
        owner_call=call,
        owner_reply=reply,
        expected_head=source.tenant_commit_sequence,
        predecessor=source.materialization_commitment,
    )
