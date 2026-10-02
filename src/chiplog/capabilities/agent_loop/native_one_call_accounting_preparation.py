"""Pure validation and accounting for the bounded native cancellation exchange."""

from __future__ import annotations

import hashlib
from base64 import b64decode

from pydantic import TypeAdapter, ValidationError

from .call_acceptance_contracts import (
    CallSubjectHead,
    CancelledBeforeAcceptRecord,
    InitializedCallRecord,
    NotExecutedCallResultRecord,
    SealedResponseRecord,
)
from .execution_contracts import ConsequentialToolCall, ExecutionContinue, ExecutionRunRecord
from .execution_parsing import parse_execution_response
from .execution_preparation_bindings_contracts import (
    decode_native_execution_preparation_bindings_v2,
)
from .execution_run_versions import ExecutionRun
from .native_one_call_accounting_contracts import (
    PreparedNativeOneCallCancelledAccounting,
    PreparedNativeOneCallCancelledAccountingV2,
    PrepareNativeOneCallCancelledAccounting,
    PrepareNativeOneCallCancelledAccountingV2,
)
from .recovery_contracts import NotApplicable, Present, RecoveryDTO
from .recovery_domain import RecoveryHold, sealed_call_accounting
from .recovery_frontier_contracts import CancelledBeforeAcceptResult, TerminalCallFrontier
from .recovery_frontier_registry_contracts import (
    RECOVERY_FRONTIER_REGISTRY_SCHEMA,
    decode_native_one_call_cancellation_frontier_registry_v1,
)


def _reference(subject: str, value: RecoveryDTO | ExecutionRun) -> CallSubjectHead:
    digest = hashlib.sha256(value.canonical_bytes()).hexdigest()
    return CallSubjectHead(
        subject_id=subject,
        revision=Present(head="record:" + digest, fingerprint=digest),
    )


def _decode(raw: bytes, type_: type[RecoveryDTO], label: str) -> RecoveryDTO:
    try:
        value = type_.model_validate_json(raw)
    except (IndexError, UnicodeDecodeError, ValidationError, ValueError) as error:
        raise RecoveryHold(f"invalid {label} bytes") from error
    if value.canonical_bytes() != raw:
        raise RecoveryHold(f"noncanonical {label} bytes")
    return value


def _decode_run(raw: bytes, label: str) -> ExecutionRun:
    try:
        value: ExecutionRun = TypeAdapter(ExecutionRun).validate_json(raw)
    except (UnicodeDecodeError, ValidationError, ValueError) as error:
        raise RecoveryHold(f"invalid {label} bytes") from error
    if value.canonical_bytes() != raw:
        raise RecoveryHold(f"noncanonical {label} bytes")
    return value


def _run_reference(run: ExecutionRun) -> CallSubjectHead:
    run_id = run.run_id
    head = run.head
    digest = run.digest()
    if not all((run_id, head, digest)):
        raise RecoveryHold("invalid native Run identity")
    return CallSubjectHead(
        subject_id=run_id,
        revision=Present(head=head, fingerprint=digest),
    )


