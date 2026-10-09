"""Selected original resolution is re-derived before publication or readback."""

from __future__ import annotations

import pytest

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
    command,
)
from chiplog.platform.broker import BrokerSession, PublicPortCall, PublicPortSuccess
from tests.capabilities.agent_loop.test_original_call_resolution_preparation import request


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


def test_command_rederives_owner_result_and_exact_four_record_batch() -> None:
    evidence = publication_evidence()

    batch = command(evidence)

    assert batch.operation_kind == "agent_loop.original-call-resolution.v1"
    assert tuple(record.schema_id for record in batch.records) == (
        "chiplog.loop.original-resolution-basis.v1",
        "chiplog.loop.original-obligation-closure.v1",
        "chiplog.loop.recovered-call-outcome.v1",
        "chiplog.loop.original-resolver-batch.v1",
    )
    assert batch.idempotency_key == evidence.prepared.batch.batch_id


def test_command_rejects_mismatched_owner_reply() -> None:
    evidence = publication_evidence()
    changed = evidence.model_copy(
        update={
            "owner_reply": evidence.owner_reply.model_copy(
                update={"canonical_payload": b"{}"}
            )
        }
    )

    with pytest.raises(ValueError, match=r"invalid .* recovery-wire body"):
        command(changed)
