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
from .r7_compatibility import (
    R7_COMPATIBILITY_LEDGER,
    R7_PARITY_CORPUS,
    CompatibilityEntry,
    ParityCase,
    verify_r7_compatibility_ledger,
)
from .verifier import ArchitectureViolation, verify_manifest

__all__ = (
    "EMPTY_PRODUCTION_GENERATION",
    "R1_SIGNATURES",
    "R7_COMPATIBILITY_LEDGER",
    "R7_PARITY_CORPUS",
    "ArchitectureManifest",
    "ArchitectureViolation",
    "BridgeBinding",
    "CapabilityDecl",
    "CompatibilityEntry",
    "ExecutableRoot",
    "ExportDecl",
    "ImportEdge",
    "PackageDecl",
    "ParityCase",
    "ProviderBinding",
    "RecordOwnership",
    "RouteEdge",
    "SurfaceDecl",
    "signature_digest",
    "verify_manifest",
    "verify_r7_compatibility_ledger",
)
