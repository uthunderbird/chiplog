"""Isolated scheduler owner handler. Receives inert bytes; no raw I/O or authority."""

import base64

from .scheduler_preparation import (
    BATCH_SCHEMA,
    PREPARED_SCHEMA,
    ConfigurationPreparationRequest,
    IntervalPreparationRequest,
    LeasePreparationRequest,
    RolloverPreparationRequest,
    prepare_configuration,
    prepare_interval_request,
    prepare_lease,
    prepare_rollover_request,
)

ROUTES = (
    (
        "scheduler.prepare_lease",
        "broker",
        "agent_loop",
        "chiplog.scheduler.lease-preparation.v1",
        "chiplog.scheduler.prepared-lease.v1",
    ),
    (
        "scheduler.prepare_configuration",
        "broker",
        "agent_loop",
        "chiplog.scheduler.configuration-preparation.v1",
        BATCH_SCHEMA,
    ),
    (
        "scheduler.prepare_interval",
        "broker",
        "agent_loop",
        "chiplog.scheduler.interval-preparation.v1",
        BATCH_SCHEMA,
    ),
    (
        "scheduler.prepare_rollover",
        "broker",
        "agent_loop",
        "chiplog.scheduler.rollover-preparation.v1",
        BATCH_SCHEMA,
    ),
)


def dispatch(operation: str, payload: bytes) -> dict[str, object]:
    try:
        if operation == "scheduler.prepare_lease":
            lease_request = LeasePreparationRequest.model_validate_json(payload)
            parsed = lease_request.canonical_bytes()
            prepared = prepare_lease(lease_request)
            result = prepared.canonical_bytes()
            schema = PREPARED_SCHEMA
        elif operation == "scheduler.prepare_configuration":
            configuration_request = ConfigurationPreparationRequest.model_validate_json(payload)
            parsed = configuration_request.canonical_bytes()
            result = prepare_configuration(configuration_request).canonical_bytes()
            schema = BATCH_SCHEMA
        elif operation == "scheduler.prepare_interval":
            interval_request = IntervalPreparationRequest.model_validate_json(payload)
            parsed = interval_request.canonical_bytes()
            result = prepare_interval_request(interval_request).canonical_bytes()
            schema = BATCH_SCHEMA
        elif operation == "scheduler.prepare_rollover":
            rollover_request = RolloverPreparationRequest.model_validate_json(payload)
            parsed = rollover_request.canonical_bytes()
            result = prepare_rollover_request(rollover_request).canonical_bytes()
            schema = BATCH_SCHEMA
        else:
            return {"failure": "PROTOCOL_REJECTED", "reason": "unknown scheduler owner operation"}
        if parsed != payload:
            raise ValueError("noncanonical scheduler preparation request")
        return {
            "payload": base64.b64encode(result).decode(),
            "schema_id": schema,
        }
    except (ValueError, TypeError, IndexError) as error:
        return {"failure": "PROTOCOL_REJECTED", "reason": str(error)}
