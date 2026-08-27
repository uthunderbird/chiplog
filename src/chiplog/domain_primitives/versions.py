from __future__ import annotations

from dataclasses import dataclass

from .identity import SchemaId


@dataclass(frozen=True)
class CodecVersion:
    value: int


@dataclass(frozen=True)
class CanonicalizationVersion:
    value: int


@dataclass(frozen=True)
class OwnerTag:
    value: str


@dataclass(frozen=True)
class Fingerprint:
    algorithm: str
    digest: bytes


@dataclass(frozen=True)
class ProducingVersions:
    schema_id: SchemaId
    codec: CodecVersion
    canonicalization: CanonicalizationVersion
