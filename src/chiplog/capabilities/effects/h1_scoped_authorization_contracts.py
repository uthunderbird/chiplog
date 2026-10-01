"""Inert H1 authorization request shapes, deliberately outside V2/V3 issuance.

These codecs establish only canonical structure and relations between submitted
descriptions. They do not authenticate a claim source, establish currentness,
open a writer, or grant publication or SEND authority.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal, Self

from pydantic import Field, model_validator

from chiplog.capabilities.agent_loop.delivery_work_contracts import DeliveryWorkStateV1

from .dispatch_authority_contracts import Digest, DispatchObservationDTO, Identity
from .h1_producer_source_contracts import (
    H1PreparedDeliveryMandateCandidateV1,
    H1ProducerNotApplicableV1,
    H1ProducerSourceInventoryV1,
)

_CURRENT_DOMAIN = b"chiplog.effects.h1-scoped-authorization-current.v1\x00"
_FENCE_DOMAIN = b"chiplog.effects.h1-delivery-work-claim-fence.v1\x00"
_REQUEST_DOMAIN = b"chiplog.effects.h1-scoped-authorization-request.v1\x00"


def _digest(domain: bytes, value: bytes) -> str:
    return hashlib.sha256(domain + value).hexdigest()


class H1ScopedAuthorizationCurrentV1(DispatchObservationDTO):
    """Fresh H1 candidate inputs, before any reader or writer authentication."""

    schema_id: Literal["chiplog.effects.h1-scoped-authorization-current.v1"] = (
        "chiplog.effects.h1-scoped-authorization-current.v1"
    )
    tenant_id: Identity
    intent_id: Identity
    command_id: Identity
    command_bytes: bytes = Field(min_length=1)
    command_fingerprint: Digest
    mandate_candidate: H1PreparedDeliveryMandateCandidateV1
    sources: H1ProducerSourceInventoryV1
    broker_epoch: Identity
    runtime_generation: Identity
    clock_epoch: Identity
    observed_at_ns: int = Field(ge=0, le=2**64 - 1)
    lease_expires_at_ns: int = Field(gt=0, le=2**64 - 1)

    @model_validator(mode="after")
    def exact_h1_shape(self) -> Self:
        if self.command_fingerprint != _digest(_CURRENT_DOMAIN, self.command_bytes):
            raise ValueError("H1 current command fingerprint differs")
        if self.observed_at_ns >= self.lease_expires_at_ns:
            raise ValueError("H1 current lease is not live")
        for role in ("planning", "original_adoption"):
            source = getattr(self.sources, role)
            if not isinstance(source, H1ProducerNotApplicableV1) or source.role != role:
                raise ValueError("H1 current requires its closed not-applicable source role")
            if source.candidate != self.mandate_candidate:
                raise ValueError("H1 current not-applicable candidate differs")
        return self


class H1DeliveryWorkClaimFenceV1(DispatchObservationDTO):
    """Descriptive selected-g1 claim fence; it is not a legacy ``WorkerFence``."""

    schema_id: Literal["chiplog.effects.h1-delivery-work-claim-fence.v1"] = (
        "chiplog.effects.h1-delivery-work-claim-fence.v1"
    )
    kind: Literal["H1_SELECTED_DELIVERY_WORK_CLAIM"] = "H1_SELECTED_DELIVERY_WORK_CLAIM"
    selected_claim_decision_id: Identity
    selected_claim_decision_fingerprint: Digest
    selected_g1_canonical_bytes: bytes = Field(min_length=1)
    selected_g1_record_fingerprint: Digest
    command_id: Identity
    command_fingerprint: Digest
    current_fingerprint: Digest
    holder_id: Identity
    session_id: Identity
    broker_epoch: Identity
    runtime_generation: Identity
    clock_epoch: Identity
    claimed_at_ns: int = Field(ge=0, le=2**64 - 1)
    lease_expires_at_ns: int = Field(gt=0, le=2**64 - 1)

    @model_validator(mode="after")
    def exact_claim_shape(self) -> Self:
        if (
            self.selected_g1_record_fingerprint
            != hashlib.sha256(self.selected_g1_canonical_bytes).hexdigest()
        ):
            raise ValueError("H1 delivery-work g1 fingerprint differs")
        try:
            g1 = DeliveryWorkStateV1.model_validate_json(self.selected_g1_canonical_bytes)
        except ValueError as error:
            raise ValueError("H1 delivery-work g1 bytes are invalid") from error
        if g1.canonical_bytes() != self.selected_g1_canonical_bytes:
            raise ValueError("H1 delivery-work g1 bytes are noncanonical")
        if (
            g1.generation != 1
            or g1.state != "CLAIMED"
            or g1.record_id != g1.selected_source.subject_id + ":g1"
            or g1.holder_id != self.holder_id
            or g1.session_id != self.session_id
            or g1.trusted_clock_ns != self.claimed_at_ns
            or g1.lease_expires_at_ns != self.lease_expires_at_ns
            or self.claimed_at_ns >= self.lease_expires_at_ns
        ):
            raise ValueError("H1 delivery-work claim fence differs from g1")
        return self


class H1PrepareScopedAuthorizationV1(DispatchObservationDTO):
    """A pure cross-bound request. It is not accepted by legacy V3 issuance."""

    schema_id: Literal["chiplog.effects.h1-prepare-scoped-authorization.v1"] = (
        "chiplog.effects.h1-prepare-scoped-authorization.v1"
    )
    kind: Literal["H1_PREPARE_SCOPED_AUTHORIZATION"] = "H1_PREPARE_SCOPED_AUTHORIZATION"
    tenant_id: Identity
    command_id: Identity
    command_fingerprint: Digest
    current: H1ScopedAuthorizationCurrentV1
    claim_fence: H1DeliveryWorkClaimFenceV1
    request_fingerprint: Digest

    @model_validator(mode="after")
    def exact_cross_binding(self) -> Self:
        current_bytes = self.current.canonical_bytes()
        fence_bytes = self.claim_fence.canonical_bytes()
        current_fingerprint = _digest(_CURRENT_DOMAIN, current_bytes)
        fence_fingerprint = _digest(_FENCE_DOMAIN, fence_bytes)
        preimage = b"".join(
            len(part).to_bytes(8, "big") + part
            for part in (
                self.tenant_id.encode(),
                self.command_id.encode(),
                current_bytes,
                fence_bytes,
            )
        )
        g1 = DeliveryWorkStateV1.model_validate_json(self.claim_fence.selected_g1_canonical_bytes)
        if (
            self.tenant_id != self.current.tenant_id
            or self.tenant_id != g1.selected_source.tenant_id
            or self.current.intent_id != g1.selected_source.intent_id
            or self.command_id != self.current.command_id
            or self.command_id != self.claim_fence.command_id
            or self.command_fingerprint != self.current.command_fingerprint
            or self.command_fingerprint != self.claim_fence.command_fingerprint
            or self.claim_fence.current_fingerprint != current_fingerprint
            or self.claim_fence.broker_epoch != self.current.broker_epoch
            or self.claim_fence.runtime_generation != self.current.runtime_generation
            or self.claim_fence.clock_epoch != self.current.clock_epoch
            or self.claim_fence.claimed_at_ns > self.current.observed_at_ns
            or self.current.observed_at_ns >= self.current.lease_expires_at_ns
            or self.current.lease_expires_at_ns > self.claim_fence.lease_expires_at_ns
            or self.request_fingerprint
            != _digest(_REQUEST_DOMAIN, preimage + fence_fingerprint.encode())
        ):
            raise ValueError("H1 authorization request cross-binding differs")
        return self


def decode_h1_prepare_scoped_authorization_v1(raw: bytes) -> H1PrepareScopedAuthorizationV1:
    """Strictly decode this canonical, inert H1 request wire only."""

    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON member")
            result[key] = value
        return result

    try:
        parsed = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
    except (AttributeError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("H1 authorization request is not strict UTF-8 JSON") from error
    if (
        not isinstance(parsed, dict)
        or json.dumps(parsed, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
        != raw
    ):
        raise ValueError("H1 authorization request is noncanonical")
    try:
        value = H1PrepareScopedAuthorizationV1.model_validate_json(raw)
    except ValueError as error:
        raise ValueError("H1 authorization request is invalid") from error
    if value.canonical_bytes() != raw:
        raise ValueError("H1 authorization request differs from canonical DTO")
    return value


__all__ = [
    "H1DeliveryWorkClaimFenceV1",
    "H1PrepareScopedAuthorizationV1",
    "H1ScopedAuthorizationCurrentV1",
    "decode_h1_prepare_scoped_authorization_v1",
]
