from __future__ import annotations

from dataclasses import dataclass

from .tenant import TenantId


@dataclass(frozen=True)
class RecordId:
    tenant_id: TenantId
    value: str


@dataclass(frozen=True)
class RecordTypeId:
    namespace: str
    name: str


@dataclass(frozen=True)
class SchemaId:
    namespace: str
    name: str
    version: int
