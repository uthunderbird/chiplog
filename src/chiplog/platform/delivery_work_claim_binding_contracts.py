"""Platform contracts for a broker-bound first delivery-work claim.

Construction is not authentication.  The future broker captures claimant and
clock values privately, then binds them here while it verifies the selected,
materialized g0 decision at its transaction cut.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import Field, model_validator

from chiplog.capabilities.agent_loop.delivery_work_contracts import (
    DeliveryWorkClaimProjectionV1,
    DeliveryWorkSourceV1,
    prepare_delivery_work_claim,
)
from chiplog.platform._owner_publication_contracts import (
    BrokerDTO,
    DeliveryWorkClaimBatchV1,
    Digest,
    Identity,
    decode_delivery_work_claim_command,
)


class DeliveryWorkClaimantClockBindingV1(BrokerDTO):
    """Describes values the future broker independently captures and verifies."""

    holder_id: Identity
    session_id: Identity
    broker_epoch: Identity
    runtime_generation: Identity
    clock_epoch: Identity
    lease_expires_at_ns: int = Field(ge=0)
    trusted_clock_ns: int = Field(ge=0)

    @model_validator(mode="after")
    def live_lease_interval(self) -> DeliveryWorkClaimantClockBindingV1:
        if self.lease_expires_at_ns <= self.trusted_clock_ns:
            raise ValueError("delivery work claim binding lease interval is not live")
        return self


def canonical_delivery_work_claim_batch_bytes(batch: DeliveryWorkClaimBatchV1) -> bytes:
    """Canonical request preimage for the external selected-binding digest."""
    return json.dumps(batch.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()


class DeliveryWorkClaimBindingV1(BrokerDTO):
    """Selected g0/V4 evidence plus broker-captured values for one exact g1 record."""

    schema_id: Literal["chiplog.delivery-work-claim-binding.v1"] = (
        "chiplog.delivery-work-claim-binding.v1"
    )
    genesis_decision_id: Identity
    genesis_decision_fingerprint: Digest
    selected_source: DeliveryWorkSourceV1
    claim_projection: DeliveryWorkClaimProjectionV1
    claimant_clock: DeliveryWorkClaimantClockBindingV1
    request_digest: Digest

    @model_validator(mode="after")
    def exact_selected_projection(self) -> DeliveryWorkClaimBindingV1:
        if self.selected_source != self.claim_projection.selected_source:
            raise ValueError("delivery work claim binding selected source differs")
        return self


def validate_delivery_work_claim_binding(
    batch: DeliveryWorkClaimBatchV1, binding: DeliveryWorkClaimBindingV1
) -> None:
    """Bind the selected evidence, canonical request, and descriptive g1 bytes exactly."""
    command = decode_delivery_work_claim_command(batch.operation, batch.claim_command)
    projection = command.projection
    if (
        binding.selected_source != projection.selected_source
        or binding.claim_projection != projection
        or binding.request_digest
        != hashlib.sha256(canonical_delivery_work_claim_batch_bytes(batch)).hexdigest()
    ):
        raise ValueError("delivery work claim selected binding differs from batch")
    expected = prepare_delivery_work_claim(
        binding.selected_source,
        binding.claim_projection.predecessor,
        holder_id=binding.claimant_clock.holder_id,
        session_id=binding.claimant_clock.session_id,
        lease_expires_at_ns=binding.claimant_clock.lease_expires_at_ns,
        trusted_clock_ns=binding.claimant_clock.trusted_clock_ns,
    )
    record = batch.complete_records[0]
    if record.canonical_bytes != expected.canonical_bytes():
        raise ValueError("delivery work claim record differs from broker binding")
