"""Native completion acceptance and its distinct durable outbound delivery."""

from typing import Literal

from .delivery_contracts import AcceptedDelivery, DeliveryDTO, ExactHead
from .delivery_preparation import DeliveryAcceptanceProposal


class NativeCompletionDTO(DeliveryDTO):
    schema_id: str
    head: str
    tenant_id: str
    principal_id: str


class NativeCompletionAcceptanceV1(NativeCompletionDTO):
    schema_id: Literal["chiplog.completion.native-acceptance.v1"] = (
        "chiplog.completion.native-acceptance.v1"
    )
    run_id: str
    source_run_head: str
    worker_at_acceptance: ExactHead
    proposal: DeliveryAcceptanceProposal


class NativeDeliveryIntentV1(NativeCompletionDTO):
    schema_id: Literal["chiplog.delivery.native-intent.v1"] = "chiplog.delivery.native-intent.v1"
    delivery_id: str
    acceptance_head: str
    delivery: AcceptedDelivery
    receipt_key_fingerprint: str


class NativeDeliverySendV1(NativeCompletionDTO):
    schema_id: Literal["chiplog.delivery.native-send.v1"] = "chiplog.delivery.native-send.v1"
    delivery_id: str
    intent_head: str
    ticket_bytes: bytes
    gate_request_bytes: bytes


class NativeDeliveryClaimV1(NativeCompletionDTO):
    schema_id: Literal["chiplog.delivery.native-claim.v1"] = "chiplog.delivery.native-claim.v1"
    delivery_id: str
    send_head: str
    worker: str


class NativeDeliveryReceiptV1(NativeCompletionDTO):
    schema_id: Literal["chiplog.delivery.native-receipt.v1"] = "chiplog.delivery.native-receipt.v1"
    delivery_id: str
    send_head: str
    raw_receipt: bytes
    outcome: Literal["CONFIRMED", "FAILED_NO_EFFECT"]