def prepare_native_one_call_cancelled_accounting(
    request: PrepareNativeOneCallCancelledAccounting,
) -> PreparedNativeOneCallCancelledAccounting:
    """Validate every direct-wire relation before the generic frontier join."""

    request = PrepareNativeOneCallCancelledAccounting.model_validate_json(request.canonical_bytes())
    captured_run = _decode_run(request.captured_run_bytes, "captured Run")
    sealed_run = _decode_run(request.selected_sealed_run_bytes, "sealed Run")
    if not isinstance(captured_run, ExecutionRunRecord) or not isinstance(
        sealed_run, ExecutionRunRecord
    ):
        raise RecoveryHold("native cancellation requires v2 physical Runs")
    response = _decode(request.selected_response_bytes, SealedResponseRecord, "response seal")
    initialized = _decode(
        request.initialized_record_bytes, InitializedCallRecord, "initialized record"
    )
    terminal = _decode(request.terminal_record_bytes, CancelledBeforeAcceptRecord, "terminal")
    result = _decode(
        request.result_record_bytes, NotExecutedCallResultRecord, "not-executed result"
    )
    assert isinstance(response, SealedResponseRecord)
    assert isinstance(initialized, InitializedCallRecord)
    assert isinstance(terminal, CancelledBeforeAcceptRecord)
    assert isinstance(result, NotExecutedCallResultRecord)

    if _run_reference(captured_run) != request.captured_run:
        raise RecoveryHold("captured Run reference differs from canonical record")
    if _reference(sealed_run.run_id, sealed_run) != request.selected_sealed_run:
        raise RecoveryHold("selected sealed Run record reference differs from canonical record")
    if _run_reference(sealed_run) != request.physical_sealed_run:
        raise RecoveryHold("physical sealed Run locator differs from canonical record")
    if _reference(response.response_seal_id, response) != request.selected_response:
        raise RecoveryHold("selected response reference differs from canonical record")
    if _reference(initialized.original_call_id, initialized) != request.initialized:
        raise RecoveryHold("initialized reference differs from canonical record")
    if _reference(terminal.terminal_id, terminal) != request.terminal:
        raise RecoveryHold("terminal reference differs from canonical record")
    if _reference(result.result_id, result) != request.result:
        raise RecoveryHold("result reference differs from canonical record")
    try:
        decode_native_one_call_cancellation_frontier_registry_v1(
            RECOVERY_FRONTIER_REGISTRY_SCHEMA,
            request.frontier_registry_bytes,
            expected_reference=request.frontier_registry,
        )
    except ValueError as error:
        raise RecoveryHold("fixed native cancellation registry differs from direct wire") from error

    original = initialized.call.original
    if not captured_run.turns or not sealed_run.turns:
        raise RecoveryHold("native cancellation Run has no selected turn")
    capture_turn = captured_run.turns[-1]
    sealed_turn = sealed_run.turns[-1]
    if (
        not capture_turn.attempts
        or not sealed_turn.attempts
        or capture_turn.selector != len(capture_turn.attempts) - 1
        or sealed_turn.selector != len(sealed_turn.attempts) - 1
    ):
        raise RecoveryHold("native cancellation Run has no selected attempt")
    capture_attempt = capture_turn.attempts[capture_turn.selector]
    sealed_attempt = sealed_turn.attempts[sealed_turn.selector]
    try:
        raw_call = b64decode(initialized.call.canonical_call_base64, validate=True)
        native_call = ConsequentialToolCall.model_validate_json(raw_call)
        captured_response = b64decode(capture_attempt.response_base64 or "", validate=True)
        parsed_response = parse_execution_response(
            captured_response, capture_attempt.manifest.artifact
        )
    except (UnicodeDecodeError, ValidationError, ValueError) as error:
        raise RecoveryHold("invalid initialized native call") from error
    if (
        native_call.canonical_bytes() != raw_call
        or native_call.tool != "request_self_effect"
        or initialized.call.classification != "CONSEQUENTIAL"
        or not isinstance(initialized.call.retry_lineage, NotApplicable)
        or captured_run.tenant != request.tenant_id
        or sealed_run.tenant != request.tenant_id
        or captured_run.state != "ACTIVE"
        or sealed_run.state != "ACTIVE"
        or captured_run.event != "ModelResponseCaptured"
        or sealed_run.event != "ModelResponseSealed"
        or captured_run.head
        != "loop:" + captured_run.model_copy(update={"head": "pending"}).digest()
        or sealed_run.head != "loop:" + sealed_run.model_copy(update={"head": "pending"}).digest()
        or captured_run.run_id != original.original_run_id
        or sealed_run.run_id != original.original_run_id
        or sealed_run.predecessor != captured_run.head
        or sealed_run.model_dump(exclude={"head", "predecessor", "event", "turns"})
        != captured_run.model_dump(exclude={"head", "predecessor", "event", "turns"})
        or sealed_run.turns[:-1] != captured_run.turns[:-1]
        or len(sealed_run.turns) != len(captured_run.turns)
        or capture_turn.turn_id != original.original_turn_id
        or sealed_turn.turn_id != original.original_turn_id
        or sealed_turn.model_dump(
            exclude={"head", "state", "attempts", "response_seal", "initialized_calls"}
        )
        != capture_turn.model_dump(
            exclude={"head", "state", "attempts", "response_seal", "initialized_calls"}
        )
        or len(sealed_turn.attempts) != len(capture_turn.attempts)
        or sealed_turn.attempts[:-1] != capture_turn.attempts[:-1]
        or capture_turn.state != "RESPONSE_AVAILABLE"
        or capture_turn.head
        != "execution-turn:" + capture_turn.model_copy(update={"head": "pending"}).digest()
        or capture_attempt.state != "RESPONSE_CAPTURED"
        or capture_attempt.head
        != "execution-attempt:" + capture_attempt.model_copy(update={"head": "pending"}).digest()
        or capture_attempt.receipt is None
        or not isinstance(parsed_response, ExecutionContinue)
        or parsed_response.canonical_bytes() != captured_response
        or len(parsed_response.tool_calls) != 1
        or parsed_response.tool_calls[0] != native_call
        or original.ordinal != 0
        or original.model_call_label != native_call.call_id
        or initialized.original_call_id != "call:" + original.digest()
        or capture_turn.response_seal is not None
        or capture_turn.initialized_calls is not None
        or sealed_turn.state != "ACCEPTED"
        or sealed_turn.head
        != "execution-turn:" + sealed_turn.model_copy(update={"head": "pending"}).digest()
        or sealed_attempt.state != "TERMINAL_ACCEPTED"
        or sealed_attempt.head
        != "execution-attempt:" + sealed_attempt.model_copy(update={"head": "pending"}).digest()
        or sealed_attempt.model_dump(exclude={"head", "state"})
        != capture_attempt.model_dump(exclude={"head", "state"})
        or sealed_turn.response_seal != request.selected_response
        or sealed_turn.initialized_calls != (request.initialized,)
        or original.captured_response != request.captured_run
        or response.tenant_id != request.tenant_id
        or original.tenant_id != request.tenant_id
        or response.original_run_id != original.original_run_id
        or response.original_turn_id != original.original_turn_id
        or response.captured_response != original.captured_response
        or response.complete_ordered_initialized != (request.initialized,)
        or request.acceptance.initialized != request.initialized.revision
        or terminal.original_call_id != initialized.original_call_id
        or terminal.original != original
        or terminal.initialized != request.initialized
        or terminal.not_executed_result_id != result.result_id
        or result.original_call_id != initialized.original_call_id
        or result.initialized != request.initialized
        or result.terminal != request.terminal
        or result.outcome != "NOT_EXECUTED"
    ):
        raise RecoveryHold("native cancellation direct-wire joins differ")

    frontier = TerminalCallFrontier(
        original_call_id=initialized.original_call_id,
        response_id=response.response_seal_id,
        acceptance=request.acceptance,
        disposition=CancelledBeforeAcceptResult(
            terminal=request.terminal.revision,
            not_executed_result=request.result.revision,
            initialized_predecessor=request.initialized.revision,
            recovered_outcome=NotApplicable(),
        ),
    )
    accounted = sealed_call_accounting(
        response.response_seal_id,
        response.digest(),
        (initialized.original_call_id,),
        (frontier,),
    )
    return PreparedNativeOneCallCancelledAccounting(
        source_request_fingerprint=hashlib.sha256(request.canonical_bytes()).hexdigest(),
        selected_response=request.selected_response,
        sealed_manifest_fingerprint=accounted.sealed_manifest_fingerprint,
        frontier_registry=request.frontier_registry,
        complete_ordered_calls=accounted.calls,
    )


