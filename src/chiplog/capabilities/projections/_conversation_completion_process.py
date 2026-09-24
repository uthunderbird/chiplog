"""Isolated H1 route for deterministic accepted conversation preparation."""

from __future__ import annotations

import base64

from . import _r7_process
from .conversation_completion_owner import ConversationCompletionOwner
from .conversation_preparation_contracts import PrepareConversationCompletionV1

OPERATION = "projections.prepare_conversation_completion"
REQUEST_SCHEMA = "chiplog.conversation.prepare-completion.v1"
RESULT_SCHEMA = "chiplog.conversation.prepared-completion-result.v1"
ROUTES = tuple(
    sorted(
        (*_r7_process.ROUTES, (OPERATION, "broker", "projections", REQUEST_SCHEMA, RESULT_SCHEMA))
    )
)


def dispatch(operation: str, payload: bytes) -> dict[str, object]:
    if operation != OPERATION:
        return _r7_process.dispatch(operation, payload)
    try:
        request = PrepareConversationCompletionV1.model_validate_json(payload)
        if request.canonical_json_bytes() != payload:
            raise ValueError("payload is not canonical")
        result = ConversationCompletionOwner().prepare_completion(request)
        return {
            "payload": base64.b64encode(result.canonical_json_bytes()).decode(),
            "schema_id": RESULT_SCHEMA,
        }
    except (ValueError, TypeError) as error:
        return {"failure": "PROTOCOL_REJECTED", "reason": str(error)}
