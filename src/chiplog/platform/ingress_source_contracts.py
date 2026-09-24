"""Closed source-observation interpretation; decoding grants no source authority."""

from __future__ import annotations

import json
from typing import Literal, Protocol

from pydantic import model_validator

from chiplog.adapters.driven.ingress_retained_source import (
    RetainedSourceObservation,
    decode_retained_observation,
)

from ._ingress_contracts import Head, Identity, IngressDTO, ReceiptToken, SourceBinding, SourceClass
from .ingress_transition_contracts import RetainedIngressSource


class IngressSourceObservationV1(IngressDTO):
    schema_id: str
    source_class: SourceClass
    reader_id: Identity
    source_binding: SourceBinding
    source_head: Head
    original_identity: Identity
    original_bytes: bytes
    proof_head: Head

    @model_validator(mode="after")
    def _bind_source_class(self) -> IngressSourceObservationV1:
        if self.source_binding.source_class != self.source_class:
            raise ValueError("source binding class differs from source observation")
        return self

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()


class TelegramPushObservation(IngressSourceObservationV1):
    schema_id: Literal["chiplog.ingress.source.telegram-push.v1"] = (
        "chiplog.ingress.source.telegram-push.v1"
    )
    source_class: Literal["TELEGRAM_PUSH"] = "TELEGRAM_PUSH"
    reader_id: Literal["telegram-push-v1"] = "telegram-push-v1"


class TelegramPollObservation(IngressSourceObservationV1):
    schema_id: Literal["chiplog.ingress.source.telegram-poll.v1"] = (
        "chiplog.ingress.source.telegram-poll.v1"
    )
    source_class: Literal["TELEGRAM_POLL"] = "TELEGRAM_POLL"
    reader_id: Literal["telegram-poll-v1"] = "telegram-poll-v1"


class CliPeerObservation(IngressSourceObservationV1):
    schema_id: Literal["chiplog.ingress.source.cli-peer.v1"] = "chiplog.ingress.source.cli-peer.v1"
    source_class: Literal["CLI"] = "CLI"
    reader_id: Literal["cli-peer-v1"] = "cli-peer-v1"


class ProviderCallbackObservation(IngressSourceObservationV1):
    schema_id: Literal["chiplog.ingress.source.provider-callback.v1"] = (
        "chiplog.ingress.source.provider-callback.v1"
    )
    source_class: Literal["PROVIDER_CALLBACK"] = "PROVIDER_CALLBACK"
    reader_id: Literal["provider-callback-v1"] = "provider-callback-v1"


class ProviderPollObservation(IngressSourceObservationV1):
    schema_id: Literal["chiplog.ingress.source.provider-poll.v1"] = (
        "chiplog.ingress.source.provider-poll.v1"
    )
    source_class: Literal["PROVIDER_POLL"] = "PROVIDER_POLL"
    reader_id: Literal["provider-poll-v1"] = "provider-poll-v1"


class ReconciliationObservation(IngressSourceObservationV1):
    schema_id: Literal["chiplog.ingress.source.reconciliation.v1"] = (
        "chiplog.ingress.source.reconciliation.v1"
    )
    source_class: Literal["RECONCILIATION"] = "RECONCILIATION"
    reader_id: Literal["reconciliation-v1"] = "reconciliation-v1"


class ToolResultObservation(IngressSourceObservationV1):
    schema_id: Literal["chiplog.ingress.source.tool-result.v1"] = (
        "chiplog.ingress.source.tool-result.v1"
    )
    source_class: Literal["TOOL_RESULT"] = "TOOL_RESULT"
    reader_id: Literal["tool-result-v1"] = "tool-result-v1"


type SourceObservation = RetainedSourceObservation | IngressSourceObservationV1
SOURCE_ROWS: tuple[tuple[SourceClass, str, str, type[IngressSourceObservationV1] | None], ...] = (
    (
        "TELEGRAM_PUSH",
        "telegram-push-v1",
        "chiplog.ingress.source.telegram-push.v1",
        TelegramPushObservation,
    ),
    (
        "TELEGRAM_POLL",
        "telegram-poll-v1",
        "chiplog.ingress.source.telegram-poll.v1",
        TelegramPollObservation,
    ),
    ("CLI", "cli-peer-v1", "chiplog.ingress.source.cli-peer.v1", CliPeerObservation),
    (
        "CLI",
        "<root-deployed-retained-reader>",
        "chiplog.ingress.retained-source-observation.v1",
        None,
    ),
    (
        "PROVIDER_CALLBACK",
        "provider-callback-v1",
        "chiplog.ingress.source.provider-callback.v1",
        ProviderCallbackObservation,
    ),
    (
        "PROVIDER_POLL",
        "provider-poll-v1",
        "chiplog.ingress.source.provider-poll.v1",
        ProviderPollObservation,
    ),
    (
        "RECONCILIATION",
        "reconciliation-v1",
        "chiplog.ingress.source.reconciliation.v1",
        ReconciliationObservation,
    ),
    (
        "TOOL_RESULT",
        "tool-result-v1",
        "chiplog.ingress.source.tool-result.v1",
        ToolResultObservation,
    ),
)


def decode_ingress_source(
    retained: RetainedIngressSource,
    *,
    expected_class: SourceClass,
    expected_binding: SourceBinding,
    registered_reader_id: str,
) -> SourceObservation:
    """Interpret exact retained bytes after the caller selects a registered reader.

    This deliberately does not verify live source proof, authorize an ACK/release,
    or assert that the caller's binding has a deployed profile.
    """
    if (
        retained.reader_id != registered_reader_id
        or expected_binding.source_class != expected_class
    ):
        raise ValueError("retained reader or expected source class differs")
    if (
        expected_class == "CLI"
        and retained.schema_id == "chiplog.ingress.retained-source-observation.v1"
    ):
        observation = decode_retained_observation(retained.canonical_source_bytes)
        scope = json.loads(observation.signed_metadata_bytes)["value"]["scope"]
        if (scope["tenant_id"], scope["database_id"]) != (
            expected_binding.tenant_id,
            expected_binding.database_id,
        ):
            raise ValueError("retained source scope differs from expected binding")
        if observation.proof != retained.source:
            raise ValueError("retained source head differs from observation proof")
        return observation
    rows = [
        row
        for row in SOURCE_ROWS
        if row[:3] == (expected_class, registered_reader_id, retained.schema_id)
    ]
    if len(rows) != 1 or rows[0][3] is None:
        raise ValueError("unregistered ingress source class/schema/reader")
    cls = rows[0][3]
    assert cls is not None
    decoded_observation = cls.model_validate_json(retained.canonical_source_bytes)
    if decoded_observation.canonical_bytes() != retained.canonical_source_bytes:
        raise ValueError("noncanonical ingress source bytes")
    if (
        decoded_observation.source_binding != expected_binding
        or decoded_observation.source_head != retained.source
    ):
        raise ValueError("source observation differs from retained source/binding")
    return decoded_observation


class DestructiveIngressReceiver(Protocol):
    def receive_after_allocation(
        self, token: ReceiptToken, selected_allocation: Head
    ) -> tuple[bytes, RetainedIngressSource]: ...


class CursorIngressSource(Protocol):
    def request_after_selection(
        self, binding: SourceBinding, applied_cursor: Head, selected_request: Head
    ) -> RetainedIngressSource: ...
