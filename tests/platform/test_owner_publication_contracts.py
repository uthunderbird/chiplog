"""Broker-internal shape tests; public capability consumers use their own ports."""

import hashlib

import pytest
from pydantic import TypeAdapter, ValidationError

from chiplog.platform._owner_publication_contracts import (
    BrokerOperation,
    BrokerTransportObservationAuthentication,
    OwnerCommandBytes,
    PublicationAuthentication,
    RegisteredPublication,
    SingleOwnerBatch,
)


def test_closed_batch_variants_separate_multiowner_publication() -> None:
    schema = TypeAdapter(RegisteredPublication).json_schema()
    assert set(schema["discriminator"]["mapping"]) == {
        "SINGLE_OWNER",
        "PLAN_EFFECT_ATOMIC",
        "CALL_EFFECT_ATOMIC",
        "COMPLETE_DELIVERY_ATOMIC",
    }
    # Acceptance and completion must not enter the single-owner escape hatch.
    for operation in (
        "effects.accept_call",
        "effects.prepare_delivery",
        "effects.publish_plan_effect",
        "agent_loop.complete_acceptance",
        "arbitrary.sql",
    ):
        with pytest.raises(ValidationError):
            TypeAdapter(BrokerOperation).validate_python(operation)
    single = SingleOwnerBatch.model_fields
    assert "admission_guard" not in single and "connection" not in single


@pytest.mark.parametrize(
    "operation", ["scheduler.genesis", "scheduler.amend_schedule", "scheduler.amend_policy"]
)
def test_scheduler_configuration_uses_registered_owner_publication(operation: str) -> None:
    assert TypeAdapter(BrokerOperation).validate_python(operation) == operation


def test_owner_command_codec_preserves_every_byte_without_utf8_assumption() -> None:
    raw = bytes(range(256))
    command = OwnerCommandBytes(
        owner="effects",
        schema_id="effects.command.v1",
        canonical_bytes=raw,
        fingerprint=hashlib.sha256(raw).hexdigest(),
    )
    assert OwnerCommandBytes.model_validate_json(command.model_dump_json()) == command
    with pytest.raises(ValidationError):
        OwnerCommandBytes.model_validate({**command.model_dump(), "owner": "unknown"})


def test_late_broker_transport_observation_does_not_borrow_live_worker_lease() -> None:
    head = {
        "owner": "effects",
        "record_kind": "TRANSMISSION",
        "subject_id": "transmission",
        "record_id": "child",
        "fingerprint": "a" * 64,
    }
    observation = BrokerTransportObservationAuthentication.model_validate(
        {
            "invocation": {
                "issuance_id": "issued",
                "issuance_fingerprint": "b" * 64,
                "broker_epoch": "epoch",
                "broker_session": "session",
                "runtime_generation": "generation",
                "operation_subject": "observation",
            },
            "issued_operation": head,
            "transmission": head,
            "originating_broker_epoch": "original-epoch",
            "recipient_binding_fingerprint": "c" * 64,
            "adapter_contract_version": "adapter.v1",
            "observation_fingerprint": "d" * 64,
        }
    )
    adapter: TypeAdapter[PublicationAuthentication] = TypeAdapter(PublicationAuthentication)
    assert adapter.validate_json(observation.model_dump_json()) == observation
    for omitted in ("issued_operation", "transmission", "recipient_binding_fingerprint"):
        with pytest.raises(ValidationError):
            adapter.validate_python(
                {key: item for key, item in observation.model_dump().items() if key != omitted}
            )
