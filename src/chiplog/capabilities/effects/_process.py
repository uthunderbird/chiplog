"""Isolated effects owner preparation; inert snapshots only, no storage or provider I/O."""

import base64

from .application import prepare_transition
from .contracts import (
    AcceptEffectCommand,
    AuthorizeDispatchCommand,
    BeforeSendDispositionCommand,
    CommitSendCommand,
    EffectCommand,
    EffectDenied,
    EffectPreparationRequest,
    PublishDeliveryIntentCommand,
    PublishPlanEffectCommand,
    PublishRecoveryIntentCommand,
    ReconcileEffectCommand,
    RecordEvidenceCommand,
)
from .domain import EffectRuleViolation

ROUTES = (
    (
        "effects.prepare_transition",
        "broker",
        "effects",
        "chiplog.effects.prepare.v1",
        "chiplog.effects.prepared-publication.v1",
    ),
)


def _command(request: EffectPreparationRequest) -> EffectCommand:
    value = request.command_bytes
    match request.operation:
        case "effects.accept_call":
            return AcceptEffectCommand.model_validate_json(value)
        case "effects.publish_plan_effect":
            return PublishPlanEffectCommand.model_validate_json(value)
        case "effects.prepare_delivery":
            return PublishDeliveryIntentCommand.model_validate_json(value)
        case "effects.publish_recovery_intent":
            return PublishRecoveryIntentCommand.model_validate_json(value)
        case "effects.authorize":
            return AuthorizeDispatchCommand.model_validate_json(value)
        case "effects.before_send":
            return BeforeSendDispositionCommand.model_validate_json(value)
        case "effects.commit_send":
            return CommitSendCommand.model_validate_json(value)
        case "effects.record_evidence":
            return RecordEvidenceCommand.model_validate_json(value)
        case "effects.reconcile":
            return ReconcileEffectCommand.model_validate_json(value)


def dispatch(operation: str, payload: bytes) -> dict[str, object]:
    if operation != "effects.prepare_transition":
        return {"failure": "PROTOCOL_REJECTED", "reason": "unknown effects owner operation"}
    try:
        request = EffectPreparationRequest.model_validate_json(payload)
        if request.canonical_bytes() != payload:
            raise ValueError("noncanonical owner preparation request")
        command = _command(request)
        if command.canonical_bytes() != request.command_bytes:
            raise ValueError("noncanonical owner command bytes")
        prepared = prepare_transition(command, request.expected, request.current)
        return {
            "payload": base64.b64encode(prepared.canonical_bytes()).decode(),
            "schema_id": "chiplog.effects.prepared-publication.v1",
        }
    except EffectRuleViolation as error:
        denied = EffectDenied.model_validate(
            {
                "disposition": error.code,
                "command_id": request.current.command_id,
                "reason": str(error),
            }
        )
        return {
            "payload": base64.b64encode(denied.canonical_bytes()).decode(),
            "schema_id": "chiplog.effects.prepared-publication.v1",
        }
    except (ValueError, TypeError, IndexError) as error:
        return {"failure": "PROTOCOL_REJECTED", "reason": str(error)}
