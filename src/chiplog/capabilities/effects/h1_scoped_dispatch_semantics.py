"""Immutable registry for the implemented H1 scoped pre-send preparation.

The registry covers only construction of the authorization and ordinal-zero
records.  It neither authorizes provider I/O nor defines the later lifecycle
reducer.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Literal

from .contracts import DispatchSemanticBinding, ExactHead
from .dispatch_authority_contracts import CapturedSource, DispatchObservationDTO
from .domain import EffectRuleViolation

H1_SCOPED_DISPATCH_SEMANTICS = DispatchSemanticBinding(
    normative_manifest="chiplog.effects.h1-scoped-dispatch-preparation-manifest.v1",
    reducer_version="chiplog.effects.scoped-dispatch-preparation.v3",
    transition_registry_version="chiplog.effects.h1-scoped-dispatch-preparation-transitions.v1",
    canonicalization_fingerprint_version="chiplog.effects.intent.v3",
    adapter_contract_version="chiplog.effects.scoped-dispatch-preparation-adapter.v1",
)

H1_SCOPED_DISPATCH_SEMANTIC_SOURCE_ID = "chiplog.effects.h1-scoped-dispatch-preparation-manifest"
H1_SCOPED_DISPATCH_SEMANTIC_SOURCE_VERSION = "v1"
H1_SCOPED_DISPATCH_SEMANTIC_OWNER_ID = "effects"
H1_SCOPED_DISPATCH_SEMANTIC_READER_ID = "chiplog.effects.h1-scoped-dispatch-semantic-registry.v1"

PREPARE_SCOPED_AUTHORIZATION_V3 = "effects.prepare_scoped_authorization_v3"
PREPARE_SCOPED_FIRST_SEND_V3 = "effects.prepare_scoped_first_send_v3"
PREPARE_H1_SCOPED_DELIVERY_V2 = "effects.prepare_h1_scoped_delivery_v2"

H1_SCOPED_DISPATCH_V2_SEMANTICS = DispatchSemanticBinding(
    normative_manifest="chiplog.effects.h1-scoped-dispatch-preparation-manifest.v2",
    reducer_version="chiplog.effects.scoped-dispatch-preparation.v3",
    transition_registry_version="chiplog.effects.h1-scoped-dispatch-preparation-transitions.v2",
    canonicalization_fingerprint_version="chiplog.effects.intent.v3",
    adapter_contract_version="chiplog.effects.scoped-dispatch-preparation-adapter.v2",
)

_V2_SOURCE_ID = "chiplog.effects.h1-scoped-dispatch-preparation-manifest"
_V2_SOURCE_VERSION = "v2"
_V2_READER_ID = "chiplog.effects.h1-scoped-dispatch-semantic-registry.v2"


class H1ScopedDispatchSemanticManifestV1(DispatchObservationDTO):
    """The closed registry of pure, pre-provider dispatch operations."""

    schema_id: Literal["chiplog.effects.h1-scoped-dispatch-preparation-manifest.v1"] = (
        "chiplog.effects.h1-scoped-dispatch-preparation-manifest.v1"
    )
    semantics: DispatchSemanticBinding = H1_SCOPED_DISPATCH_SEMANTICS
    operations: tuple[
        Literal[
            "effects.prepare_scoped_authorization_v3",
            "effects.prepare_scoped_first_send_v3",
        ],
        ...,
    ] = ("effects.prepare_scoped_authorization_v3", "effects.prepare_scoped_first_send_v3")
    semantic_registry_source_id: Literal[
        "chiplog.effects.h1-scoped-dispatch-preparation-manifest"
    ] = "chiplog.effects.h1-scoped-dispatch-preparation-manifest"
    semantic_registry_source_version: Literal["v1"] = "v1"
    semantic_registry_owner_id: Literal["effects"] = "effects"
    semantic_registry_reader_id: Literal[
        "chiplog.effects.h1-scoped-dispatch-semantic-registry.v1"
    ] = "chiplog.effects.h1-scoped-dispatch-semantic-registry.v1"


H1_SCOPED_DISPATCH_SEMANTIC_MANIFEST = H1ScopedDispatchSemanticManifestV1()


class H1ScopedDispatchSemanticManifestV2(DispatchObservationDTO):
    """V2 registry binds the V2 producer to its pure pre-send successors."""

    schema_id: Literal["chiplog.effects.h1-scoped-dispatch-preparation-manifest.v2"] = (
        "chiplog.effects.h1-scoped-dispatch-preparation-manifest.v2"
    )
    semantics: DispatchSemanticBinding = H1_SCOPED_DISPATCH_V2_SEMANTICS
    operations: tuple[str, ...] = (
        PREPARE_H1_SCOPED_DELIVERY_V2,
        PREPARE_SCOPED_AUTHORIZATION_V3,
        PREPARE_SCOPED_FIRST_SEND_V3,
    )
    producer_request_schema: Literal["chiplog.effects.h1-scoped-delivery-owner-call.v2"] = (
        "chiplog.effects.h1-scoped-delivery-owner-call.v2"
    )
    producer_result_schema: Literal["chiplog.effects.prepared-h1-scoped-delivery.v2"] = (
        "chiplog.effects.prepared-h1-scoped-delivery.v2"
    )
    producer_adapter_route: Literal[
        "chiplog.capabilities.effects._h1_scoped_v2_process:dispatch"
    ] = "chiplog.capabilities.effects._h1_scoped_v2_process:dispatch"
    semantic_registry_source_id: Literal[
        "chiplog.effects.h1-scoped-dispatch-preparation-manifest"
    ] = "chiplog.effects.h1-scoped-dispatch-preparation-manifest"
    semantic_registry_source_version: Literal["v2"] = "v2"
    semantic_registry_owner_id: Literal["effects"] = "effects"
    semantic_registry_reader_id: Literal[
        "chiplog.effects.h1-scoped-dispatch-semantic-registry.v2"
    ] = "chiplog.effects.h1-scoped-dispatch-semantic-registry.v2"


H1_SCOPED_DISPATCH_V2_SEMANTIC_MANIFEST = H1ScopedDispatchSemanticManifestV2()


def h1_scoped_dispatch_semantic_manifest_bytes() -> bytes:
    return H1_SCOPED_DISPATCH_SEMANTIC_MANIFEST.canonical_bytes()


def h1_scoped_dispatch_semantic_manifest_head() -> ExactHead:
    raw = h1_scoped_dispatch_semantic_manifest_bytes()
    fingerprint = hashlib.sha256(raw).hexdigest()
    return ExactHead(
        subject_id=H1_SCOPED_DISPATCH_SEMANTIC_SOURCE_ID,
        head=H1_SCOPED_DISPATCH_SEMANTIC_SOURCE_ID + "/" + fingerprint,
        fingerprint=fingerprint,
    )


def h1_scoped_dispatch_semantic_registry_capture(
    *, clock_contract: str, clock_epoch: str, valid_until_ns: int
) -> CapturedSource:
    head = h1_scoped_dispatch_semantic_manifest_head()
    return CapturedSource(
        source_id=H1_SCOPED_DISPATCH_SEMANTIC_SOURCE_ID,
        source_version=H1_SCOPED_DISPATCH_SEMANTIC_SOURCE_VERSION,
        owner_id=H1_SCOPED_DISPATCH_SEMANTIC_OWNER_ID,
        reader_id=H1_SCOPED_DISPATCH_SEMANTIC_READER_ID,
        invalidation_manifest=head,
        head=head,
        canonical_value=h1_scoped_dispatch_semantic_manifest_bytes(),
        clock_contract=clock_contract,
        clock_epoch=clock_epoch,
        valid_until_ns=valid_until_ns,
    )


def h1_scoped_dispatch_v2_semantic_registry_capture(
    *, clock_contract: str, clock_epoch: str, valid_until_ns: int
) -> CapturedSource:
    raw = H1_SCOPED_DISPATCH_V2_SEMANTIC_MANIFEST.canonical_bytes()
    fingerprint = hashlib.sha256(raw).hexdigest()
    head = ExactHead(
        subject_id=_V2_SOURCE_ID, head=_V2_SOURCE_ID + "/" + fingerprint, fingerprint=fingerprint
    )
    return CapturedSource(
        source_id=_V2_SOURCE_ID,
        source_version=_V2_SOURCE_VERSION,
        owner_id="effects",
        reader_id=_V2_READER_ID,
        invalidation_manifest=head,
        head=head,
        canonical_value=raw,
        clock_contract=clock_contract,
        clock_epoch=clock_epoch,
        valid_until_ns=valid_until_ns,
    )


def require_h1_scoped_dispatch_operation(
    *,
    semantics: DispatchSemanticBinding,
    operation: str,
    original: CapturedSource,
    current: CapturedSource,
    clock_contract: str,
    clock_epoch: str,
    valid_until_ns: int,
    allow_producer: bool = False,
) -> None:
    """Require exact registered semantics for one durable pre-send operation."""
    manifest_type: type[DispatchObservationDTO]
    expected_manifest: DispatchObservationDTO
    capture: Callable[..., CapturedSource]
    if semantics == H1_SCOPED_DISPATCH_SEMANTICS:
        manifest_type = H1ScopedDispatchSemanticManifestV1
        expected_manifest = H1_SCOPED_DISPATCH_SEMANTIC_MANIFEST
        capture = h1_scoped_dispatch_semantic_registry_capture
    elif semantics == H1_SCOPED_DISPATCH_V2_SEMANTICS:
        manifest_type = H1ScopedDispatchSemanticManifestV2
        expected_manifest = H1_SCOPED_DISPATCH_V2_SEMANTIC_MANIFEST
        capture = h1_scoped_dispatch_v2_semantic_registry_capture
    else:
        raise EffectRuleViolation(
            "DISPATCH_VERSION_HOLD", "scoped dispatch semantics are not registered"
        )
    try:
        manifest = manifest_type.model_validate_json(current.canonical_value)
    except ValueError as error:
        raise EffectRuleViolation(
            "DISPATCH_VERSION_HOLD", "scoped dispatch registry is malformed"
        ) from error
    expected_current = capture(
        clock_contract=clock_contract,
        clock_epoch=clock_epoch,
        valid_until_ns=valid_until_ns,
    )
    expected_original = capture(
        clock_contract=original.clock_contract,
        clock_epoch=original.clock_epoch,
        valid_until_ns=original.valid_until_ns,
    )
    if (
        manifest != expected_manifest
        or operation not in manifest.operations
        or (
            semantics == H1_SCOPED_DISPATCH_V2_SEMANTICS
            and not allow_producer
            and operation not in (PREPARE_SCOPED_AUTHORIZATION_V3, PREPARE_SCOPED_FIRST_SEND_V3)
        )
        or (original.clock_contract, original.clock_epoch) != (clock_contract, clock_epoch)
        or original != expected_original
        or current != expected_current
    ):
        raise EffectRuleViolation(
            "DISPATCH_VERSION_HOLD", "scoped dispatch operation is not exactly registered"
        )


__all__ = [
    "H1_SCOPED_DISPATCH_SEMANTICS",
    "H1_SCOPED_DISPATCH_SEMANTIC_MANIFEST",
    "H1_SCOPED_DISPATCH_V2_SEMANTICS",
    "PREPARE_H1_SCOPED_DELIVERY_V2",
    "PREPARE_SCOPED_AUTHORIZATION_V3",
    "PREPARE_SCOPED_FIRST_SEND_V3",
    "H1ScopedDispatchSemanticManifestV1",
    "h1_scoped_dispatch_semantic_manifest_bytes",
    "h1_scoped_dispatch_semantic_manifest_head",
    "h1_scoped_dispatch_semantic_registry_capture",
    "h1_scoped_dispatch_v2_semantic_registry_capture",
    "require_h1_scoped_dispatch_operation",
]
