"""Structural contracts for canonical R7 graph assembly."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class OwnerGeneration:
    owner_id: str
    process_identity: str
    generation_id: str
    session_id: str
    capability_ids: tuple[str, ...]
    provider_ids: tuple[str, ...]
    target_ids: tuple[str, ...]
    factory_ids: tuple[str, ...]
    scopes: tuple[str, ...]


@dataclass(frozen=True)
class RuntimeGraphGeneration:
    tenant_id: str
    generation_id: str
    manifest_digest: str
    broker_epoch: int
    owners: tuple[OwnerGeneration, ...]
    routes: tuple[tuple[str, str, str, str, str], ...]
    leaves: tuple[tuple[str, str, str], ...]
    broker_capabilities: tuple[str, ...]
    application_loop_id: str


class R7GraphBuilder(Protocol):
    def build(self, tenant_id: str, manifest_bytes: bytes) -> RuntimeGraphGeneration: ...


__all__ = ["OwnerGeneration", "R7GraphBuilder", "RuntimeGraphGeneration"]
