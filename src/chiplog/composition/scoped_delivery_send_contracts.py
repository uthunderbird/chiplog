"""Public inert SEND result wire for the prepared-delivery scoped V3 route."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Annotated, Final, Literal

from pydantic import Field, TypeAdapter, model_validator

from chiplog.adapters.driven.effects_hermetic import IssuedEffectSendTicket
from chiplog.capabilities.effects.contracts import ExactHead, TransmissionAttempt
from chiplog.capabilities.effects.dispatch_authority_contracts import (
    Digest,
    DispatchObservationDTO,
    Identity,
)
from chiplog.capabilities.effects.lifecycle_transition_contracts import SelectedEffectsSource
from chiplog.capabilities.effects.scoped_delivery_source_contracts import (
    decode_selected_delivery_first_send,
    decode_selected_delivery_intent,
)
from chiplog.capabilities.effects.scoped_dispatch_contracts import ScopedFirstSendParent
from chiplog.capabilities.effects.scoped_intent_record_contracts import (
    SCHEMA_ID as INTENT_SCHEMA_ID,
)
from chiplog.composition.r16_effects_inputs import canonical as ticket_canonical
from chiplog.composition.scoped_delivery_atomic_outcome_contracts import (
    ScopedDeliveryAtomicOutcomeV3,
)

SCOPED_V3_CONSUMPTION_SCHEMA: Final = "chiplog.broker-dispatch.scoped-send-consumption.v3"
SCOPED_V3_CHILD_SCHEMA: Final = "chiplog.effects.scoped-first-send-child.v3"
SCOPED_V3_PARENT_SCHEMA: Final = "chiplog.effects.scoped-first-send-parent.v3"
SCOPED_V3_CONSUME_SCHEMA: Final = "chiplog.broker-dispatch.consume-scoped-send.v3"
SCOPED_V3_REGISTRY: Final = ExactHead(
    subject_id="broker-dispatch.scoped-v3.registry",
    head="broker-dispatch.scoped-v3.registry/v1",
    fingerprint=hashlib.sha256(b"scoped-v3-selected-child-once;replay-never-emits").hexdigest(),
)


def _ticket_from_bytes(raw: bytes) -> IssuedEffectSendTicket:
    try:
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise TypeError("ticket is not an object")
        for field in ("canonical_address", "payload"):
            encoded = value.get(field)
            if (
                not isinstance(encoded, dict)
                or set(encoded) != {"exact_hex"}
                or not isinstance(encoded["exact_hex"], str)
            ):
                raise TypeError("ticket bytes lack exact_hex encoding")
            value[field] = bytes.fromhex(encoded["exact_hex"])
        if not isinstance(value.get("bundle_members"), list) or not all(
            isinstance(item, str) for item in value["bundle_members"]
        ):
            raise TypeError("ticket bundle members are not exact strings")
        value["bundle_members"] = tuple(value["bundle_members"])
        ticket = IssuedEffectSendTicket(**value)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("scoped V3 ticket bytes are not a canonical issued ticket") from error
    if ticket_canonical(asdict(ticket)) != raw:
        raise ValueError("scoped V3 ticket bytes are not canonical issued ticket bytes")
    if type(ticket.request_ordinal) is not int or ticket.request_ordinal < 0:
        raise ValueError("scoped V3 ticket ordinal is not a nonnegative integer")
    return ticket


def _consume_command_id(tenant_id: str, transmission: ExactHead) -> str:
    raw = json.dumps(
        [tenant_id, transmission.model_dump(mode="json")],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    return "consume-send/" + hashlib.sha256(raw).hexdigest()


class ScopedDeliverySendRequestV3(DispatchObservationDTO):
    """Four caller locators; composition obtains authority and current inputs."""

    kind: Literal["SEND_SELECTED_SCOPED_DELIVERY_V3"] = "SEND_SELECTED_SCOPED_DELIVERY_V3"
    peer: Identity
    intent_id: Identity
    act_id: Identity
    worker_run_id: Identity


class _ExactSelectedEffectsSource(SelectedEffectsSource):
    @model_validator(mode="after")
    def _preserve_selected_bytes(self) -> _ExactSelectedEffectsSource:
        if (
            self.subject != self.physical_record
            or hashlib.sha256(self.canonical_record_bytes).hexdigest()
            != self.physical_record.fingerprint
        ):
            raise ValueError("selected effects source does not preserve physical bytes")
        return self


class ScopedDeliveryIntentSourceV3(_ExactSelectedEffectsSource):
    owner: Literal["effects"] = "effects"
    schema_id: Literal["chiplog.effects.external-action-intent.v3"] = INTENT_SCHEMA_ID


class ScopedFirstSendDecisionSourceV3(_ExactSelectedEffectsSource):
    owner: Literal["effects"] = "effects"
    schema_id: Literal["chiplog.effects.scoped-first-send-decision.v3"] = (
        "chiplog.effects.scoped-first-send-decision.v3"
    )


class SelectedScopedTransmissionAttemptV3(DispatchObservationDTO):
    """One exact physical ordinal-zero child, decoded from retained bytes."""

    owner: Literal["effects"] = "effects"
    schema_id: Literal["chiplog.effects.scoped-first-send-child.v3"] = SCOPED_V3_CHILD_SCHEMA
    subject: ExactHead
    canonical_record_bytes: bytes = Field(min_length=1)
    selected_decision: ExactHead
    physical_record: ExactHead
    record: TransmissionAttempt

    @model_validator(mode="after")
    def _decode_and_bind_child(self) -> SelectedScopedTransmissionAttemptV3:
        try:
            decoded = TransmissionAttempt.model_validate_json(self.canonical_record_bytes)
        except ValueError as error:
            raise ValueError(
                "selected V3 child bytes do not decode as TransmissionAttempt"
            ) from error
        if (
            decoded != self.record
            or self.canonical_record_bytes != decoded.canonical_bytes()
            or decoded.ordinal != 0
            or self.subject != self.physical_record
            or hashlib.sha256(self.canonical_record_bytes).hexdigest()
            != self.physical_record.fingerprint
        ):
            raise ValueError("selected V3 first child differs from retained ordinal-zero bytes")
        return self


class SelectedScopedFirstSendParentV3(DispatchObservationDTO):
    """Physical SEND_COMMITTED parent selected with the first decision and child."""

    owner: Literal["effects"] = "effects"
    schema_id: Literal["chiplog.effects.scoped-first-send-parent.v3"] = SCOPED_V3_PARENT_SCHEMA
    subject: ExactHead
    canonical_record_bytes: bytes = Field(min_length=1)
    selected_decision: ExactHead
    physical_record: ExactHead
    record: ScopedFirstSendParent

    @model_validator(mode="after")
    def _decode_and_bind_parent(self) -> SelectedScopedFirstSendParentV3:
        try:
            decoded = ScopedFirstSendParent.model_validate_json(self.canonical_record_bytes)
        except ValueError as error:
            raise ValueError("selected V3 first-send parent bytes are invalid") from error
        if (
            decoded != self.record
            or self.canonical_record_bytes != decoded.canonical_bytes()
            or self.subject != self.physical_record
            or hashlib.sha256(self.canonical_record_bytes).hexdigest()
            != self.physical_record.fingerprint
        ):
            raise ValueError("selected V3 first-send parent differs from physical bytes")
        return self


class ScopedV3ConsumeRequest(DispatchObservationDTO):
    """Separate V3 broker request for one exact selected scoped first SEND."""

    schema_id: Literal["chiplog.broker-dispatch.consume-scoped-send.v3"] = SCOPED_V3_CONSUME_SCHEMA
    tenant_id: Identity
    command_id: Identity
    selected_send: ExactHead
    send_record: ExactHead
    intent: ExactHead
    transmission: ExactHead
    ticket_bytes: bytes = Field(min_length=1)
    ticket_digest: Digest
    registry: ExactHead

    @model_validator(mode="after")
    def _bind_ticket_digest(self) -> ScopedV3ConsumeRequest:
        if hashlib.sha256(self.ticket_bytes).hexdigest() != self.ticket_digest:
            raise ValueError("scoped V3 ticket digest differs from exact ticket bytes")
        if self.registry != SCOPED_V3_REGISTRY:
            raise ValueError("scoped V3 consume request uses an unregistered broker registry")
        _ticket_from_bytes(self.ticket_bytes)
        return self


class ScopedV3BrokerConsumptionRecord(DispatchObservationDTO):
    schema_id: Literal["chiplog.broker-dispatch.scoped-send-consumption.v3"] = (
        SCOPED_V3_CONSUMPTION_SCHEMA
    )
    kind: Literal["SCOPED_V3_SEND_CONSUMED"] = "SCOPED_V3_SEND_CONSUMED"
    state: Literal["CONSUMED"] = "CONSUMED"
    request: ScopedV3ConsumeRequest


class ScopedV3BrokerConsumptionSource(DispatchObservationDTO):
    owner: Literal["broker_dispatch"] = "broker_dispatch"
    schema_id: Literal["chiplog.broker-dispatch.scoped-send-consumption.v3"] = (
        SCOPED_V3_CONSUMPTION_SCHEMA
    )
    subject: ExactHead
    canonical_record_bytes: bytes = Field(min_length=1)
    selected_decision: ExactHead
    physical_record: ExactHead
    record: ScopedV3BrokerConsumptionRecord

    @model_validator(mode="after")
    def _bind_selected_record(self) -> ScopedV3BrokerConsumptionSource:
        if (
            self.subject != self.physical_record
            or self.canonical_record_bytes != self.record.canonical_bytes()
            or hashlib.sha256(self.canonical_record_bytes).hexdigest()
            != self.physical_record.fingerprint
            or self.subject.subject_id != self.record.request.command_id
        ):
            raise ValueError("broker consumption source differs from its retained V3 record")
        return self


ScopedDeliverySendFailureReasonV3 = Literal[
    "DENIED", "INVALID_SELECTED_SOURCE", "SOURCE_UNAVAILABLE", "STALE", "CONFLICT"
]
ScopedDeliverySendUncertainStageV3 = Literal[
    "DURABLE_SELECTION", "DURABLE_CONSUMPTION", "PUBLICATION", "REPLY"
]


class ScopedDeliverySendReceiptV3(DispatchObservationDTO):
    request: ScopedDeliverySendRequestV3
    selected_intent: ScopedDeliveryIntentSourceV3
    first_send_decision: ScopedFirstSendDecisionSourceV3
    first_send_child: SelectedScopedTransmissionAttemptV3
    first_send_parent: SelectedScopedFirstSendParentV3
    consumption: ScopedV3BrokerConsumptionSource
    outcome: ScopedDeliveryAtomicOutcomeV3

    @model_validator(mode="after")
    def _bind_consumption_to_exact_first_send(self) -> ScopedDeliverySendReceiptV3:
        intent = decode_selected_delivery_intent(self.selected_intent)
        first_send = decode_selected_delivery_first_send(
            self.first_send_decision, selected_intent=self.selected_intent
        )
        child, consume = self.first_send_child.record, self.consumption.record.request
        parent = self.first_send_parent.record
        ticket, mandate, recipient = (
            _ticket_from_bytes(consume.ticket_bytes),
            intent.mandate,
            intent.mandate.recipient,
        )
        if self.outcome.kind == "UNKNOWN":
            observed_parent = (
                self.outcome.parent.record if self.outcome.parent is not None else None
            )
            observed_obligation = (
                self.outcome.obligation.record if self.outcome.obligation is not None else None
            )
            observed_reduction = (
                self.outcome.reduction.record if self.outcome.reduction is not None else None
            )
            outcome_matches = (
                (observed_parent is None or (
                    observed_parent.original_intent == self.selected_intent.subject
                    and child.transmission in observed_parent.complete_ordered_children
                ))
                and (observed_obligation is None or (
                    observed_obligation.original_intent == self.selected_intent.subject
                    and observed_obligation.original_send == self.first_send_decision.subject
                    and child.transmission in observed_obligation.complete_ordered_children
                ))
                and (observed_reduction is None or (
                    observed_reduction.original_intent == self.selected_intent.subject
                    and child.transmission in observed_reduction.complete_ordered_children
                    and self.outcome.parent is not None
                    and observed_reduction.current_parent == self.outcome.parent.subject
                ))
                and (observed_parent is None or observed_obligation is None or (
                    observed_parent.complete_ordered_children
                    == observed_obligation.complete_ordered_children
                    and (
                        not isinstance(observed_parent.original_obligation, ExactHead)
                        or observed_parent.original_obligation == observed_obligation.obligation
                    )
                ))
                and (observed_parent is None or observed_reduction is None or (
                    observed_parent.complete_ordered_children
                    == observed_reduction.complete_ordered_children
                    and observed_parent.complete_ordered_evidence
                    == observed_reduction.complete_ordered_evidence
                ))
                and (observed_obligation is None or observed_reduction is None or (
                    observed_reduction.original_obligation == observed_obligation.obligation
                ))
            )
        else:
            outcome_matches = (
                self.outcome.original_intent == self.selected_intent.subject
                and self.outcome.original_send == self.first_send_decision.subject
                and self.outcome.parent.record.complete_ordered_children[:1]
                == (child.transmission,)
            )
        if (
            self.first_send_decision.selected_decision != consume.selected_send
            or self.first_send_child.selected_decision != consume.selected_send
            or self.first_send_parent.selected_decision != consume.selected_send
            or parent.original_intent != self.selected_intent.subject
            or parent.send_decision != self.first_send_decision.subject
            or parent.first_child != self.first_send_child.subject
            or parent.predecessor != first_send.prior_parent
            or child.intent != self.selected_intent.subject
            or child.send_commit != self.first_send_decision.subject
            or child.ordinal != 0
            or consume.send_record != self.first_send_decision.subject
            or consume.intent != self.selected_intent.subject
            or consume.transmission != child.transmission
            or consume.command_id != _consume_command_id(consume.tenant_id, child.transmission)
            or self.consumption.subject.subject_id != consume.command_id
            or self.selected_intent.subject.subject_id != self.request.intent_id
            or intent.intent_id != self.request.intent_id
            or first_send.identity.command_id != self.request.act_id
            or ticket.tenant_id != consume.tenant_id
            or consume.tenant_id != mandate.tenant_id
            or ticket.issued_operation_id != consume.command_id
            or ticket.journal_decision_id != consume.selected_send.subject_id
            or ticket.journal_decision_fingerprint != consume.selected_send.fingerprint
            or ticket.transmission_id != child.transmission.subject_id
            or ticket.transmission_fingerprint != child.transmission.fingerprint
            or ticket.request_ordinal != child.ordinal
            or ticket.intent_id != intent.intent_id
            or ticket.intent_fingerprint != intent.fingerprint
            or ticket.provider != recipient.provider
            or ticket.account != recipient.account
            or ticket.recipient != recipient.recipient
            or ticket.canonical_address != recipient.canonical_address
            or ticket.endpoint_head != recipient.endpoint.head
            or ticket.credential_binding_head != recipient.credential_binding.head
            or ticket.adapter_contract_version != mandate.semantics.adapter_contract_version
            or ticket.payload != mandate.payload
            or ticket.payload_fingerprint != mandate.effect_fingerprint
            or ticket.idempotency_key != mandate.idempotency_fence_key
            or ticket.bundle_members
            != tuple(member.subject_id for member in mandate.bundle_members)
            or child.semantics != mandate.semantics
            or child.payload_fingerprint != mandate.effect_fingerprint
            or child.recipient != recipient
            or child.idempotency_fence_key != mandate.idempotency_fence_key
            or not outcome_matches
        ):
            raise ValueError("consumption does not bind this selected V3 intent and first SEND")
        return self


class ScopedDeliverySendCommittedV3(DispatchObservationDTO):
    kind: Literal["COMMITTED"] = "COMMITTED"
    receipt: ScopedDeliverySendReceiptV3


class ScopedDeliverySendReplayedV3(DispatchObservationDTO):
    kind: Literal["REPLAYED"] = "REPLAYED"
    receipt: ScopedDeliverySendReceiptV3


class ScopedDeliverySendRejectedV3(DispatchObservationDTO):
    kind: Literal["REJECTED"] = "REJECTED"
    intent_id: Identity
    act_id: Identity
    reason: ScopedDeliverySendFailureReasonV3


class ScopedDeliverySendUncertainV3(DispatchObservationDTO):
    kind: Literal["UNCERTAIN"] = "UNCERTAIN"
    intent_id: Identity
    act_id: Identity
    reason: ScopedDeliverySendFailureReasonV3
    stage: ScopedDeliverySendUncertainStageV3


type ScopedDeliverySendResultV3 = Annotated[
    ScopedDeliverySendCommittedV3
    | ScopedDeliverySendReplayedV3
    | ScopedDeliverySendRejectedV3
    | ScopedDeliverySendUncertainV3,
    Field(discriminator="kind"),
]
SCOPED_DELIVERY_SEND_RESULT_V3: TypeAdapter[ScopedDeliverySendResultV3] = TypeAdapter(
    ScopedDeliverySendResultV3
)
