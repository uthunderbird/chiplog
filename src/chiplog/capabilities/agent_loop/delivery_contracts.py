"""Loop-owned accepted delivery surface; effects owns all transport transitions."""

from typing import Annotated, Literal, Protocol

from pydantic import ConfigDict, Field

from chiplog.capabilities.agent_loop.contracts import Frozen

Identity = Annotated[str, Field(min_length=1)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class DeliveryDTO(Frozen):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, ser_json_bytes="base64", val_json_bytes="base64"
    )


class ExactHead(DeliveryDTO):
    identity: Identity
    head: Identity
    fingerprint: Digest


class ProviderRecipient(DeliveryDTO):
    provider_id: Identity
    account_id: Identity
    recipient_id: Identity
    endpoint: ExactHead
    canonical_address: bytes
    credential_binding: ExactHead


class OriginSelection(DeliveryDTO):
    kind: Literal["ORIGIN_EXACT"] = "ORIGIN_EXACT"
    ingress_binding: ExactHead
    recipient: ProviderRecipient


class ModelSelection(DeliveryDTO):
    kind: Literal["MODEL_SELECTED_EXACT"] = "MODEL_SELECTED_EXACT"
    recipient: ProviderRecipient


EndpointSelection = Annotated[OriginSelection | ModelSelection, Field(discriminator="kind")]


class AcceptedDelivery(DeliveryDTO):
    delivery_id: Identity
    acceptance: ExactHead
    selection: EndpointSelection
    rendered_bytes: bytes
    render_digest: Digest
    manifest_digest: Digest
    visibility: tuple[ExactHead, ...] = Field(min_length=1)
    provenance: tuple[ExactHead, ...] = Field(min_length=1)
    disclosure: tuple[ExactHead, ...] = Field(min_length=1)
    narrowing: tuple[ExactHead, ...]
    policy: ExactHead


class DeliveryManifest(DeliveryDTO):
    manifest_id: Identity
    run_id: Identity
    turn_id: Identity
    complete_acceptance: ExactHead
    authenticated_worker_fence: ExactHead
    ordered_deliveries: tuple[AcceptedDelivery, ...] = Field(min_length=1)
    canonicalization_version: Literal["chiplog.delivery.v1"] = "chiplog.delivery.v1"


class CommittedAssertion(DeliveryDTO):
    assertion_code: Identity
    committed_query: ExactHead
    complete_evidence: tuple[ExactHead, ...] = Field(min_length=1)
    closure: ExactHead
    renderer_version: Identity
    rendered_bytes: bytes
    rendered_digest: Digest


class DeliveryPublished(DeliveryDTO):
    """Effects acknowledgement, never a claim of provider or human receipt."""

    disposition: Literal["PUBLISHED", "REPLAY"]
    publication_head: ExactHead


class PreparedDeliveryPublication(DeliveryDTO):
    """Effects-owned inert members for the same atomic CompleteAcceptance batch."""

    disposition: Literal["PREPARED"] = "PREPARED"
    preparation_identity: Identity
    complete_acceptance: ExactHead
    ordered_effect_record_bytes: tuple[bytes, ...] = Field(min_length=1)
    complete_members_digest: Digest


class DeliveryPublicationHeld(DeliveryDTO):
    disposition: Literal["HOLD", "CONFLICT"]
    reason: Identity


DeliveryPublicationResult = Annotated[
    DeliveryPublished | DeliveryPublicationHeld, Field(discriminator="disposition")
]


class DeliveryPublicationPort(Protocol):
    """Owner-local outbound preparation; broker commits the combined batch once."""

    async def prepare_deliveries(
        self, manifest: DeliveryManifest
    ) -> PreparedDeliveryPublication | DeliveryPublicationHeld: ...
