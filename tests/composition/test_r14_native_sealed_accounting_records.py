from __future__ import annotations

import hashlib

import pytest
from pydantic import TypeAdapter

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
    NativeSealedAccountingV3PhysicalEnvelope,
    NativeSealedAccountingV3Record,
    RetainedNativeSealedAccountingV3Publication,
    sealed_accounting_v3_command,
    sealed_accounting_v3_envelope,
    sealed_accounting_v3_record,
    validate_retained_sealed_accounting_v3_publication,
)
from chiplog.platform.broker import (
    BrokerSession,
    CallBudget,
    PublicPortCall,
    PublicPortSuccess,
)
from tests.capabilities.agent_loop.test_native_sealed_accounting_v3 import (
    _request,
    _two_frontiers,
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


def test_v3_record_retains_mixed_per_seal_result_and_external_source_stamp():
    evidence = _retained()

    selected = validate_retained_sealed_accounting_v3_publication(evidence)
    reopened = TypeAdapter(NativeSealedAccountingV3Record).validate_json(
        selected.canonical_bytes()
    )

    assert reopened == selected
    assert selected.record_id == "native-sealed-accounting:" + "a" * 64
    assert selected.ordered_sealed_call_ids == ("call", "call-2")
    assert tuple(
        call.original_call_id for call in selected.result.complete_ordered_calls
    ) == selected.ordered_sealed_call_ids
    assert selected.source_stamp == evidence.source_stamp
    assert "source_stamp" not in evidence.request.model_dump()


def test_v3_candidate_command_is_one_seal_scoped_atomic_physical_row():
    evidence = _retained()

    command = sealed_accounting_v3_command(evidence)
    envelope = sealed_accounting_v3_envelope(evidence)
    reopened = TypeAdapter(NativeSealedAccountingV3PhysicalEnvelope).validate_json(
        envelope.canonical_bytes()
    )

    assert reopened == envelope
    assert command.tenant_id == "tenant"
    assert command.expected_head == 12
    assert command.idempotency_key == evidence.record.record_id
    assert command.operation_kind == "agent_loop.native-sealed-accounting.v3"
    assert len(command.records) == len(envelope.records) == 1
    assert command.records[0].record_id == evidence.record.record_id
    assert command.records[0].canonical_bytes == evidence.record.canonical_bytes()
    assert command.records[0].fingerprint == hashlib.sha256(
        evidence.record.canonical_bytes()
    ).hexdigest()


@pytest.mark.parametrize(
    "mutation",
    ("request_bytes", "response_bytes", "selected_seal", "member_order"),
)
def test_v3_record_rejects_one_sided_owner_exchange_substitution(mutation: str):
    evidence = _retained()
    if mutation == "request_bytes":
        evidence = evidence.model_copy(
            update={
                "owner_request": evidence.owner_request.model_copy(
                    update={"canonical_payload": b"{}"}
                )
            }
        )
    elif mutation == "response_bytes":
        evidence = evidence.model_copy(
            update={
                "owner_response": evidence.owner_response.model_copy(
                    update={"canonical_payload": b"{}"}
                )
            }
        )
    elif mutation == "selected_seal":
        changed = evidence.request.model_copy(
            update={
                "selected_response": evidence.request.selected_response.model_copy(
                    update={
                        "revision": evidence.request.selected_response.revision.model_copy(
                            update={"fingerprint": "d" * 64}
                        )
                    }
                )
            }
        )
        evidence = evidence.model_copy(update={"request": changed})
    else:
        changed_response = evidence.response.model_copy(
            update={
                "complete_ordered_calls": tuple(
                    reversed(evidence.response.complete_ordered_calls)
                )
            }
        )
        evidence = evidence.model_copy(update={"response": changed_response})

    with pytest.raises(ValueError, match="retained exchange"):
        validate_retained_sealed_accounting_v3_publication(evidence)
