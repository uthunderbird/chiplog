"""Closed architectural contracts; no production graph is constructed here."""

from .manifests import (
    EMPTY_PRODUCTION_GENERATION,
    R1_SIGNATURES,
    ArchitectureManifest,
    BridgeBinding,
    CapabilityDecl,
    ExecutableRoot,
    ExportDecl,
    ImportEdge,
    PackageDecl,
    ProviderBinding,
    RecordOwnership,
    RouteEdge,
    SurfaceDecl,
    signature_digest,
)
from .verifier import ArchitectureViolation, verify_manifest

__all__ = (
    "EMPTY_PRODUCTION_GENERATION",
    "R1_SIGNATURES",
    "ArchitectureManifest",
    "ArchitectureViolation",
    "BridgeBinding",
    "CapabilityDecl",
    "ExecutableRoot",
    "ExportDecl",
    "ImportEdge",
    "PackageDecl",
    "ProviderBinding",
    "RecordOwnership",
    "RouteEdge",
    "SurfaceDecl",
    "signature_digest",
    "verify_manifest",
)
