"""Immutable semantics for the H1 prepared-delivery producer.

This describes the Effects-owned pure preparation operation only.  It does
not describe a broker reader or authorize dispatch, commitment, or SEND.
"""

from __future__ import annotations

import hashlib
from typing import Literal

from .contracts import DispatchSemanticBinding, ExactHead
from .dispatch_authority_contracts import CapturedSource, DispatchObservationDTO

H1_PRODUCER_SEMANTICS = DispatchSemanticBinding(
    normative_manifest="chiplog.effects.h1-prepared-delivery-producer-manifest.v1",
    reducer_version="chiplog.effects.h1-scoped-preparation.v1",
    transition_registry_version="chiplog.effects.h1-prepared-delivery-producer-transitions.v1",
    canonicalization_fingerprint_version="chiplog.effects.intent.v3",
    adapter_contract_version="chiplog.effects.h1-scoped-producer-adapter.v1",
)

H1_PRODUCER_SEMANTIC_SOURCE_ID = "chiplog.effects.h1-prepared-delivery-producer-manifest"
H1_PRODUCER_SEMANTIC_SOURCE_VERSION = "v1"
H1_PRODUCER_SEMANTIC_OWNER_ID = "effects"
H1_PRODUCER_SEMANTIC_READER_ID = "chiplog.effects.h1-scoped-producer-semantic-registry.v1"


class H1ProducerSemanticManifestV1(DispatchObservationDTO):
    """The closed producer contract retained by the semantic registry capture."""

    schema_id: Literal["chiplog.effects.h1-prepared-delivery-producer-manifest.v1"] = (
        "chiplog.effects.h1-prepared-delivery-producer-manifest.v1"
    )
    semantics: DispatchSemanticBinding = H1_PRODUCER_SEMANTICS
    operation: Literal["effects.prepare_h1_scoped_delivery"] = "effects.prepare_h1_scoped_delivery"
    request_schema: Literal["chiplog.effects.h1-scoped-delivery-owner-call.v1"] = (
        "chiplog.effects.h1-scoped-delivery-owner-call.v1"
    )
    result_schema: Literal["chiplog.effects.prepared-h1-scoped-delivery.v1"] = (
        "chiplog.effects.prepared-h1-scoped-delivery.v1"
    )
    adapter_route: Literal["chiplog.capabilities.effects._h1_scoped_process:dispatch"] = (
        "chiplog.capabilities.effects._h1_scoped_process:dispatch"
    )
    adapter_mount: Literal["effects.prepare_h1_scoped_delivery"] = (
        "effects.prepare_h1_scoped_delivery"
    )
    semantic_registry_source_id: Literal[
        "chiplog.effects.h1-prepared-delivery-producer-manifest"
    ] = "chiplog.effects.h1-prepared-delivery-producer-manifest"
    semantic_registry_source_version: Literal["v1"] = "v1"
    semantic_registry_owner_id: Literal["effects"] = "effects"
    semantic_registry_reader_id: Literal[
        "chiplog.effects.h1-scoped-producer-semantic-registry.v1"
    ] = "chiplog.effects.h1-scoped-producer-semantic-registry.v1"
    producer_transitions: tuple[
        Literal[
            "VALIDATE_CANONICAL_OWNER_CALL",
            "VALIDATE_PREPARED_DELIVERY_INPUTS",
            "VALIDATE_H1_HISTORY_GENERATION",
            "DERIVE_SCOPED_INTENT",
            "RETURN_PREPARED_H1_SCOPED_DELIVERY",
            "RETURN_DENIED",
        ],
        ...,
    ] = (
        "VALIDATE_CANONICAL_OWNER_CALL",
        "VALIDATE_PREPARED_DELIVERY_INPUTS",
        "VALIDATE_H1_HISTORY_GENERATION",
        "DERIVE_SCOPED_INTENT",
        "RETURN_PREPARED_H1_SCOPED_DELIVERY",
        "RETURN_DENIED",
    )
    send_transitions: tuple[()] = ()


H1_PRODUCER_SEMANTIC_MANIFEST = H1ProducerSemanticManifestV1()


def h1_producer_semantic_manifest_bytes() -> bytes:
    return H1_PRODUCER_SEMANTIC_MANIFEST.canonical_bytes()


def h1_producer_semantic_manifest_head() -> ExactHead:
    raw = h1_producer_semantic_manifest_bytes()
    fingerprint = hashlib.sha256(raw).hexdigest()
    return ExactHead(
        subject_id=H1_PRODUCER_SEMANTIC_SOURCE_ID,
        head=H1_PRODUCER_SEMANTIC_SOURCE_ID + "/" + fingerprint,
        fingerprint=fingerprint,
    )


def h1_producer_semantic_registry_capture(
    *, clock_contract: str, clock_epoch: str, valid_until_ns: int
) -> CapturedSource:
    """Return the declared semantic-registry capture for one H1 observation.

    The capture binds immutable Effects bytes to the caller's already-selected
    observation horizon.  It does not attest that a physical broker reader ran.
    """
    head = h1_producer_semantic_manifest_head()
    return CapturedSource(
        source_id=H1_PRODUCER_SEMANTIC_SOURCE_ID,
        source_version=H1_PRODUCER_SEMANTIC_SOURCE_VERSION,
        owner_id=H1_PRODUCER_SEMANTIC_OWNER_ID,
        reader_id=H1_PRODUCER_SEMANTIC_READER_ID,
        invalidation_manifest=head,
        head=head,
        canonical_value=h1_producer_semantic_manifest_bytes(),
        clock_contract=clock_contract,
        clock_epoch=clock_epoch,
        valid_until_ns=valid_until_ns,
    )


def require_h1_producer_semantics(
    *,
    supported_semantics: DispatchSemanticBinding | None,
    original: CapturedSource,
    current: CapturedSource,
    clock_contract: str,
    clock_epoch: str,
    valid_until_ns: int,
) -> None:
    """Require the exact H1 producer semantics and its two equal captures."""
    if supported_semantics != H1_PRODUCER_SEMANTICS:
        raise ValueError("H1 scoped delivery needs registered producer semantics")
    expected = h1_producer_semantic_registry_capture(
        clock_contract=clock_contract,
        clock_epoch=clock_epoch,
        valid_until_ns=valid_until_ns,
    )
    for label, source in (("original", original), ("current", current)):
        if hashlib.sha256(source.canonical_value).hexdigest() != source.head.fingerprint:
            raise ValueError("H1 " + label + " semantic registry head fingerprint differs")
        if source != expected:
            raise ValueError("H1 " + label + " semantic registry differs from manifest")
    if original != current:
        raise ValueError("H1 original and current semantic registries differ")


__all__ = [
    "H1_PRODUCER_SEMANTICS",
    "H1_PRODUCER_SEMANTIC_MANIFEST",
    "H1ProducerSemanticManifestV1",
    "h1_producer_semantic_manifest_bytes",
    "h1_producer_semantic_manifest_head",
    "h1_producer_semantic_registry_capture",
    "require_h1_producer_semantics",
]
