"""Inert, canonical evidence for one reader-validated J7 current-scope exchange."""

from __future__ import annotations

import base64
from typing import Any, Literal

from pydantic import field_serializer, field_validator, model_validator

from chiplog.capabilities.deployment_trust.cli_custody_contracts import CliCustodyDTO, UInt64
from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
    CurrentHermeticExecutionScopeV1,
    ReadCurrentHermeticExecutionScopeV1,
)
from chiplog.platform.broker import PublicPortCall, PublicPortSuccess


def _encode_broker_payload(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _decode_broker_payload(value: object) -> bytes:
    if type(value) is not str:
        raise ValueError("broker canonical payload is not base64 text")
    try:
        decoded = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError) as error:
        raise ValueError("broker canonical payload is not base64") from error
    if _encode_broker_payload(decoded) != value:
        raise ValueError("broker canonical payload is noncanonical base64")
    return decoded


def _broker_dump(value: PublicPortCall | PublicPortSuccess) -> dict[str, Any]:
    dumped = value.model_dump(mode="python")
    payload = dumped.get("canonical_payload")
    if not isinstance(payload, bytes):  # pragma: no cover - broker DTO enforces this
        raise ValueError("broker canonical payload is absent")
    dumped["canonical_payload"] = _encode_broker_payload(payload)
    return dumped


def _broker_load(value: object) -> object:
    if not isinstance(value, dict):
        return value
    dumped = dict(value)
    dumped["canonical_payload"] = _decode_broker_payload(dumped.get("canonical_payload"))
    if isinstance(dumped.get("held_resources"), list):
        dumped["held_resources"] = tuple(dumped["held_resources"])
    return dumped


class H1J7CurrentScopeExchangeV1(CliCustodyDTO):
    """A current exchange observation.  Possession conveys no delivery authority."""

    schema_id: Literal["chiplog.composition.h1-j7-current-scope-exchange.v1"] = (
        "chiplog.composition.h1-j7-current-scope-exchange.v1"
    )
    request: ReadCurrentHermeticExecutionScopeV1
    result: CurrentHermeticExecutionScopeV1
    sent: PublicPortCall
    returned: PublicPortSuccess
    sent_at_ns: UInt64
    returned_at_ns: UInt64

    @field_validator("sent", "returned", mode="before")
    @classmethod
    def _decode_broker_envelope(cls, value: object) -> object:
        return _broker_load(value)

    @field_serializer("sent", "returned", when_used="json")
    def _encode_broker_envelope(self, value: PublicPortCall | PublicPortSuccess) -> dict[str, Any]:
        return _broker_dump(value)

    @model_validator(mode="after")
    def _exact_exchange(self) -> H1J7CurrentScopeExchangeV1:
        if (
            self.result.source_anchor != self.request.source_anchor
            or self.result.scope_ref != self.request.expected_scope_ref
            or self.returned.request_id != self.sent.request_id
            or self.returned.responder != self.sent.callee
            or self.returned_at_ns < self.sent_at_ns
            or self.returned_at_ns >= self.sent.budget.absolute_deadline_ns
        ):
            raise ValueError("current scope exchange differs from its exact wire")
        return self
