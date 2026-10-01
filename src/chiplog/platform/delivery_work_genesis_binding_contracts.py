"""Retained selected-V4 binding for a delivery-work genesis decision."""

from typing import Literal

from chiplog.capabilities.agent_loop.delivery_work_contracts import DeliveryWorkSourceV1
from chiplog.platform._owner_publication_contracts import BrokerDTO, Digest, Identity


class DeliveryWorkGenesisBindingV1(BrokerDTO):
    schema_id: Literal["chiplog.delivery-work-genesis-binding.v1"] = (
        "chiplog.delivery-work-genesis-binding.v1"
    )
    selected_decision_id: Identity
    selected_decision_fingerprint: Digest
    selected_source: DeliveryWorkSourceV1
    request_digest: Digest
