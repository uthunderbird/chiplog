from __future__ import annotations

import hashlib

import pytest
from pydantic import TypeAdapter

from chiplog.composition.r14_native_accounting_records import (
    NativeSealedAccountingV3PhysicalEnvelope,
    NativeSealedAccountingV3Record,
    _sealed_accounting_v3_artifacts,
    sealed_accounting_v3_command,
    sealed_accounting_v3_envelope,
    validate_retained_sealed_accounting_v3_publication,
)
from tests.support.composition.r14_native_sealed_accounting_records import (
    _retained,
)


def test_v3_record_retains_mixed_per_seal_result_and_external_source_stamp() -> None:
    evidence = _retained()

    selected = validate_retained_sealed_accounting_v3_publication(evidence)
    reopened = TypeAdapter(NativeSealedAccountingV3Record).validate_json(selected.canonical_bytes())

    assert reopened == selected
    assert selected.record_id == "native-sealed-accounting:" + "a" * 64
    assert selected.ordered_sealed_call_ids == ("call", "call-2")
    assert (
        tuple(call.original_call_id for call in selected.result.complete_ordered_calls)
        == selected.ordered_sealed_call_ids
    )
    assert selected.source_stamp == evidence.source_stamp
    assert "source_stamp" not in evidence.request.model_dump()


def test_v3_candidate_command_is_one_seal_scoped_atomic_physical_row() -> None:
    evidence = _retained()

    command = sealed_accounting_v3_command(evidence)
    envelope = sealed_accounting_v3_envelope(evidence)
    record, bundled_command, bundled_envelope = _sealed_accounting_v3_artifacts(evidence)
    reopened = TypeAdapter(NativeSealedAccountingV3PhysicalEnvelope).validate_json(
        envelope.canonical_bytes()
    )

    assert reopened == envelope
    assert record == evidence.record
    assert bundled_command == command
    assert bundled_envelope == envelope
    assert command.tenant_id == "tenant"
    assert command.expected_head == 12
    assert command.idempotency_key == evidence.record.record_id
    assert command.operation_kind == "agent_loop.native-sealed-accounting.v3"
    assert len(command.records) == len(envelope.records) == 1
    assert command.records[0].record_id == evidence.record.record_id
    assert command.records[0].canonical_bytes == evidence.record.canonical_bytes()
    assert (
        command.records[0].fingerprint
        == hashlib.sha256(evidence.record.canonical_bytes()).hexdigest()
    )


@pytest.mark.parametrize(
    "mutation",
    ("request_bytes", "response_bytes", "selected_seal", "member_order"),
)
def test_v3_record_rejects_one_sided_owner_exchange_substitution(mutation: str) -> None:
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
                "complete_ordered_calls": tuple(reversed(evidence.response.complete_ordered_calls))
            }
        )
        evidence = evidence.model_copy(update={"response": changed_response})

    with pytest.raises(ValueError, match="retained exchange"):
        validate_retained_sealed_accounting_v3_publication(evidence)
