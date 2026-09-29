"""Candidate-only H1 producer sources for a prepared external delivery.

These DTOs retain a closed source inventory and a fresh physical observation
candidate.  They do not authenticate a reader result, prove currentness, or
authorize SEND; the broker reader owns those checks.
"""

from __future__ import annotations

import hashlib
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from chiplog.capabilities.deployment_trust.prepared_external_delivery_contracts import (
    PreparedExternalDeliveryGrantAnchorV2,
)
from chiplog.capabilities.deployment_trust.prepared_external_delivery_policy_contracts import (
    PreparedExternalSelfDeliveryPolicyAnchorV1,
)

from .contracts import DispatchSemanticBinding, ExactHead
from .dispatch_authority_contracts import CapturedSource, Digest, DispatchObservationDTO, Identity


class H1PreparedDeliveryMandateCandidateV1(DispatchObservationDTO):
    """Exact candidate bytes bound to one H1 PREPARED_DELIVERY proof.

    ``prepared_delivery_basis_bytes`` is deliberately retained as bytes here.
    The pure effects owner decodes and compares it with the basis it derives
    from the loop, keeping this source DTO independent of the V3 intent module.
    """

    schema_id: Literal["chiplog.effects.h1-prepared-delivery-mandate-candidate.v1"] = (
        "chiplog.effects.h1-prepared-delivery-mandate-candidate.v1"
    )
    prepared_delivery_basis_bytes: bytes = Field(min_length=1)
    grant_anchor: PreparedExternalDeliveryGrantAnchorV2
    policy_anchor: PreparedExternalSelfDeliveryPolicyAnchorV1
    canonical_mandate_bytes: bytes = Field(min_length=1)
    mandate_fingerprint: Digest

    @model_validator(mode="after")
    def exact_mandate_fingerprint(self) -> Self:
        if hashlib.sha256(self.canonical_mandate_bytes).hexdigest() != self.mandate_fingerprint:
            raise ValueError("H1 mandate candidate fingerprint differs from bytes")
        return self


class H1ProducerNotApplicableV1(DispatchObservationDTO):
    """Narrow H1 exception for sources superseded by prepared delivery proof."""

    schema_id: Literal["chiplog.effects.h1-producer-not-applicable.v1"] = (
        "chiplog.effects.h1-producer-not-applicable.v1"
    )
    kind: Literal["H1_PREPARED_DELIVERY_NOT_APPLICABLE"] = (
        "H1_PREPARED_DELIVERY_NOT_APPLICABLE"
    )
    role: Literal["planning", "original_adoption"]
    reason: Literal["PREPARED_DELIVERY_PROOF"] = "PREPARED_DELIVERY_PROOF"
    candidate: H1PreparedDeliveryMandateCandidateV1


H1ProducerSourceObservationV1 = Annotated[
    CapturedSource | H1ProducerNotApplicableV1,
    Field(discriminator="kind"),
]


class H1ProducerSourceInventoryV1(DispatchObservationDTO):
    """The exact eleven H1 producer source roles in stable retention order."""

    schema_id: Literal["chiplog.effects.h1-producer-source-inventory.v1"] = (
        "chiplog.effects.h1-producer-source-inventory.v1"
    )
    trust: CapturedSource
    planning: H1ProducerSourceObservationV1
    semantic_registry: CapturedSource
    original_adoption: H1ProducerSourceObservationV1
    runtime_and_fence: CapturedSource
    endpoint: CapturedSource
    credential_lifecycle: CapturedSource
    deployment_entitlement: CapturedSource
    clock: CapturedSource
    effects_history: CapturedSource
    normative_conflict_generation: CapturedSource

    @model_validator(mode="after")
    def exceptions_match_their_roles(self) -> Self:
        for role in ("planning", "original_adoption"):
            source = getattr(self, role)
            if isinstance(source, H1ProducerNotApplicableV1) and source.role != role:
                raise ValueError("H1 not-applicable source role differs from inventory role")
        return self


class H1ProducerCurrentInputsV1(DispatchObservationDTO):
    """Fresh H1 observation candidate, pending broker-reader authentication.

    The physical cut and history head are independent observations.  No field
    in this DTO attests that they are authentic, complete, or current.
    """

    schema_id: Literal["chiplog.effects.h1-producer-current-inputs.v1"] = (
        "chiplog.effects.h1-producer-current-inputs.v1"
    )
    command_fingerprint: Digest
    immutable_mandate_candidate: H1PreparedDeliveryMandateCandidateV1
    physical_cut: ExactHead
    history_observation: ExactHead
    sources: H1ProducerSourceInventoryV1
    supported_semantics: DispatchSemanticBinding | None
    clock_contract: Identity
    clock_epoch: Identity
    observed_time_ns: int = Field(ge=0)
    lease_expires_at_ns: int = Field(gt=0)

    @model_validator(mode="after")
    def unexpired_observation_lease(self) -> Self:
        if self.observed_time_ns >= self.lease_expires_at_ns:
            raise ValueError("H1 current observation lease has expired")
        return self


def require_h1_prepared_delivery_candidate(
    candidate: H1PreparedDeliveryMandateCandidateV1,
    *,
    basis_bytes: bytes,
    grant_anchor: PreparedExternalDeliveryGrantAnchorV2,
    policy_anchor: PreparedExternalSelfDeliveryPolicyAnchorV1,
    mandate_bytes: bytes,
) -> None:
    """Require a candidate to bind exactly to the owner-derived H1 inputs."""
    if (
        candidate.prepared_delivery_basis_bytes != basis_bytes
        or candidate.grant_anchor != grant_anchor
        or candidate.policy_anchor != policy_anchor
        or candidate.canonical_mandate_bytes != mandate_bytes
    ):
        raise ValueError("H1 prepared-delivery candidate differs from owner-derived inputs")


def require_h1_prepared_delivery_sources(
    inventory: H1ProducerSourceInventoryV1,
    *,
    basis_bytes: bytes,
    grant_anchor: PreparedExternalDeliveryGrantAnchorV2,
    policy_anchor: PreparedExternalSelfDeliveryPolicyAnchorV1,
    mandate_bytes: bytes,
) -> None:
    """Require the two narrow exceptions and bind both to the same proof."""
    for role in ("planning", "original_adoption"):
        source = getattr(inventory, role)
        if not isinstance(source, H1ProducerNotApplicableV1):
            raise ValueError("H1 prepared delivery requires proof-bound not-applicable source")
        require_h1_prepared_delivery_candidate(
            source.candidate,
            basis_bytes=basis_bytes,
            grant_anchor=grant_anchor,
            policy_anchor=policy_anchor,
            mandate_bytes=mandate_bytes,
        )


__all__ = [
    "H1PreparedDeliveryMandateCandidateV1",
    "H1ProducerCurrentInputsV1",
    "H1ProducerNotApplicableV1",
    "H1ProducerSourceInventoryV1",
    "H1ProducerSourceObservationV1",
    "require_h1_prepared_delivery_candidate",
    "require_h1_prepared_delivery_sources",
]
