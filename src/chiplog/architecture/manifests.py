"""Serializable, inert declarations for architecture-boundary verification."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from hashlib import sha256
from typing import Any, Literal

Visibility = Literal["public", "private"]
PackageRole = Literal[
    "domain_primitives", "capability", "workflow", "adapter", "platform", "composition"
]
ImportMode = Literal["static", "dynamic", "entry_point", "reflection", "service_locator"]
RouteKind = Literal["forward", "reverse", "callback"]


def signature_digest(export: str, fields: tuple[tuple[str, str], ...]) -> str:
    """Derive the frozen R1 signature digest without importing R1."""

    payload = json.dumps(
        {"export": export, "fields": fields},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return sha256(payload).hexdigest()


_R1_FIELDS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("TenantId", (("value", "str"),)),
    ("PrincipalId", (("value", "str"),)),
    ("PermissionScope", (("value", "str"),)),
    ("RecordId", (("tenant_id", "TenantId"), ("value", "str"))),
    ("RecordTypeId", (("namespace", "str"), ("name", "str"))),
    ("SchemaId", (("namespace", "str"), ("name", "str"), ("version", "int"))),
    ("CodecVersion", (("value", "int"),)),
    ("CanonicalizationVersion", (("value", "int"),)),
    ("OwnerTag", (("value", "str"),)),
    ("Fingerprint", (("algorithm", "str"), ("digest", "bytes"))),
    (
        "ProducingVersions",
        (
            ("schema_id", "SchemaId"),
            ("codec", "CodecVersion"),
            ("canonicalization", "CanonicalizationVersion"),
        ),
    ),
    (
        "CanonicalBytes",
        (("payload", "bytes"), ("producing_versions", "ProducingVersions")),
    ),
    (
        "PreservedBytes",
        (("original", "bytes"), ("producing_versions", "ProducingVersions")),
    ),
)

R1_SIGNATURES: tuple[tuple[str, str], ...] = tuple(
    sorted(
        (f"chiplog.domain_primitives:{name}", signature_digest(name, fields))
        for name, fields in _R1_FIELDS
    )
)


@dataclass(frozen=True)
class PackageDecl:
    name: str
    role: PackageRole
    owner: str


@dataclass(frozen=True)
class ExportDecl:
    reference: str
    package: str
    visibility: Visibility
    signature_digest: str
    capability: str


@dataclass(frozen=True)
class CapabilityDecl:
    name: str
    package: str


@dataclass(frozen=True)
class RecordOwnership:
    record_type: str
    owner: str
    commit_boundary: str


@dataclass(frozen=True)
class BridgeBinding:
    name: str
    consumer: str
    provider: str
    implementation: str
    consumer_port: str
    provider_port: str


@dataclass(frozen=True)
class ProviderBinding:
    port: str
    owner: str
    implementation: str
    fallback: str | None = None


@dataclass(frozen=True)
class SurfaceDecl:
    name: str
    owner: str
    executable: bool
    generic_authoritative_write: bool = False


@dataclass(frozen=True)
class ExecutableRoot:
    executable: str
    assembly: str
    bootstrap: str
    resource_order: tuple[str, ...]
    teardown_order: tuple[str, ...]
    readiness_after_start: bool
    admission_bypass: bool = False


@dataclass(frozen=True)
class ImportEdge:
    name: str
    importer: str
    imported: str
    symbol: str
    mode: ImportMode = "static"


@dataclass(frozen=True)
class RouteEdge:
    name: str
    consumer: str
    provider: str
    bridge: str
    kind: RouteKind
    synchronous: bool = True


@dataclass(frozen=True)
class ArchitectureManifest:
    generation: str
    schema_version: int
    evidentiary: bool
    r1_signatures: tuple[tuple[str, str], ...]
    packages: tuple[PackageDecl, ...]
    exports: tuple[ExportDecl, ...]
    capabilities: tuple[CapabilityDecl, ...]
    record_ownership: tuple[RecordOwnership, ...]
    bridges: tuple[BridgeBinding, ...]
    providers: tuple[ProviderBinding, ...]
    surfaces: tuple[SurfaceDecl, ...]
    executable_roots: tuple[ExecutableRoot, ...]
    imports: tuple[ImportEdge, ...]
    routes: tuple[RouteEdge, ...]

    def canonical_bytes(self) -> bytes:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":")).encode()

    def fingerprint(self) -> str:
        return sha256(self.canonical_bytes()).hexdigest()


EMPTY_PRODUCTION_GENERATION = ArchitectureManifest(
    generation="production-stage0-not-materialized-v1",
    schema_version=1,
    evidentiary=False,
    r1_signatures=R1_SIGNATURES,
    packages=(),
    exports=(),
    capabilities=(),
    record_ownership=(),
    bridges=(),
    providers=(),
    surfaces=(),
    executable_roots=(),
    imports=(),
    routes=(),
)


def manifest_mapping(manifest: ArchitectureManifest) -> dict[str, Any]:
    """Return the inert JSON-compatible representation used by external tooling."""

    return asdict(manifest)
