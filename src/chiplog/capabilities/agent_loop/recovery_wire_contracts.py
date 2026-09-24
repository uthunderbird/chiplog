"""Closed external recovery-wire decoders.

The schema identifiers here name the external exchange family.  They are not
serialized into, and do not change, the nested owner DTO bodies.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from pydantic import TypeAdapter, ValidationError

from .call_acceptance_contracts import CallPreparationRejected
from .execution_recovery_contracts import ExecutionRecoveryRequest, ExecutionRecoveryResult
from .model_attempt_recovery_contracts import (
    ModelAttemptRecoveryRequest,
    ModelAttemptRecoveryResult,
)
from .original_recovery_contracts import OriginalRecoveryRequest, OriginalRecoveryResult
from .post_terminal_contracts import (
    PostTerminalWorkRequest,
    PostTerminalWorkResult,
    WorkPreparationRejected,
)
from .readonly_execution_contracts import ReadOnlyPreparationRequest, ReadOnlyPreparationResult
from .recovery_contracts import RecoveryDTO

EXECUTION_RECOVERY_REQUEST_SCHEMA = "chiplog.execution.recovery-request.v1"
EXECUTION_RECOVERY_RESULT_SCHEMA = "chiplog.execution.recovery-result.v1"
ORIGINAL_RECOVERY_REQUEST_SCHEMA = "chiplog.loop.original-recovery-request.v1"
ORIGINAL_RECOVERY_RESULT_SCHEMA = "chiplog.loop.original-recovery-result.v1"
READONLY_PREPARATION_REQUEST_SCHEMA = "chiplog.readonly.preparation-request.v1"
READONLY_PREPARATION_RESULT_SCHEMA = "chiplog.readonly.preparation-result.v1"
MODEL_RECOVERY_REQUEST_SCHEMA = "chiplog.execution.model-recovery-request.v1"
MODEL_RECOVERY_RESULT_SCHEMA = "chiplog.execution.model-recovery-result.v1"
POST_TERMINAL_PREPARATION_REQUEST_SCHEMA = "chiplog.post-terminal.preparation-request.v1"
POST_TERMINAL_PREPARATION_RESULT_SCHEMA = "chiplog.post-terminal.preparation-result.v1"


class RecoveryWireIntegrityError(ValueError):
    """Supplied exchange schema, bytes, or request/result relation is invalid."""


@dataclass(frozen=True)
class DecodedRecoveryWire[T]:
    """A decoded owner DTO retaining the exact canonical bytes supplied by the caller."""

    schema_id: str
    canonical_bytes: bytes
    value: T


@dataclass(frozen=True)
class RecoveryWireFamily[T, U]:
    request_schema: str
    result_schema: str
    request_type: object
    result_type: object


@dataclass(frozen=True)
class RecoveryWireDecoderRow:
    """One closed external family and its public, fixed decoders."""

    request_schema: str
    result_schema: str
    request_union: object
    result_union: object
    request_decoder: Callable[[str, bytes], object]
    result_decoder: Callable[[str, bytes], object]


EXECUTION_RECOVERY_FAMILY = RecoveryWireFamily[ExecutionRecoveryRequest, ExecutionRecoveryResult](
    EXECUTION_RECOVERY_REQUEST_SCHEMA,
    EXECUTION_RECOVERY_RESULT_SCHEMA,
    ExecutionRecoveryRequest,
    ExecutionRecoveryResult,
)
ORIGINAL_RECOVERY_FAMILY = RecoveryWireFamily[OriginalRecoveryRequest, OriginalRecoveryResult](
    ORIGINAL_RECOVERY_REQUEST_SCHEMA,
    ORIGINAL_RECOVERY_RESULT_SCHEMA,
    OriginalRecoveryRequest,
    OriginalRecoveryResult,
)
READONLY_PREPARATION_FAMILY = RecoveryWireFamily[
    ReadOnlyPreparationRequest, ReadOnlyPreparationResult
](
    READONLY_PREPARATION_REQUEST_SCHEMA,
    READONLY_PREPARATION_RESULT_SCHEMA,
    ReadOnlyPreparationRequest,
    ReadOnlyPreparationResult,
)
MODEL_RECOVERY_FAMILY = RecoveryWireFamily[ModelAttemptRecoveryRequest, ModelAttemptRecoveryResult](
    MODEL_RECOVERY_REQUEST_SCHEMA,
    MODEL_RECOVERY_RESULT_SCHEMA,
    ModelAttemptRecoveryRequest,
    ModelAttemptRecoveryResult,
)
POST_TERMINAL_PREPARATION_FAMILY = RecoveryWireFamily[
    PostTerminalWorkRequest, PostTerminalWorkResult
](
    POST_TERMINAL_PREPARATION_REQUEST_SCHEMA,
    POST_TERMINAL_PREPARATION_RESULT_SCHEMA,
    PostTerminalWorkRequest,
    PostTerminalWorkResult,
)

_EXECUTION_REQUEST_ADAPTER: TypeAdapter[ExecutionRecoveryRequest] = TypeAdapter(
    ExecutionRecoveryRequest
)
_EXECUTION_RESULT_ADAPTER: TypeAdapter[ExecutionRecoveryResult] = TypeAdapter(
    ExecutionRecoveryResult
)
_ORIGINAL_REQUEST_ADAPTER: TypeAdapter[OriginalRecoveryRequest] = TypeAdapter(
    OriginalRecoveryRequest
)
_ORIGINAL_RESULT_ADAPTER: TypeAdapter[OriginalRecoveryResult] = TypeAdapter(OriginalRecoveryResult)
_READONLY_REQUEST_ADAPTER: TypeAdapter[ReadOnlyPreparationRequest] = TypeAdapter(
    ReadOnlyPreparationRequest
)
_READONLY_RESULT_ADAPTER: TypeAdapter[ReadOnlyPreparationResult] = TypeAdapter(
    ReadOnlyPreparationResult
)
_MODEL_REQUEST_ADAPTER: TypeAdapter[ModelAttemptRecoveryRequest] = TypeAdapter(
    ModelAttemptRecoveryRequest
)
_MODEL_RESULT_ADAPTER: TypeAdapter[ModelAttemptRecoveryResult] = TypeAdapter(
    ModelAttemptRecoveryResult
)
_WORK_REQUEST_ADAPTER: TypeAdapter[PostTerminalWorkRequest] = TypeAdapter(PostTerminalWorkRequest)
_WORK_RESULT_ADAPTER: TypeAdapter[PostTerminalWorkResult] = TypeAdapter(PostTerminalWorkResult)

_SUCCESS_KIND_BY_REQUEST_KIND = {
    "PREPARE_EXECUTION_ACCOUNTING_V1": "PREPARED_EXECUTION_ACCOUNTING_V1",
    "PREPARE_EXECUTION_CONTINUATION_V1": "PREPARED_EXECUTION_CONTINUATION_V1",
    "PREPARE_EXECUTION_SUSPENSION_V2": "PREPARED_EXECUTION_SUSPENSION_V2",
    "PREPARE_EXECUTION_RESUME_V2": "PREPARED_EXECUTION_RESUME_V2",
    "PREPARE_EXECUTION_SUCCESSOR_V2": "PREPARED_EXECUTION_SUCCESSOR_V2",
    "PREPARE_NEXT_EXECUTION_TURN_V1": "PREPARED_NEXT_EXECUTION_TURN_V1",
    "PREPARE_ABORT_CANCEL_EXECUTION_V1": "PREPARED_EXECUTION_TERMINAL_V1",
    "PREPARE_ORIGINAL_CALL_RESOLUTION_V1": "PREPARED_ORIGINAL_CALL_RESOLUTION_V1",
    "PREPARE_LOOP_SEMANTIC_REDUCTION_V1": "PREPARED_LOOP_SEMANTIC_REDUCTION_V1",
    "PREPARE_READONLY_ATTEMPT_V1": "PREPARED_READONLY_ATTEMPT_V1",
    "PREPARE_READONLY_OUTCOME_V1": "PREPARED_READONLY_OUTCOME_V1",
    "PREPARE_READONLY_PENDING_V1": "PREPARED_READONLY_PENDING_V1",
    "PREPARE_READONLY_REDUCTION_V1": "PREPARED_READONLY_REDUCTION_V1",
    "REPLACE_EXECUTION_MODEL_ATTEMPT_V1": "PREPARED_MODEL_ATTEMPT_REPLACEMENT_V1",
    "RETAIN_LATE_EXECUTION_RESPONSE_V1": "PREPARED_LATE_EXECUTION_RESPONSE_V1",
    "PREPARE_TERMINAL_WORK_V1": "PREPARED_POST_TERMINAL_WORK_V1",
    "PREPARE_WORK_LEASE_V1": "PREPARED_POST_TERMINAL_WORK_V1",
    "PREPARE_WORK_ROLLOVER_V1": "PREPARED_POST_TERMINAL_WORK_V1",
    "PREPARE_WORK_CLOSE_V1": "PREPARED_POST_TERMINAL_WORK_V1",
}
_CLASSIFIABLE_EXECUTION_REQUEST_KINDS = {
    "PREPARE_EXECUTION_RESUME_V2",
    "PREPARE_EXECUTION_SUCCESSOR_V2",
}


def _decode[T: RecoveryDTO](
    schema_id: str, expected_schema: str, adapter: TypeAdapter[T], raw: bytes
) -> DecodedRecoveryWire[T]:
    if schema_id != expected_schema:
        raise RecoveryWireIntegrityError(f"unexpected recovery-wire schema {schema_id!r}")
    try:
        value = adapter.validate_json(raw)
    except (ValidationError, ValueError) as error:
        raise RecoveryWireIntegrityError(f"invalid {schema_id} recovery-wire body") from error
    if value.canonical_bytes() != raw:
        raise RecoveryWireIntegrityError(f"noncanonical {schema_id} recovery-wire body")
    return DecodedRecoveryWire(schema_id=schema_id, canonical_bytes=raw, value=value)


def decode_execution_recovery_request(
    schema_id: str, raw: bytes
) -> DecodedRecoveryWire[ExecutionRecoveryRequest]:
    return _decode(schema_id, EXECUTION_RECOVERY_REQUEST_SCHEMA, _EXECUTION_REQUEST_ADAPTER, raw)


def decode_execution_recovery_result(
    schema_id: str, raw: bytes
) -> DecodedRecoveryWire[ExecutionRecoveryResult]:
    return _decode(schema_id, EXECUTION_RECOVERY_RESULT_SCHEMA, _EXECUTION_RESULT_ADAPTER, raw)


def decode_original_recovery_request(
    schema_id: str, raw: bytes
) -> DecodedRecoveryWire[OriginalRecoveryRequest]:
    return _decode(schema_id, ORIGINAL_RECOVERY_REQUEST_SCHEMA, _ORIGINAL_REQUEST_ADAPTER, raw)


def decode_original_recovery_result(
    schema_id: str, raw: bytes
) -> DecodedRecoveryWire[OriginalRecoveryResult]:
    return _decode(schema_id, ORIGINAL_RECOVERY_RESULT_SCHEMA, _ORIGINAL_RESULT_ADAPTER, raw)


def decode_readonly_preparation_request(
    schema_id: str, raw: bytes
) -> DecodedRecoveryWire[ReadOnlyPreparationRequest]:
    return _decode(schema_id, READONLY_PREPARATION_REQUEST_SCHEMA, _READONLY_REQUEST_ADAPTER, raw)


def decode_readonly_preparation_result(
    schema_id: str, raw: bytes
) -> DecodedRecoveryWire[ReadOnlyPreparationResult]:
    return _decode(schema_id, READONLY_PREPARATION_RESULT_SCHEMA, _READONLY_RESULT_ADAPTER, raw)


def decode_model_recovery_request(
    schema_id: str, raw: bytes
) -> DecodedRecoveryWire[ModelAttemptRecoveryRequest]:
    return _decode(schema_id, MODEL_RECOVERY_REQUEST_SCHEMA, _MODEL_REQUEST_ADAPTER, raw)


def decode_model_recovery_result(
    schema_id: str, raw: bytes
) -> DecodedRecoveryWire[ModelAttemptRecoveryResult]:
    return _decode(schema_id, MODEL_RECOVERY_RESULT_SCHEMA, _MODEL_RESULT_ADAPTER, raw)


def decode_post_terminal_preparation_request(
    schema_id: str, raw: bytes
) -> DecodedRecoveryWire[PostTerminalWorkRequest]:
    return _decode(schema_id, POST_TERMINAL_PREPARATION_REQUEST_SCHEMA, _WORK_REQUEST_ADAPTER, raw)


def decode_post_terminal_preparation_result(
    schema_id: str, raw: bytes
) -> DecodedRecoveryWire[PostTerminalWorkResult]:
    return _decode(schema_id, POST_TERMINAL_PREPARATION_RESULT_SCHEMA, _WORK_RESULT_ADAPTER, raw)


RECOVERY_WIRE_DECODER_ROWS = (
    RecoveryWireDecoderRow(
        EXECUTION_RECOVERY_REQUEST_SCHEMA,
        EXECUTION_RECOVERY_RESULT_SCHEMA,
        ExecutionRecoveryRequest,
        ExecutionRecoveryResult,
        decode_execution_recovery_request,
        decode_execution_recovery_result,
    ),
    RecoveryWireDecoderRow(
        ORIGINAL_RECOVERY_REQUEST_SCHEMA,
        ORIGINAL_RECOVERY_RESULT_SCHEMA,
        OriginalRecoveryRequest,
        OriginalRecoveryResult,
        decode_original_recovery_request,
        decode_original_recovery_result,
    ),
    RecoveryWireDecoderRow(
        READONLY_PREPARATION_REQUEST_SCHEMA,
        READONLY_PREPARATION_RESULT_SCHEMA,
        ReadOnlyPreparationRequest,
        ReadOnlyPreparationResult,
        decode_readonly_preparation_request,
        decode_readonly_preparation_result,
    ),
    RecoveryWireDecoderRow(
        MODEL_RECOVERY_REQUEST_SCHEMA,
        MODEL_RECOVERY_RESULT_SCHEMA,
        ModelAttemptRecoveryRequest,
        ModelAttemptRecoveryResult,
        decode_model_recovery_request,
        decode_model_recovery_result,
    ),
    RecoveryWireDecoderRow(
        POST_TERMINAL_PREPARATION_REQUEST_SCHEMA,
        POST_TERMINAL_PREPARATION_RESULT_SCHEMA,
        PostTerminalWorkRequest,
        PostTerminalWorkResult,
        decode_post_terminal_preparation_request,
        decode_post_terminal_preparation_result,
    ),
)


def _revalidate[T: RecoveryDTO](
    wire: DecodedRecoveryWire[T], schema: str, adapter: TypeAdapter[T]
) -> None:
    verified = _decode(wire.schema_id, schema, adapter, wire.canonical_bytes)
    if verified.value != wire.value:
        raise RecoveryWireIntegrityError("decoded recovery-wire carrier does not match its bytes")


def _validate_exchange[T: RecoveryDTO, U: RecoveryDTO](
    request: DecodedRecoveryWire[T],
    result: DecodedRecoveryWire[U],
    request_schema: str,
    result_schema: str,
    request_adapter: TypeAdapter[T],
    result_adapter: TypeAdapter[U],
    *,
    allow_classification: bool = False,
    work_rejection: bool = False,
) -> None:
    _revalidate(request, request_schema, request_adapter)
    _revalidate(result, result_schema, result_adapter)
    request_value = cast(Any, request.value)
    result_value = cast(Any, result.value)
    request_kind = request_value.kind
    result_kind = result_value.kind
    if isinstance(result_value, WorkPreparationRejected):
        if work_rejection:
            return
        raise RecoveryWireIntegrityError("post-terminal rejection outside post-terminal family")
    if isinstance(result_value, CallPreparationRejected):
        if result_value.command_id != request_value.command_id:
            raise RecoveryWireIntegrityError("rejection command_id does not match request")
        return
    expected_kind = _SUCCESS_KIND_BY_REQUEST_KIND.get(request_kind)
    is_classification = allow_classification and result_kind == "EXECUTION_RECOVERY_CLASSIFIED_V1"
    if is_classification and request_kind not in _CLASSIFIABLE_EXECUTION_REQUEST_KINDS:
        raise RecoveryWireIntegrityError(
            "execution classification is not valid for this request kind"
        )
    if not is_classification and result_kind != expected_kind:
        raise RecoveryWireIntegrityError(
            f"result kind {result_kind!r} is incompatible with request kind {request_kind!r}"
        )
    fingerprint = getattr(result_value, "source_request_fingerprint", None)
    expected_fingerprint = hashlib.sha256(request.canonical_bytes).hexdigest()
    if fingerprint != expected_fingerprint:
        raise RecoveryWireIntegrityError(
            "result source_request_fingerprint does not bind request bytes"
        )


def validate_execution_recovery_exchange(
    request: DecodedRecoveryWire[ExecutionRecoveryRequest],
    result: DecodedRecoveryWire[ExecutionRecoveryResult],
) -> None:
    _validate_exchange(
        request,
        result,
        EXECUTION_RECOVERY_REQUEST_SCHEMA,
        EXECUTION_RECOVERY_RESULT_SCHEMA,
        _EXECUTION_REQUEST_ADAPTER,
        _EXECUTION_RESULT_ADAPTER,
        allow_classification=True,
    )


def validate_original_recovery_exchange(
    request: DecodedRecoveryWire[OriginalRecoveryRequest],
    result: DecodedRecoveryWire[OriginalRecoveryResult],
) -> None:
    _validate_exchange(
        request,
        result,
        ORIGINAL_RECOVERY_REQUEST_SCHEMA,
        ORIGINAL_RECOVERY_RESULT_SCHEMA,
        _ORIGINAL_REQUEST_ADAPTER,
        _ORIGINAL_RESULT_ADAPTER,
    )


def validate_readonly_preparation_exchange(
    request: DecodedRecoveryWire[ReadOnlyPreparationRequest],
    result: DecodedRecoveryWire[ReadOnlyPreparationResult],
) -> None:
    _validate_exchange(
        request,
        result,
        READONLY_PREPARATION_REQUEST_SCHEMA,
        READONLY_PREPARATION_RESULT_SCHEMA,
        _READONLY_REQUEST_ADAPTER,
        _READONLY_RESULT_ADAPTER,
    )


def validate_model_recovery_exchange(
    request: DecodedRecoveryWire[ModelAttemptRecoveryRequest],
    result: DecodedRecoveryWire[ModelAttemptRecoveryResult],
) -> None:
    _validate_exchange(
        request,
        result,
        MODEL_RECOVERY_REQUEST_SCHEMA,
        MODEL_RECOVERY_RESULT_SCHEMA,
        _MODEL_REQUEST_ADAPTER,
        _MODEL_RESULT_ADAPTER,
    )


def validate_post_terminal_preparation_exchange(
    request: DecodedRecoveryWire[PostTerminalWorkRequest],
    result: DecodedRecoveryWire[PostTerminalWorkResult],
) -> None:
    _validate_exchange(
        request,
        result,
        POST_TERMINAL_PREPARATION_REQUEST_SCHEMA,
        POST_TERMINAL_PREPARATION_RESULT_SCHEMA,
        _WORK_REQUEST_ADAPTER,
        _WORK_RESULT_ADAPTER,
        work_rejection=True,
    )
