"""Pure transport state checks; durable emission-before-invoke needs runtime evidence."""

import base64

import pytest
from tests.support.execution_fan_out import bind_run, fixture

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.execution_attempts import (
    capture_execution_response,
    emit_execution_attempt,
)
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord


@pytest.fixture
async def prepared() -> ExecutionRunRecord:
    request = await fixture()
    run = request.captured_run
    turn = run.turns[-1]
    attempt = turn.attempts[-1].model_copy(
        update={"state": "PREPARED_NOT_EMITTED", "response_base64": None, "receipt": None}
    )
    turn = turn.model_copy(update={"state": "CALL_ACTIVE", "attempts": (attempt,)})
    return bind_run(
        request, run.model_copy(update={"event": "ModelAttemptPrepared", "turns": (turn,)})
    ).captured_run


def test_unknown_emission_cannot_reemit_after_wire_reopen(prepared: ExecutionRunRecord) -> None:
    emitted = emit_execution_attempt(prepared)
    reopened = ExecutionRunRecord.model_validate_json(emitted.canonical_bytes())
    with pytest.raises(LoopRejected):
        emit_execution_attempt(reopened)
    assert reopened.turns[-1].attempts[-1].state == "EMITTED_OUTCOME_UNKNOWN"
    assert reopened.original_obligations == prepared.original_obligations
    assert reopened.no_retry_references == prepared.no_retry_references
    assert reopened.origin == prepared.origin
    assert reopened.predecessor == prepared.head


def test_capture_preserves_original_transport_and_exact_binary_response(
    prepared: ExecutionRunRecord,
) -> None:
    with pytest.raises(LoopRejected):
        capture_execution_response(prepared, b"response", "receipt")
    emitted = emit_execution_attempt(prepared)
    original = emitted.turns[-1].attempts[-1]
    raw = b"\xff\x00not parsed as a tool"
    captured = capture_execution_response(emitted, raw, "original-receipt")
    attempt = captured.turns[-1].attempts[-1]
    assert base64.b64decode(attempt.response_base64 or "", validate=True) == raw
    assert attempt.manifest == original.manifest
    assert attempt.request == original.request
    assert attempt.attempt_id == original.attempt_id
    assert attempt.lineage_id == original.lineage_id
    assert attempt.generation == original.generation
    assert captured.turns[-1].response_seal is None
    assert captured.turns[-1].initialized_calls is None
    assert captured.delivery_acceptance is None
    assert captured.state == "ACTIVE"
    assert captured.predecessor == emitted.head
    with pytest.raises(LoopRejected):
        capture_execution_response(captured, raw, "other")


def test_response_bound_and_missing_receipt_fail_without_changing_unknown(
    prepared: ExecutionRunRecord,
) -> None:
    emitted = emit_execution_attempt(prepared)
    raw = b"x" * emitted.policy.max_response_bytes
    assert capture_execution_response(emitted, raw, "receipt").event == "ModelResponseCaptured"
    with pytest.raises(LoopRejected):
        capture_execution_response(emitted, raw + b"x", "receipt")
    with pytest.raises(LoopRejected):
        capture_execution_response(emitted, b"", "")
    assert emitted.turns[-1].attempts[-1].state == "EMITTED_OUTCOME_UNKNOWN"


@pytest.mark.parametrize("field", ["tenant", "principal", "worker_session", "contour_head"])
def test_manifest_substitution_rejects_even_with_valid_run_head(
    prepared: ExecutionRunRecord, field: str
) -> None:
    turn = prepared.turns[-1]
    attempt = turn.attempts[-1]
    attempt = attempt.model_copy(
        update={"manifest": attempt.manifest.model_copy(update={field: "other"})}
    )
    changed = prepared.model_copy(
        update={"head": "pending", "turns": (turn.model_copy(update={"attempts": (attempt,)}),)}
    )
    changed = changed.model_copy(update={"head": "loop:" + changed.digest()})
    with pytest.raises(LoopRejected):
        emit_execution_attempt(changed)
