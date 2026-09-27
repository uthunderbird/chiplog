"""Closed H1 delivery bindings are strict retained evidence references."""

import hashlib

import pytest
from pydantic import ValidationError

from chiplog.platform.h1_delivery_binding_contracts import H1DeliveryBinding


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _value() -> dict[str, str]:
    return {
        "deployment_id": "deployment",
        "database_id": "database",
        "database_genesis_digest": _digest(b"genesis"),
        "tenant_id": "tenant",
        "journal_instance_id": "journal",
        "closure_entry_id": _digest(b"entry"),
        "closure_payload_digest": _digest(b"payload"),
        "command_id": "command",
        "command_fingerprint": _digest(b"command"),
        "request_digest": _digest(b"request"),
    }


def test_h1_delivery_binding_has_fixed_closed_schema_and_roles() -> None:
    binding = H1DeliveryBinding.model_validate(_value())

    assert binding.schema_id == "chiplog.execution.h1-selected-delivery-binding.v1"
    assert binding.journal_role == "h1-delivery-evidence"
    assert binding.closure_schema_id == "chiplog.execution.h1-delivery-selection-closure.v1"
    with pytest.raises(ValidationError):
        H1DeliveryBinding.model_validate({**_value(), "unexpected": "value"})
    with pytest.raises(ValidationError):
        H1DeliveryBinding.model_validate({**_value(), "closure_entry_id": "not-a-digest"})
