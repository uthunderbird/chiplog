"""Public consumer shape checks, not selection or runtime-authority evidence."""

import base64
import hashlib

import pytest
from pydantic import ValidationError

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.composition.r14_cancellation_contracts import (
    CancelCallSubmission,
    RetainedCancellationPreparation,
)
from chiplog.composition.r14_execution_cancellation_contracts import (
    ExecutionCallCancellationPort,
    ExecutionCancellationPhysicalEnvelope,
    ExecutionCancelledCallReceipt,
    RetainedExecutionCancellationPreparation,
)
from chiplog.composition.r14_fanout_contracts import FanOutPhysicalMember
from tests.support.execution_cancellation import retained_shape


def _head(identity: str) -> CallSubjectHead:
    return CallSubjectHead(
        subject_id=identity,
        revision=Present(head="record:" + identity, fingerprint="a" * 64),
    )


def _receipt() -> ExecutionCancelledCallReceipt:
    return ExecutionCancelledCallReceipt(
        original_call_id="call:one",
        initialized=_head("call:one"),
        terminal=_head("terminal:one"),
        result=_head("result:one"),
        publication_id="cancellation:one",
        publication_fingerprint="b" * 64,
    )


class _WireOnlyPort:
    async def cancel_execution_call(
        self, peer: str, submission: CancelCallSubmission
    ) -> ExecutionCancelledCallReceipt:
        assert peer == "shape-only-peer"
        assert submission.original_call_id == "call:one"
        return _receipt()


async def test_public_consumer_receives_receipt_without_run_companion() -> None:
    port: ExecutionCallCancellationPort = _WireOnlyPort()
    receipt = await port.cancel_execution_call(
        "shape-only-peer",
        CancelCallSubmission(
            act_id="act:one",
            original_call_id="call:one",
            initialized=_head("call:one"),
            current_run=_head("run:one"),
        ),
    )
    assert ExecutionCancelledCallReceipt.model_validate_json(receipt.canonical_bytes()) == receipt
    assert receipt.terminal == _head("terminal:one")
    assert receipt.result == _head("result:one")


@pytest.mark.parametrize("field", ("authorized", "run_companion", "provider_success"))
def test_receipt_rejects_authority_and_unrelated_claims(field: str) -> None:
    with pytest.raises(ValidationError):
        ExecutionCancelledCallReceipt.model_validate({**_receipt().model_dump(), field: True})


def _envelope() -> ExecutionCancellationPhysicalEnvelope:
    records = tuple(
        FanOutPhysicalMember(
            record_id=name,
            owner="agent_loop",
            schema_id=schema,
            canonical_payload_base64=base64.b64encode(raw).decode(),
            fingerprint=hashlib.sha256(raw).hexdigest(),
        )
        for name, schema, raw in (
            ("terminal", "chiplog.call.cancelled-before-accept.v1", b"\x00terminal\xff"),
            ("result", "chiplog.call.not-executed-result.v1", b"\x00result\xfe"),
        )
    )
    return ExecutionCancellationPhysicalEnvelope(
        tenant_id="tenant",
        idempotency_key="cancel:one",
        expected_head=4,
        retained_preparation_fingerprint="c" * 64,
        records=records,
        request_fingerprint="d" * 64,
    )


def test_physical_wire_preserves_order_and_binary_bytes_without_claiming_semantics() -> None:
    envelope = _envelope()
    decoded = ExecutionCancellationPhysicalEnvelope.model_validate_json(envelope.canonical_bytes())
    assert decoded == envelope
    assert tuple(base64.b64decode(row.canonical_payload_base64) for row in decoded.records) == (
        b"\x00terminal\xff",
        b"\x00result\xfe",
    )


@pytest.mark.parametrize("count", (0, 1, 3))
def test_physical_wire_rejects_missing_or_extra_members(count: int) -> None:
    envelope = _envelope()
    with pytest.raises(ValidationError):
        ExecutionCancellationPhysicalEnvelope.model_validate(
            {
                **envelope.model_dump(),
                "records": [envelope.records[0]] * count,
            }
        )


@pytest.mark.parametrize(
    "field,value",
    (
        ("kind", "R14_CANCELLATION_PHYSICAL_V1"),
        ("operation_kind", "agent_loop.cancel-before-accept.v1"),
        ("operation_kind", "unregistered"),
    ),
)
def test_legacy_or_unknown_envelope_discriminants_reject(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        ExecutionCancellationPhysicalEnvelope.model_validate(
            {**_envelope().model_dump(), field: value}
        )


async def test_retained_transport_preserves_original_binary_and_ipc_bytes() -> None:
    retained = await retained_shape()
    decoded = RetainedExecutionCancellationPreparation.model_validate_json(
        retained.canonical_bytes()
    )
    assert decoded == retained
    assert decoded.trust.snapshot_bytes == b"\xff\x00snapshot"
    assert decoded.act.authenticated_reference_bytes == b"\xfe\x00reference"
    assert decoded.owner_request.canonical_payload == retained.request.canonical_bytes()
    assert decoded.owner_response.canonical_payload == retained.proposal.canonical_bytes()
    assert decoded.run_predecessor == retained.run_predecessor
    with pytest.raises(ValidationError):
        RetainedCancellationPreparation.model_validate_json(retained.canonical_bytes())


@pytest.mark.parametrize(
    "field", ("act", "trust", "owner_request", "owner_response", "run_predecessor")
)
async def test_retention_requires_original_evidence(field: str) -> None:
    value = (await retained_shape()).model_dump()
    del value[field]
    with pytest.raises(ValidationError):
        RetainedExecutionCancellationPreparation.model_validate(value)


async def test_retained_legacy_discriminant_and_run_companion_are_not_admitted() -> None:
    value = (await retained_shape()).model_dump()
    with pytest.raises(ValidationError):
        RetainedExecutionCancellationPreparation.model_validate(
            {
                **value,
                "kind": "R14_SELECTED_CANCELLATION_PREPARATION_V1",
            }
        )
    with pytest.raises(ValidationError):
        RetainedExecutionCancellationPreparation.model_validate(
            {**value, "run_companion": value["run_predecessor"]}
        )
