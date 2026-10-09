"""Selected original resolution is re-derived before publication or readback."""

from __future__ import annotations

from types import SimpleNamespace

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
    EVIDENCE_FIELD,
    OPERATION,
    OWNER_OPERATION,
    RetainedOriginalCallResolutionPublication,
    command,
    validate_selected_command,
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


def test_history_validation_checks_retained_command_without_source_recapture() -> None:
    evidence = publication_evidence()
    batch = command(evidence)
    runtime = SimpleNamespace(
        _tenant_id=evidence.tenant_id,
        _database_identity=(evidence.database_id, 1, 2),
        _check_database_identity=lambda: None,
    )
    entry = {
        "operation_kind": OPERATION,
        "operation_id": batch.idempotency_key,
        "predecessor": evidence.predecessor,
        "resulting": "a" * 64,
        "expected_head": batch.expected_head,
        "fingerprint": batch.request_fingerprint,
        EVIDENCE_FIELD: evidence.canonical_bytes().decode(),
    }

    assert validate_selected_command(runtime, entry, batch) == evidence


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("operation_kind", "agent_loop.substituted.v1"),
        ("operation_id", "substituted"),
        ("predecessor", "f" * 64),
        ("resulting", "not-a-digest"),
        ("expected_head", 0),
        ("fingerprint", "e" * 64),
    ),
)
def test_history_validation_rejects_selected_entry_substitution(field, value) -> None:
    evidence = publication_evidence()
    batch = command(evidence)
    runtime = SimpleNamespace(
        _tenant_id=evidence.tenant_id,
        _database_identity=(evidence.database_id, 1, 2),
        _check_database_identity=lambda: None,
    )
    entry = {
        "operation_kind": OPERATION,
        "operation_id": batch.idempotency_key,
        "predecessor": evidence.predecessor,
        "resulting": "a" * 64,
        "expected_head": batch.expected_head,
        "fingerprint": batch.request_fingerprint,
        EVIDENCE_FIELD: evidence.canonical_bytes().decode(),
    }
    entry[field] = value

    with pytest.raises(ValueError):
        validate_selected_command(runtime, entry, batch)


def test_history_validation_rechecks_physical_database_identity() -> None:
    evidence = publication_evidence()
    batch = command(evidence)
    runtime = SimpleNamespace(
        _tenant_id=evidence.tenant_id,
        _database_identity=(evidence.database_id, 1, 2),
        _check_database_identity=lambda: (_ for _ in ()).throw(
            ValueError("runtime physical database identity changed")
        ),
    )
    entry = {
        "operation_kind": OPERATION,
        "operation_id": batch.idempotency_key,
        "predecessor": evidence.predecessor,
        "resulting": "a" * 64,
        "expected_head": batch.expected_head,
        "fingerprint": batch.request_fingerprint,
        EVIDENCE_FIELD: evidence.canonical_bytes().decode(),
    }

    with pytest.raises(ValueError, match="physical database identity changed"):
        validate_selected_command(runtime, entry, batch)
