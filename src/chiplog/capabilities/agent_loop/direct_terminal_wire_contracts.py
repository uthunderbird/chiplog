"""Private V30 wire family for the accepted-call direct terminal owner."""

from pydantic import TypeAdapter, ValidationError

from .call_acceptance_contracts import CallPreparationRejected
from .direct_terminal_contracts import (
    PrepareAcceptedDirectTerminalV1,
    PreparedAcceptedDirectTerminalV1,
)

REQUEST_SCHEMA = "chiplog.execution.direct-terminal-request.v1"
RESULT_SCHEMA = "chiplog.execution.direct-terminal-result.v1"
_REQUEST: TypeAdapter[PrepareAcceptedDirectTerminalV1] = TypeAdapter(
    PrepareAcceptedDirectTerminalV1
)
_DirectTerminalResult = PreparedAcceptedDirectTerminalV1 | CallPreparationRejected
_RESULT: TypeAdapter[_DirectTerminalResult] = TypeAdapter(_DirectTerminalResult)


def decode_request(schema_id: str, raw: bytes) -> PrepareAcceptedDirectTerminalV1:
    if schema_id != REQUEST_SCHEMA:
        raise ValueError("unexpected direct-terminal request schema")
    try:
        value = _REQUEST.validate_json(raw)
    except (ValidationError, ValueError) as error:
        raise ValueError("invalid direct-terminal request") from error
    if value.canonical_bytes() != raw:
        raise ValueError("noncanonical direct-terminal request")
    return value


def decode_result(
    schema_id: str, raw: bytes
) -> PreparedAcceptedDirectTerminalV1 | CallPreparationRejected:
    if schema_id != RESULT_SCHEMA:
        raise ValueError("unexpected direct-terminal result schema")
    try:
        value = _RESULT.validate_json(raw)
    except (ValidationError, ValueError) as error:
        raise ValueError("invalid direct-terminal result") from error
    if value.canonical_bytes() != raw:
        raise ValueError("noncanonical direct-terminal result")
    return value