def prepare_native_one_call_cancelled_accounting_v2(
    request: PrepareNativeOneCallCancelledAccountingV2,
) -> PreparedNativeOneCallCancelledAccountingV2:
    """Purely join V24's carried original-Prepare and cancellation evidence."""
    try:
        request = PrepareNativeOneCallCancelledAccountingV2.model_validate_json(
            request.canonical_bytes()
        )
        bindings = decode_native_execution_preparation_bindings_v2(
            request.preparation_bindings.canonical_bytes()
        )
        prepared_run = _decode_run(bindings.prepared_run_bytes, "prepared Run")
        captured_run = _decode_run(request.request.captured_run_bytes, "captured Run")
    except (ValidationError, ValueError, RecoveryHold) as error:
        raise RecoveryHold("invalid native preparation binding") from error
    if not isinstance(prepared_run, ExecutionRunRecord) or not isinstance(
        captured_run, ExecutionRunRecord
    ):
        raise RecoveryHold("native preparation requires physical Runs")
    if (
        len(prepared_run.turns) != 1
        or len(captured_run.turns) != 1
        or len(prepared_run.turns[0].attempts) != 1
        or len(captured_run.turns[0].attempts) != 1
        or prepared_run.turns[0].selector != 0
        or captured_run.turns[0].selector != 0
        or prepared_run.turns[0].turn_id != captured_run.turns[0].turn_id
        or prepared_run.turns[0].attempts[0].manifest != captured_run.turns[0].attempts[0].manifest
    ):
        raise RecoveryHold("native preparation manifest differs from captured Run")
    stamp = request.source_stamp
    if (
        request.request.tenant_id != stamp.tenant_id
        or bindings.tenant_id != stamp.tenant_id
        or bindings.database_identity != stamp.database_identity
        or bindings.prepared_run != _run_reference(prepared_run)
        or bindings.selected_response_seal != request.request.selected_response
        or prepared_run.run_id != captured_run.run_id
        or prepared_run.tenant != stamp.tenant_id
        or captured_run.tenant != stamp.tenant_id
        or prepared_run.policy != captured_run.policy
    ):
        raise RecoveryHold("native preparation binding differs from cancellation wire")
    result = prepare_native_one_call_cancelled_accounting(request.request)
    return PreparedNativeOneCallCancelledAccountingV2(
        source_request_fingerprint=hashlib.sha256(request.canonical_bytes()).hexdigest(),
        result=result,
    )
